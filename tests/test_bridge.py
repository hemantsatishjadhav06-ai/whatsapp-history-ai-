import asyncio
import json

import httpx
import pytest

from assistant.bridge import dispatch_bridge, payload_hash, validate_envelope


TOKEN = "synthetic-gateway-token-" + "a" * 40


def envelope():
    payload = {"text": "ठीक आहे, धन्यवाद 🙏"}
    return {"schema_version": 1, "action_id": "synthetic-action", "workspace_id": "synthetic-workspace",
            "connector_id": "synthetic-connector", "account_id": "synthetic-account",
            "conversation_id": "synthetic-chat", "recipient_id": "synthetic-recipient",
            "kind": "SEND_TEXT", "payload": payload, "payload_hash": payload_hash(payload), "connector_fence": 1}


def result(value, **changes):
    return {"schema_version": 1, "simulation": True, "action_id": value["action_id"],
            "payload_hash": value["payload_hash"], "connector_fence": value["connector_fence"],
            "status": "accepted", "provider_message_id": "mock-synthetic-accepted",
            "submitted_at": "2026-10-06T12:00:00Z", **changes}


def run_dispatch(value, handler, **options):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await dispatch_bridge(value, gateway_url=options.pop("gateway_url", "http://127.0.0.1:8090"),
                                         token=options.pop("token", TOKEN), client=client, **options)
    return asyncio.run(run())


def test_bridge_posts_exact_immutable_action_once_with_separate_internal_authentication():
    calls = []
    value = envelope()

    def handler(request):
        assert request.url == "http://127.0.0.1:8090/v1/actions"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        calls.append(json.loads(request.content))
        assert calls[-1] == value
        return httpx.Response(200, json=result(value))

    response = run_dispatch(value, handler)
    assert response.status == "accepted"
    assert response.provider_message_id == "mock-synthetic-accepted"
    assert response.error_code is None
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"action_id": "another-tenant-action"}, {"payload_hash": "0" * 64}, {"connector_fence": 2},
    {"connector_fence": True}, {"schema_version": True}, {"simulation": False},
    {"provider_message_id": "actual-provider-unverified"}, {"status": "delivered"},
    {"extra_authorization_assertion": True}, {"status": "uncertain", "provider_message_id": "mock-wrong"},
])
def test_unmatched_or_unverified_http_acceptance_is_uncertain_without_retry(changes):
    value = envelope()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=result(value, **changes))

    response = run_dispatch(value, handler)
    assert response.status == "uncertain"
    assert response.provider_message_id is None
    assert response.error_code == "DELIVERY_UNCERTAIN"
    assert len(calls) == 1


@pytest.mark.parametrize("status,expected", [(401, "failed"), (403, "failed"), (409, "failed"),
                                               (422, "failed"), (500, "uncertain"), (503, "uncertain"),
                                               (302, "uncertain")])
def test_rejections_and_server_loss_are_classified_without_blind_retry(status, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"reason_code": "GLOBAL_PAUSE"},
                              headers={"Location": "https://attacker.example.test"})

    assert run_dispatch(envelope(), handler).status == expected
    assert len(calls) == 1


def test_timeout_after_possible_submission_preserves_uncertainty():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("Synthetic loss after possible submission", request=request)

    response = run_dispatch(envelope(), handler)
    assert response.status == "uncertain"
    assert response.error_code == "DELIVERY_UNCERTAIN"
    assert len(calls) == 1


def test_explicit_gateway_uncertainty_is_retained():
    value = envelope()
    response = run_dispatch(value, lambda request: httpx.Response(200, json=result(value, status="uncertain",
                                                                                provider_message_id=None)))
    assert response.status == "uncertain"
    assert response.provider_message_id is None


@pytest.mark.parametrize("gateway_url", ["http://public.example.test", "http://token@127.0.0.1", "file:///tmp/socket",
                                        "http://localhost?route=attacker", "http://localhost/custom-action"])
def test_gateway_origin_is_server_configured_and_plain_http_is_loopback_only(gateway_url):
    with pytest.raises(ValueError):
        run_dispatch(envelope(), lambda request: pytest.fail("No transport should run"), gateway_url=gateway_url)


@pytest.mark.parametrize("token", ["", "too-short", "x" * 32 + "\n"])
def test_bridge_requires_separate_bounded_gateway_auth_token(token):
    with pytest.raises(ValueError):
        run_dispatch(envelope(), lambda request: pytest.fail("No transport should run"), token=token)


def test_envelope_rejects_unchecked_authority_assertions_bad_hash_and_executable_values():
    value = envelope()
    for changed in [{**value, "allowed": True}, {**value, "payload_hash": "0" * 64},
                    {**value, "connector_fence": True}, {**value, "schema_version": True},
                    {**value, "payload": {"text": "hello", "recipient": "attacker"}},
                    {**value, "payload": {"text": float("nan")}},
                    {**value, "payload": {"text": {"__proto__": "bad"}}}]:
        with pytest.raises(ValueError):
            validate_envelope(changed)


def test_validation_copies_payload_before_transport_await():
    original = envelope()
    copy = validate_envelope(original)
    original["payload"]["text"] = "Changed after admission"
    assert copy["payload"]["text"] == "ठीक आहे, धन्यवाद 🙏"
