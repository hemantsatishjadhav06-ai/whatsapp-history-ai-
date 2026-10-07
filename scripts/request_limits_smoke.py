"""Benign shared-Redis request-limit smoke with isolated, expiring test keys.

Uses two independent limiter instances, local Redis and a synthetic ASGI sink.
Does not create SQL engines, invoke providers, flush Redis or change other keys.
"""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import socket
import time
from uuid import uuid4

import httpx
from redis.asyncio import Redis
from starlette.responses import JSONResponse

from assistant.config import Settings
from assistant.request_security import RequestRateLimiter, RequestSecurityMiddleware

REDIS_URL = "redis://127.0.0.1:6379/0"


def scope(path, peer="192.0.2.77", method="GET"):
    return {"type": "http", "path": path, "method": method, "client": (peer, 12345)}


async def run():
    prefix = f"milo-limits-smoke-{uuid4().hex}"
    proxy_key = secrets.token_urlsafe(32)
    namespaces, clients = [], []
    inspector = Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1, max_connections=8)

    def settings(case, **overrides):
        namespace = f"{prefix}-{case}"
        namespaces.append(namespace)
        values = {"_env_file": None, "environment": "test", "model_provider": "disabled",
                  "enable_external_sends": False, "request_limits_mode": "redis", "redis_url": REDIS_URL,
                  "request_rate_namespace": namespace, "request_rate_window_seconds": 3600,
                  "request_rate_api": 1000, "request_rate_source_api": 1000,
                  "request_rate_global_api": 1000, "request_rate_auth_issues": 1000,
                  "request_rate_auth_exchanges": 1000, "request_rate_global_auth": 1000,
                  "request_rate_control": 1000, "request_rate_source_control": 1000,
                  "request_rate_global_control": 1000, "trusted_proxy_key": proxy_key}
        values.update(overrides)
        return Settings(**values)

    async def pair(config):
        independent = [RequestRateLimiter(config), RequestRateLimiter(config)]
        clients.extend(independent)
        await asyncio.gather(*(limiter.ready() for limiter in independent))
        return independent

    async def concurrent(independent, requests, expected, control=False):
        window = int(time.time() // 3600)
        outcomes = await asyncio.gather(*(independent[index % 2].allow(item, headers, control)
                                          for index, (item, headers) in enumerate(requests)))
        assert int(time.time() // 3600) == window, "Rate window changed during the smoke"
        assert sum(outcomes) == expected, (sum(outcomes), expected)
        return {"submitted": len(requests), "accepted": sum(outcomes), "rejected": outcomes.count(False)}

    try:
        await inspector.ping()
        source_pair = await pair(settings("source", request_rate_source_api=7))
        source_requests = [(scope("/v1/me" if index % 2 else "/me"),
                            {b"cookie": f"session_token=synthetic-rotating-cookie-{index}".encode(),
                             b"x-forwarded-for": f"198.51.100.{index + 1}".encode()}) for index in range(60)]
        source_result = await concurrent(source_pair, source_requests, 7)

        actor_pair = await pair(settings("actor", request_rate_api=6))
        actor_requests = [(scope("/v1/me" if index % 2 else "/me", f"192.0.2.{index + 1}"),
                           {b"cookie": b"session_token=synthetic-fixed-cookie"}) for index in range(60)]
        actor_result = await concurrent(actor_pair, actor_requests, 6)

        auth_pair = await pair(settings("auth", request_rate_auth_issues=5))
        paths = ["/auth/nonce", "/v1/auth/nonce", "/auth/native/nonce", "/v1/auth/native/nonce"]
        auth_requests = [(scope(paths[index % 4], "2001:db8::44"),
                          {b"authorization": f"Bearer synthetic-rotating-bearer-{index}".encode(),
                           b"x-milo-rate-source": f"198.51.100.{index + 1}".encode()}) for index in range(60)]
        auth_result = await concurrent(auth_pair, auth_requests, 5)

        global_pair = await pair(settings("global", request_rate_global_api=11, request_rate_global_control=4))
        global_requests = [(scope("/v1/me" if index % 2 else "/me", f"192.0.2.{index + 1}"),
                            {b"cookie": f"session_token=synthetic-global-cookie-{index}".encode()})
                           for index in range(60)]
        global_result = await concurrent(global_pair, global_requests, 11)
        controls = [(scope("/v1/pause-all" if index % 2 else "/pause-all", f"192.0.2.{index + 1}", "POST"),
                     {b"cookie": f"session_token=synthetic-control-cookie-{index}".encode()}) for index in range(20)]
        control_result = await concurrent(global_pair, controls, 4, control=True)

        signed = source_pair[0]
        peer_scope, ipv6 = scope("/me", "192.0.2.200"), "2001:0db8:0:0:0:0:0:1"

        def signed_headers(stamp):
            return {b"x-milo-rate-source": ipv6.encode(), b"x-milo-rate-timestamp": str(stamp).encode(),
                    b"x-milo-rate-signature": hmac.new(proxy_key.encode(), f"{stamp}.{ipv6}".encode(),
                                                     hashlib.sha256).hexdigest().encode()}

        assert signed.source(peer_scope, signed_headers(int(time.time()))) == "2001:db8::1"
        assert signed.source(peer_scope, signed_headers(int(time.time()) - 120)) == "192.0.2.200"
        assert signed.source(peer_scope, {b"x-milo-rate-source": ipv6.encode()}) == "192.0.2.200"
        tampered = signed_headers(int(time.time()))
        tampered[b"x-milo-rate-source"] = b"2001:db8::2"
        assert signed.source(peer_scope, tampered) == "192.0.2.200"

        keys = [key async for key in inspector.scan_iter(match=f"{prefix}-*", count=100)]
        assert keys
        key_pattern = re.compile(r"^[A-Za-z0-9_-]+:(?:api|control|issue|exchange):\d+:"
                                 r"(?:global|actor:[0-9a-f]{64}|source:[0-9a-f]{64})$")
        assert all(key_pattern.fullmatch(key.decode()) for key in keys)
        assert all(b"synthetic" not in key and b"192.0.2" not in key and b"2001:db8" not in key for key in keys)
        async with inspector.pipeline(transaction=False) as pipeline:
            for key in keys:
                pipeline.pttl(key)
            ttls = await pipeline.execute()
        assert all(0 < ttl <= 3601000 for ttl in ttls)
        assert all(value.isdigit() for value in await inspector.mget(keys))

        sink_calls = []

        async def sink(incoming_scope, receive, send):
            sink_calls.append(incoming_scope["path"])
            await JSONResponse({"synthetic": True})(incoming_scope, receive, send)

        # A bound, non-listening local socket reserves an unavailable port.
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            bad_settings = settings("outage", redis_url=f"redis://127.0.0.1:{unavailable.getsockname()[1]}/0")
            bad_limiter = RequestRateLimiter(bad_settings)
            clients.append(bad_limiter)
            wrapped = RequestSecurityMiddleware(sink, bad_settings, bad_limiter)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped),
                                         base_url="http://synthetic.invalid", timeout=3) as client:
                unavailable_results = [(await client.get(path)).status_code for path in ("/me", "/v1/me")]
                assert unavailable_results == [503, 503] and not sink_calls
                assert (await client.get("/health/live")).status_code == 200
        return {"mode": "two_independent_limiters_real_local_redis",
                "shared_source_and_alias_budget": source_result, "shared_actor_budget": actor_result,
                "nonce_aliases_ignore_rotating_credentials": auth_result,
                "shared_global_budget": global_result, "independent_control_budget": control_result,
                "signed_ipv6_canonicalized": True, "expired_unsigned_tampered_source_rejected": True,
                "counter_key_count": len(keys), "counter_keys_hashed": True,
                "counter_values_numeric": True, "all_keys_have_bounded_ttl": True,
                "minimum_ttl_ms": min(ttls), "outage_statuses": unavailable_results,
                "outage_blocked_before_sink": True, "liveness_during_outage": True,
                "sql_engines": 0, "external_provider_calls": 0, "capacity_50000_users": "NOT_MEASURED"}
    finally:
        for namespace in namespaces:
            owned_keys = [key async for key in inspector.scan_iter(match=f"{namespace}:*", count=100)]
            if owned_keys:
                await inspector.delete(*owned_keys)
        await asyncio.gather(*(limiter.close() for limiter in clients))
        await inspector.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
