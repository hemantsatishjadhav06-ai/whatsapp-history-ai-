"""Bound public work before decoding, database access or external verification.

Redis counters are shared by replicas. Admission is deliberately per process and
must be budgeted with the deployment's process count, database and edge limits.
No raw IP, bearer, browser cookie, request body or credential is stored in counters.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import time
from collections import OrderedDict
from http.cookies import SimpleCookie

from redis.asyncio import Redis
from starlette.responses import JSONResponse


def canonical_path(path):
    return path[3:] if path.startswith("/v1/") else path


def has_excess_json_depth(body, maximum):
    depth, quoted, escaped = 0, False, False
    for char in body:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > maximum:
                return True
        elif char in (93, 125):
            depth -= 1
    return False


class RequestRateLimiter:
    SCRIPT = """
for index, key in ipairs(KEYS) do
  if tonumber(redis.call('GET', key) or '0') >= tonumber(ARGV[index]) then return 0 end
end
for _, key in ipairs(KEYS) do
  if redis.call('INCR', key) == 1 then redis.call('PEXPIRE', key, ARGV[#KEYS + 1]) end
end
return 1
"""

    def __init__(self, settings):
        self.settings = settings
        self.memory = OrderedDict()
        self.redis = (Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1,
                                    max_connections=32, health_check_interval=30)
                      if settings.request_limits_mode == "redis" else None)

    async def close(self):
        if self.redis is not None:
            await self.redis.aclose()

    async def ready(self):
        if self.redis is not None:
            await self.redis.ping()

    def source(self, scope, headers):
        remote = str((scope.get("client") or ("unknown", 0))[0])
        key = self.settings.trusted_proxy_key
        source = headers.get(b"x-milo-rate-source", b"").decode("ascii", errors="ignore")
        stamp = headers.get(b"x-milo-rate-timestamp", b"").decode("ascii", errors="ignore")
        signature = headers.get(b"x-milo-rate-signature", b"").decode("ascii", errors="ignore")
        if key and len(source) <= 64 and len(stamp) <= 12 and len(signature) == 64:
            try:
                valid_ip = str(ipaddress.ip_address(source))
                expected = hmac.new(key.encode(), f"{stamp}.{source}".encode(), hashlib.sha256).hexdigest()
                if abs(time.time() - int(stamp)) <= 30 and hmac.compare_digest(signature, expected):
                    remote = valid_ip
            except (ValueError, OverflowError):
                pass
        return remote

    @staticmethod
    def session_credential(headers):
        token = headers.get(b"authorization", b"")
        if not token:
            cookie = SimpleCookie()
            try:
                cookie.load(headers.get(b"cookie", b"").decode("latin-1"))
                value = cookie.get("session_token")
                token = value.value.encode() if value else b""
            except Exception:
                token = b""
        return token

    def presents_credential(self, headers):
        return bool(self.session_credential(headers))

    def actor(self, scope, headers, auth):
        # Authentication routes are limited by a trusted source, regardless of
        # arbitrary new cookie/bearer values. Other routes additionally bound
        # each presented session; current authentication is still enforced later.
        if not auth:
            token = self.session_credential(headers)
            if token and len(token) <= 256:
                return hashlib.sha256(b"session:" + token).hexdigest()
        return hashlib.sha256(("source:" + self.source(scope, headers)).encode()).hexdigest()

    async def allow(self, scope, headers, control=False):
        settings = self.settings
        if settings.request_limits_mode == "off":
            return True
        path = canonical_path(scope["path"])
        if control:
            group, actor_limit = "control", settings.request_rate_control
        elif path in {"/auth/nonce", "/auth/native/nonce", "/auth/native/google/start"}:
            group, actor_limit = "issue", settings.request_rate_auth_issues
        elif path in {"/auth/google", "/auth/native/login", "/auth/native/refresh",
                      "/auth/native/google/callback", "/auth/native/google/exchange"}:
            group, actor_limit = "exchange", settings.request_rate_auth_exchanges
        else:
            group, actor_limit = "api", settings.request_rate_api
        window = int(time.time() // settings.request_rate_window_seconds)
        global_limit = (settings.request_rate_global_api if group == "api" else
                        settings.request_rate_global_control if group == "control" else settings.request_rate_global_auth)
        source_limit = (settings.request_rate_source_api if group == "api" else
                        settings.request_rate_source_control if group == "control" else actor_limit)
        prefix = f"{settings.request_rate_namespace}:{group}:{window}"
        source_hash = hashlib.sha256(("source:" + self.source(scope, headers)).encode()).hexdigest()
        keys = [prefix + ":global", prefix + ":actor:" + self.actor(scope, headers, group in {"issue", "exchange"}),
                prefix + ":source:" + source_hash]
        limits = [global_limit, actor_limit, source_limit]
        ttl = settings.request_rate_window_seconds + 1
        if self.redis is not None:
            return bool(await self.redis.eval(self.SCRIPT, len(keys), *keys, *limits, ttl * 1000))
        current = time.monotonic()
        # Bounded memory is for local development only, never production replicas.
        while self.memory:
            key = next(iter(self.memory))
            _, expires = self.memory[key]
            if expires <= current:
                del self.memory[key]
            else:
                break
        if len(self.memory) + sum(key not in self.memory for key in keys) > settings.request_rate_memory_keys:
            raise RuntimeError("Rate-limit capacity unavailable")
        counts = [self.memory.get(key, (0, current + ttl))[0] for key in keys]
        if any(count >= limit for count, limit in zip(counts, limits)):
            return False
        for key, count in zip(keys, counts):
            expires = self.memory.get(key, (0, current + ttl))[1]
            self.memory[key] = (count + 1, expires)
        return True


class RequestSecurityMiddleware:
    def __init__(self, app, settings, limiter):
        self.app, self.settings, self.limiter = app, settings, limiter
        self.active, self.ordinary = 0, 0

    async def reject(self, scope, receive, send, status, detail, retry=None):
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if retry is not None:
            headers["Retry-After"] = str(retry)
        await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = canonical_path(scope["path"])
        headers = dict(scope["headers"])
        if sum(len(name) + len(value) for name, value in scope["headers"]) > 65536:
            await self.reject(scope, receive, send, 431, "Request headers too large")
            return
        try:
            declared_header = headers.get(b"content-length", b"0")
            if not declared_header.isdigit() or len(declared_header) > 20:
                raise ValueError()
            declared = int(declared_header)
        except ValueError:
            await self.reject(scope, receive, send, 400, "Invalid Content-Length")
            return
        # Liveness avoids dependency/rate admission, while malformed HTTP
        # framing still receives the same client error as ordinary routes.
        if path == "/health/live":
            await self.app(scope, receive, send)
            return
        control = (scope["method"] not in {"GET", "HEAD", "OPTIONS"} and (
            path in {"/pause-all", "/resume-all", "/auth/logout", "/auth/native/logout", "/auth/native/revoke"}
            or path.startswith("/auth/sessions/")
            or (scope["method"] == "DELETE" and path.startswith("/memories/"))
            or (path.startswith("/conversations/") and path.rsplit("/", 1)[-1] in {"permissions", "resume", "takeover"})))
        if control and path != "/auth/native/revoke" and not self.limiter.presents_credential(headers):
            # Every control route except refresh-secret revocation needs a session.
            # Reject credential-free requests before they can spend the shared
            # control buckets that owners rely on for pause, takeover and logout.
            await self.reject(scope, receive, send, 401, "Authentication required")
            return
        settings = self.settings
        if self.active >= settings.request_max_inflight or (
                not control and self.ordinary >= settings.request_max_inflight - settings.request_control_reserve):
            await self.reject(scope, receive, send, 503, "Request capacity is busy; retry after checking current state", 2)
            return
        self.active += 1
        self.ordinary += int(not control)
        try:
            try:
                allowed = await self.limiter.allow(scope, headers, control)
            except Exception:
                await self.reject(scope, receive, send, 503, "Request limit service is unavailable", 2)
                return
            if not allowed:
                await self.reject(scope, receive, send, 429, "Request rate limit reached", settings.request_rate_window_seconds)
                return
            large = path in {"/imports", "/imports/preview", "/webhooks/whatsapp"}
            cap = settings.max_import_bytes * 6 + 65536 if large else settings.request_default_body_bytes
            if declared > cap:
                await self.reject(scope, receive, send, 413, "Request body too large")
                return
            body = bytearray()
            try:
                async with asyncio.timeout(settings.request_body_timeout_seconds):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > cap:
                            await self.reject(scope, receive, send, 413, "Request body too large")
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await self.reject(scope, receive, send, 408, "Request body read timed out")
                return
            raw = bytes(body)
            content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
            if raw and (content_type == b"application/json" or content_type.endswith(b"+json")):
                if has_excess_json_depth(raw, settings.request_json_max_depth):
                    await self.reject(scope, receive, send, 400, "JSON nesting exceeds the supported limit")
                    return
                try:
                    json.loads(raw)
                except (ValueError, RecursionError, UnicodeError):
                    await self.reject(scope, receive, send, 400, "Invalid JSON body")
                    return
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": raw, "more_body": False}
                return await receive()

            async def safe_send(message):
                if message["type"] == "http.response.start":
                    response_headers = [(name, value) for name, value in message.get("headers", [])
                                        if name.lower() not in {b"cache-control", b"x-content-type-options"}]
                    message = {**message, "headers": response_headers + [(b"cache-control", b"no-store"),
                                                                         (b"x-content-type-options", b"nosniff")]}
                await send(message)

            await self.app(scope, replay, safe_send)
        finally:
            self.active -= 1
            self.ordinary -= int(not control)
