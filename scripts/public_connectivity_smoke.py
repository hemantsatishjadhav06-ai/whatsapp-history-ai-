"""Read-only public ingress checks; never performs a provider login or send.

Checks anonymous configuration and rejection boundaries with empty/fake input.
Response bodies stay bounded and only selected configuration facts are saved.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEFAULT_ORIGIN = "https://web-production-bde60.up.railway.app"
MAX_RESPONSE_BYTES = 64 * 1024
PRIVATE_FIELDS = {
    "access_token", "refresh_token", "id_token", "credential", "client_secret",
    "google_client_secret", "whatsapp_access_token", "whatsapp_app_secret",
    "whatsapp_verify_token", "api_key", "password", "private_key",
    "authorization_url", "handoff", "session_token", "csrf_token",
}


class VerificationError(ValueError):
    """A bounded, sanitized verification failure."""


def validate_origin(value: str, *, allow_loopback: bool = False) -> str:
    if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise VerificationError("Origin must be an HTTP origin without whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"} or port == 0):
            raise ValueError
    except ValueError:
        raise VerificationError("Origin must not contain credentials, a path, query or fragment") from None
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if loopback:
        if not allow_loopback or parsed.scheme not in {"http", "https"}:
            raise VerificationError("Local checks require --allow-loopback")
    elif parsed.scheme != "https":
        raise VerificationError("Public connectivity checks require HTTPS")
    return f"{parsed.scheme}://{parsed.netloc}"


def _private_field_present(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in PRIVATE_FIELDS or _private_field_present(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_private_field_present(item) for item in value)
    return False


def _boolean(value: dict, key: str) -> bool:
    if type(value.get(key)) is not bool:
        raise VerificationError("Public configuration is missing a boolean status")
    return value[key]


def _object(value: dict, key: str) -> dict:
    if not isinstance(value.get(key), dict):
        raise VerificationError("Public configuration is missing an integration object")
    return value[key]


def configuration_facts(value: Any) -> dict:
    """Validate readiness consistency without recording IDs or credentials."""
    if not isinstance(value, dict) or _private_field_present(value):
        raise VerificationError("Public configuration must not expose private credentials")
    backend = _boolean(value, "backend_configured")
    browser = _boolean(value, "google_configured")
    google = _object(value, "google")
    broker = _boolean(google, "native_broker_configured")
    native = _boolean(google, "native_configured")
    platforms = _object(google, "native_platforms")
    ios, android = _boolean(platforms, "ios"), _boolean(platforms, "android")
    client_id = value.get("client_id")
    if client_id is not None and (not isinstance(client_id, str) or len(client_id) > 512):
        raise VerificationError("The public Google client identifier is invalid")
    if (not backend or _boolean(google, "configured") != browser
            or broker != native or ios != broker or android != broker
            or (broker and not browser)
            or bool(value.get("client_id")) != browser
            or google.get("client_id") != value.get("client_id")):
        raise VerificationError("Google readiness flags and public client configuration disagree")
    if google.get("native_flow") != "https_google_broker_with_one_use_S256_handoff":
        raise VerificationError("The native HTTPS Google handoff flow is unavailable")
    if (_boolean(value, "identity_grants_service_access")
            or value.get("browser_authentication") != "http_only_cookie_with_csrf"
            or value.get("native_authentication") != "revocable_bearer_with_google_nonce_and_S256_proof"):
        raise VerificationError("Public authentication boundaries are inconsistent")
    providers = _object(value, "providers")
    personal = _object(providers, "whatsapp_personal")
    business = _object(providers, "whatsapp_business")
    if personal.get("status") not in {"unavailable", "not_configured", "configured", "connected", "planned"}:
        raise VerificationError("Personal WhatsApp readiness is unavailable")
    pairing = _boolean(personal, "pairing_supported")
    if personal.get("status") == "unavailable" and pairing:
        raise VerificationError("An unavailable personal connector cannot claim pairing support")
    if business.get("status") not in {"not_configured", "configured"}:
        raise VerificationError("Business WhatsApp readiness is unavailable")
    webhook = business.get("webhook_configured")
    if webhook is not None and type(webhook) is not bool:
        raise VerificationError("Webhook readiness must be a boolean when published")
    model = _object(value, "model")
    if model.get("status") not in {"disabled", "simulation", "configured"}:
        raise VerificationError("Model readiness is unavailable")
    # Select facts rather than serializing arbitrary upstream objects. Public
    # Google client IDs are compared in memory but are omitted from evidence.
    return {
        "backend_configured": backend,
        "google_browser_configured": browser,
        "google_native_broker_configured": broker,
        "google_native_platforms": {"ios": ios, "android": android},
        "google_native_broker_live_verified": _boolean(google, "native_broker_live_verified"),
        "native_refresh_supported": _boolean(value, "native_refresh_supported"),
        "whatsapp_personal_status": str(personal.get("status", "unknown")),
        "whatsapp_personal_pairing_supported": pairing,
        "whatsapp_business_status": business["status"],
        "whatsapp_business_live_verification_required": _boolean(business, "live_verification_required"),
        "whatsapp_webhook_configured": webhook,
        "external_sends_enabled": _boolean(value, "external_sends_enabled"),
        "model_status": model["status"],
        "model_quality_verified": _boolean(model, "quality_verified"),
        "identity_grants_service_access": False,
    }


def bounded_read(response, maximum: int = MAX_RESPONSE_BYTES) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise VerificationError("Response declared an invalid length") from None
        if length < 0 or length > maximum:
            raise VerificationError("Response exceeded the read limit")
    payload = response.read(maximum + 1)
    if len(payload) > maximum:
        raise VerificationError("Response exceeded the read limit")
    return payload


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, target):
        return None


def probe(opener, origin: str, path: str, *, method: str = "GET", headers: dict | None = None, data: bytes | None = None) -> tuple[int, bytes, dict]:
    request = Request(origin + path, data=data, method=method,
                      headers={"Accept": "application/json", "Accept-Encoding": "identity",
                               "User-Agent": "Milo-read-only-connectivity-smoke/1", **(headers or {})})
    try:
        response = opener.open(request, timeout=30)
    except HTTPError as error:
        response = error
    except (URLError, TimeoutError, OSError):
        raise VerificationError("The public ingress request could not be completed") from None
    with response:
        payload = bounded_read(response)
        safe_headers = {"cookie_issued": bool(response.headers.get_all("Set-Cookie")),
                        "redirect_issued": bool(response.headers.get("Location")),
                        "no_store": "no-store" in response.headers.get("Cache-Control", "").lower()}
        return response.status, payload, safe_headers


def run_checks(origin: str, *, allow_loopback: bool = False) -> dict:
    origin = validate_origin(origin, allow_loopback=allow_loopback)
    opener = build_opener(NoRedirect())
    checks: list[dict] = []
    facts: dict | None = None
    configurations: list[dict] = []

    def check(name: str, path: str, statuses: set[int], *, method="GET", headers=None, data=None, config=False, webhook_query=False):
        nonlocal facts
        row: dict = {"name": name, "method": method, "path": path, "passed": False}
        try:
            status, payload, security = probe(opener, origin, path, method=method, headers=headers, data=data)
            row.update(http_status=status, response_bytes=len(payload), **security)
            if status not in statuses:
                raise VerificationError("The ingress returned an unexpected status")
            if security["cookie_issued"] or security["redirect_issued"] or not security["no_store"]:
                raise VerificationError("Anonymous ingress must not issue a cookie or redirect and must disable caching")
            if config:
                try:
                    parsed = json.loads(payload)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    raise VerificationError("Public configuration is not valid JSON") from None
                observed = configuration_facts(parsed)
                configurations.append(parsed)
                if facts is None:
                    facts = observed
                elif parsed != configurations[0]:
                    raise VerificationError("Browser and native public configurations disagree")
            if webhook_query and status == 503:
                # Missing provider configuration is a truthful rejection. A
                # generic busy/unavailable503 must not pass this security check.
                try:
                    parsed = json.loads(payload)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    parsed = None
                if not isinstance(parsed, dict) or parsed.get("detail") != "WhatsApp verification is not configured":
                    raise VerificationError("Webhook rejection did not confirm missing provider configuration")
                row["observed_webhook_configuration"] = "not_configured"
                if facts and facts["whatsapp_webhook_configured"] is True:
                    raise VerificationError("Published webhook readiness disagrees with verification ingress")
            row["passed"] = True
        except VerificationError as failure:
            row["failure"] = str(failure)
        except RecursionError:
            row["failure"] = "Public configuration is nested beyond the validation limit"
        checks.append(row)

    check("browser_public_configuration", "/api/auth/config", {200}, config=True)
    check("native_public_configuration_matches", "/native-api/v1/auth/config", {200}, config=True)
    check("native_identity_requires_bearer", "/native-api/v1/me", {401})
    check("native_internal_routes_unavailable", "/native-api/v1/internal/dispatch-authority", {404})
    check("native_development_login_unavailable", "/native-api/v1/auth/dev", {404}, method="POST", data=b"{}", headers={"Content-Type": "application/json"})
    check("native_config_rejects_browser_origin", "/native-api/v1/auth/config", {403}, headers={"Origin": origin})
    check("native_config_rejects_ambient_cookie", "/native-api/v1/auth/config", {403}, headers={"Cookie": "session_token=synthetic-invalid-connectivity-cookie"})
    check("native_config_rejects_browser_metadata", "/native-api/v1/auth/config", {403}, headers={"Sec-Fetch-Site": "same-origin"})
    check("unsigned_whatsapp_webhook_rejected", "/api/webhooks/whatsapp", {401}, method="POST", data=b"{}", headers={"Content-Type": "application/json"})
    check("empty_webhook_verification_rejected", "/api/webhooks/whatsapp", {400, 403, 422, 503}, webhook_query=True)
    check("missing_google_callback_state_rejected", "/api/auth/native/google/callback", {400, 422})
    passed = sum(item["passed"] for item in checks)
    return {"checked_at": datetime.now(UTC).isoformat(), "origin": origin,
            "mode": "explicit_loopback" if urlsplit(origin).hostname in {"localhost", "127.0.0.1", "::1"} else "public_https",
            "status": "passed" if passed == len(checks) else "failed", "passed": passed,
            "failed": len(checks) - passed, "checks": checks, "configuration": facts,
            "response_limit_bytes": MAX_RESPONSE_BYTES, "redirects_followed": False,
            "provider_login_performed": False, "model_calls_performed": False,
            "external_messages_submitted": False, "customer_data_writes_performed": False,
            "scope": "Anonymous configuration and rejection boundaries only; no live Google, WhatsApp, model or delivery verification."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--allow-loopback", action="store_true", help="Permit an explicitly selected local HTTP(S) origin")
    parser.add_argument("--output", type=Path, help="Write sanitized JSON evidence to this file")
    args = parser.parse_args(argv)
    try:
        result = run_checks(args.origin, allow_loopback=args.allow_loopback)
    except VerificationError as failure:
        result = {"status": "failed", "failure": str(failure)}
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
