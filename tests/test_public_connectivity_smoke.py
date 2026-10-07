"""Public smoke rejects unsafe targets and oversized or secret-bearing responses."""

from copy import deepcopy
import importlib.util
import io
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "milo_public_connectivity_smoke_tests", Path(__file__).resolve().parents[1] / "scripts/public_connectivity_smoke.py")
smoke_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(smoke_module)
MAX_RESPONSE_BYTES = smoke_module.MAX_RESPONSE_BYTES
VerificationError = smoke_module.VerificationError
bounded_read = smoke_module.bounded_read
configuration_facts = smoke_module.configuration_facts
validate_origin = smoke_module.validate_origin


def config(configured=False):
    return {"backend_configured": True, "google_configured": configured,
            "client_id": "public-google-client.apps.googleusercontent.com" if configured else None,
            "google": {"configured": configured, "client_id": "public-google-client.apps.googleusercontent.com" if configured else None,
                       "native_configured": configured, "native_broker_configured": configured,
                       "native_platforms": {"ios": configured, "android": configured},
                       "native_flow": "https_google_broker_with_one_use_S256_handoff", "native_broker_live_verified": False},
            "browser_authentication": "http_only_cookie_with_csrf",
            "native_authentication": "revocable_bearer_with_google_nonce_and_S256_proof",
            "native_refresh_supported": True, "identity_grants_service_access": False,
            "providers": {"whatsapp_personal": {"status": "unavailable", "pairing_supported": False},
                          "whatsapp_business": {"status": "configured" if configured else "not_configured", "live_verification_required": True}},
            "external_sends_enabled": configured, "model": {"status": "configured" if configured else "disabled", "quality_verified": False}}


@pytest.mark.parametrize("origin", ["http://milo.example.test", "https://user:password@milo.example.test", "https://milo.example.test/api", "https://milo.example.test?token=value", "https://milo.example.test#secret", "\nhttps://milo.example.test", "http://localhost:3100", "https://127.0.0.1:3100", "http://[::1]:3100"])
def test_origin_requires_public_https_or_explicit_loopback(origin):
    with pytest.raises(VerificationError):
        validate_origin(origin)
    assert validate_origin("http://localhost:3100", allow_loopback=True) == "http://localhost:3100"
    with pytest.raises(VerificationError):
        validate_origin("http://milo.example.test", allow_loopback=True)


@pytest.mark.parametrize("configured", [False, True])
def test_published_flags_accept_truthful_future_configuration_without_recording_client_ids(configured):
    public = config(configured)
    facts = configuration_facts(public)
    assert facts["google_native_broker_configured"] is configured
    assert facts["external_sends_enabled"] is configured
    assert "public-google-client" not in str(facts)
    inconsistent = deepcopy(public)
    inconsistent["google"]["native_platforms"]["android"] = not configured
    with pytest.raises(VerificationError):
        configuration_facts(inconsistent)
    credential = deepcopy(public)
    credential["google"]["client_secret"] = "synthetic-secret-that-must-not-be-recorded"
    with pytest.raises(VerificationError) as failure:
        configuration_facts(credential)
    assert "synthetic-secret" not in str(failure.value)


def test_streaming_response_limit_prevents_unbounded_reading_without_content_length():
    response = io.BytesIO(b"x" * (MAX_RESPONSE_BYTES * 3))
    response.headers = {}
    with pytest.raises(VerificationError):
        bounded_read(response)
    assert response.tell() == MAX_RESPONSE_BYTES + 1
    declared = io.BytesIO(b"x" * (MAX_RESPONSE_BYTES * 3))
    declared.headers = {"Content-Length": str(MAX_RESPONSE_BYTES * 3)}
    with pytest.raises(VerificationError):
        bounded_read(declared)
    assert declared.tell() == 0
