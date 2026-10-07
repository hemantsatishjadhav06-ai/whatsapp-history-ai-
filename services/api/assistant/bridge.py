"""Opt-in transport to the authenticated, mock-only Node action gateway.

The SQL dispatcher owns the durable action/attempt ledger. This client does not
retry a request or infer delivery from HTTP acceptance. The gateway independently
reads current Python dispatch authority immediately before its simulated socket.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


ENVELOPE_KEYS = frozenset({"schema_version", "action_id", "workspace_id", "connector_id", "account_id",
                           "conversation_id", "recipient_id", "kind", "payload", "payload_hash",
                           "connector_fence"})
ACTION_KINDS = frozenset({"SEND_TEXT", "QUOTE", "REACTION", "FORWARD"})


def payload_hash(payload: Mapping[str, Any]) -> str:
    """Shared Python/Node compact, sorted UTF-8 JSON payload fingerprint."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("Action payload is too deeply nested")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) <= 2 ** 53 - 1:
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii() or key in {"__proto__", "constructor", "prototype"}:
                raise ValueError("Invalid action payload key")
            _json_value(item, depth + 1)
        return
    raise ValueError("Action payload contains an unsupported JSON value")


def validate_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_KEYS:
        raise ValueError("Invalid gateway envelope fields")
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        raise ValueError("Unsupported gateway schema")
    for field in ("action_id", "workspace_id", "connector_id", "account_id", "conversation_id", "recipient_id"):
        value = envelope[field]
        if not isinstance(value, str) or not value or len(value) > 255:
            raise ValueError(f"Invalid gateway {field}")
    if not isinstance(envelope["kind"], str) or envelope["kind"] not in ACTION_KINDS:
        raise ValueError("Unsupported gateway action kind")
    fence = envelope["connector_fence"]
    if type(fence) is not int or not 1 <= fence <= 2 ** 53 - 1:
        raise ValueError("Invalid gateway connector fence")
    if not isinstance(envelope["payload"], dict):
        raise ValueError("Action payload must be an object")
    _json_value(envelope["payload"])
    payload = envelope["payload"]
    keys = {"text"} if envelope["kind"] == "SEND_TEXT" else {
        "target_message_id", "target_source_revision", "native_record_ref"}
    if envelope["kind"] == "QUOTE":
        keys.add("text")
    elif envelope["kind"] == "REACTION":
        keys.add("emoji")
    elif envelope["kind"] == "FORWARD":
        keys |= {"route_id", "route_version", "category"}
    if set(payload) != keys:
        raise ValueError("Invalid typed action payload fields")
    if envelope["payload_hash"] != payload_hash(envelope["payload"]):
        raise ValueError("Gateway payload hash mismatch")
    body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if len(body.encode()) > 65_536:
        raise ValueError("Gateway envelope is too large")
    return json.loads(body)  # Own an immutable-by-caller copy across the await boundary.


def _gateway_target(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Gateway URL must be a configured HTTP(S) origin")
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if not loopback:
            raise ValueError("Plain HTTP gateway is restricted to loopback")
    return base_url.rstrip("/") + "/v1/actions"


@dataclass(frozen=True)
class BridgeResult:
    status: str
    provider_message_id: str | None = None
    error_code: str | None = None


def _result(response: httpx.Response, envelope: Mapping[str, Any]) -> BridgeResult:
    if 400 <= response.status_code < 500:
        return BridgeResult("failed", error_code="BRIDGE_REJECTED")
    if response.status_code != 200:
        return BridgeResult("uncertain", error_code="DELIVERY_UNCERTAIN")
    try:
        data = response.json()
        fields = {"schema_version", "simulation", "action_id", "payload_hash", "connector_fence", "status",
                  "provider_message_id", "submitted_at"}
        if (not isinstance(data, dict) or not fields <= set(data) or set(data) - fields - {"error_code"}
                or type(data.get("schema_version")) is not int or data.get("schema_version") != 1
                or type(data.get("connector_fence")) is not int or data.get("simulation") is not True
                or any(data.get(key) != envelope[key] for key in ("action_id", "payload_hash", "connector_fence"))
                or data.get("status") not in {"accepted", "uncertain", "failed"}):
            raise ValueError("Uncorrelated gateway result")
        message_id = data.get("provider_message_id")
        if data["status"] == "accepted" and (not isinstance(message_id, str) or not message_id.startswith("mock-")):
            raise ValueError("Unverified mock acceptance")
        if data["status"] != "accepted" and message_id is not None:
            raise ValueError("Unexpected provider result")
        return BridgeResult(data["status"], message_id,
                            {"uncertain": "DELIVERY_UNCERTAIN", "failed": "BRIDGE_REJECTED"}.get(data["status"]))
    except (ValueError, TypeError):
        return BridgeResult("uncertain", error_code="DELIVERY_UNCERTAIN")


async def dispatch_bridge(envelope: Mapping[str, Any], *, gateway_url: str, token: str,
                          timeout_seconds: float = 10, client: httpx.AsyncClient | None = None) -> BridgeResult:
    """One transport attempt, with no automatic resend after an unknown outcome."""
    target = _gateway_target(gateway_url)
    immutable = validate_envelope(envelope)
    if not isinstance(token, str) or len(token.encode()) < 32 or any(c.isspace() for c in token):
        raise ValueError("Gateway requires a separate configured token of at least 32 bytes")
    if not 0 < timeout_seconds <= 60:
        raise ValueError("Gateway timeout must be between 0 and 60 seconds")
    try:
        if client is not None:
            response = await client.post(target, json=immutable, headers={"Authorization": f"Bearer {token}"},
                                         timeout=timeout_seconds, follow_redirects=False)
        else:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as connection:
                response = await connection.post(target, json=immutable,
                                                 headers={"Authorization": f"Bearer {token}"},
                                                 timeout=timeout_seconds)
        return _result(response, immutable)
    except httpx.HTTPError:
        return BridgeResult("uncertain", error_code="DELIVERY_UNCERTAIN")
