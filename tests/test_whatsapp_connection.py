"""Provider-mocked contracts, consent isolation and one-shot Business history sync."""

from datetime import timedelta
import asyncio
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import httpx
import pytest
import test_webhooks
from sqlalchemy import func, select

from assistant.db import now
from assistant.models import Connector, Conversation, Message, Permission, StyleProfile
from assistant.whatsapp_provider import graph_endpoint
from test_business_owner_binding import cloud_settings, google_login, workspace
from test_webhooks import incoming, signed_post

hook_app = test_webhooks.hook_app


@pytest.fixture
def business(app, client, monkeypatch):
    cloud_settings(app)
    app.state.settings.whatsapp_app_secret = "synthetic-app-secret"
    app.state.settings.whatsapp_verify_token = "synthetic-verify-token"
    google_login(client, app, monkeypatch, "authorized-google-sub")
    workspace_id = workspace(client)
    connector = client.post("/integrations/whatsapp/connect", json={"workspace_id": workspace_id}).json()

    def verify(url, **kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "id": "operator-phone-id", "display_phone_number": "+1 555 000 0000",
            "is_on_biz_app": True, "platform_type": "CLOUD_API"})

    monkeypatch.setattr(httpx, "get", verify)
    response = client.post(f"/connectors/{connector['id']}/verify")
    assert response.status_code == 200, response.text
    assert response.json()["coexistence"] is True
    return {"workspace_id": workspace_id, "connector_id": connector["id"], "client": client}


def contact(business, **changes):
    return business["client"].post("/integrations/whatsapp/contacts", json={
        "connector_id": business["connector_id"], "phone_number": "+1 (555) 111-1111",
        "title": "Approved contact", "read": True, "retain": True, "learn": True, "draft": True,
        **changes})


def test_status_is_owner_scoped_and_never_contains_provider_credentials(app, business):
    result = business["client"].get("/v1/integrations/whatsapp/status",
                                    params={"workspace_id": business["workspace_id"]})
    assert result.status_code == 200
    body = result.json()
    assert body["configured"] is True
    assert body["owner_authorized"] is True
    assert body["external_sends_enabled"] is False
    assert body["personal_account"]["supported"] is False
    assert body["business_app_history"]["maximum_days"] == 180
    assert body["business_app_history"]["live_verified"] is False
    assert body["connectors"][0]["lease_valid"] is True
    assert body["connectors"][0]["live_delivery_verified"] is False
    assert "synthetic-server-provider-secret" not in result.text
    assert "synthetic-app-secret" not in result.text
    assert business["client"].get("/integrations/whatsapp/status",
                                   params={"workspace_id": "other-owner-workspace"}).status_code == 404


def test_slow_history_provider_does_not_block_owner_pause(business, monkeypatch):
    assert contact(business).status_code == 201
    entered, release = Event(), Event()

    def slow_provider(url, **kwargs):
        entered.set()
        assert release.wait(timeout=8)
        return httpx.Response(200, request=httpx.Request("POST", url), json={"request_id": "history-proof"})

    monkeypatch.setattr(httpx, "post", slow_provider)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(business["client"].post, "/integrations/whatsapp/history-sync",
                                 json={"connector_id": business["connector_id"]})
        try:
            assert entered.wait(timeout=3)
            started = monotonic()
            paused = business["client"].post("/pause-all", params={"workspace_id": business["workspace_id"]})
            elapsed = monotonic() - started
            assert paused.status_code == 200
            assert elapsed < 2, "Owner controls waited on the provider network call"
        finally:
            release.set()
        assert future.result(timeout=3).json()["status"] == "accepted"


def test_new_business_contact_grants_are_explicit_normalized_and_upserted(app, business):
    result = contact(business)
    assert result.status_code == 201, result.text
    first = result.json()
    assert first["permissions"]["send"] is False
    assert first["permissions"]["share"] is False
    assert first["history_recovered"] is False
    second = contact(business, learn=False, draft=False).json()
    assert second["conversation"]["id"] == first["conversation"]["id"]
    assert second["permissions"]["version"] > first["permissions"]["version"]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Conversation)) == 1
        assert db.scalar(select(Conversation)).provider_chat_id == "15551111111"


@pytest.mark.parametrize("changes", [{"retain": False}, {"read": False}, {"send": True},
                                      {"phone_number": "15551111111@g.us"}])
def test_contact_permission_and_recipient_constraints(app, business, changes):
    assert contact(business, **changes).status_code == 422
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Conversation)) == 0


def test_unbound_development_owner_cannot_use_deployment_business_number(app, owner_client):
    cloud_settings(app)
    workspace_id = workspace(owner_client)
    result = owner_client.post("/integrations/whatsapp/connect", json={"workspace_id": workspace_id})
    assert result.status_code == 403
    assert owner_client.get("/integrations/whatsapp/status", params={"workspace_id": workspace_id}).json()[
        "configured"] is False


def test_history_request_is_persisted_before_provider_call_and_never_retried(app, business, monkeypatch):
    contact(business)
    requests = []

    def provider(url, **kwargs):
        requests.append((url, kwargs))
        with app.state.session_factory() as db:
            row = db.get(Connector, business["connector_id"])
            assert row.capabilities["business_history_request"]["status"] == "submitting"
        raise httpx.ReadTimeout("Unknown outcome")

    monkeypatch.setattr(httpx, "post", provider)
    first = business["client"].post("/integrations/whatsapp/history-sync",
                                     json={"connector_id": business["connector_id"]})
    second = business["client"].post("/integrations/whatsapp/history-sync",
                                      json={"connector_id": business["connector_id"]})
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "uncertain"
    assert first.json()["attempt_id"] == second.json()["attempt_id"]
    assert second.json()["resubmitted"] is False
    assert len(requests) == 1
    assert requests[0][0] == "https://graph.facebook.com/v23.0/operator-phone-id/smb_app_data"
    assert requests[0][1]["json"] == {"messaging_product": "whatsapp", "sync_type": "history"}


def test_history_provider_acceptance_preserves_concurrent_webhook_progress(app, business, monkeypatch):
    contact(business)

    def provider(url, **kwargs):
        # Simulate a signed webhook handled by another API replica while this
        # replica is waiting for the provider's history request response.
        with app.state.session_factory() as db:
            row = db.get(Connector, business["connector_id"])
            row.capabilities = {**row.capabilities, "business_history_sharing": "observed"}
            db.commit()
        return httpx.Response(200, json={"messaging_product": "whatsapp", "request_id": "provider-request"})

    monkeypatch.setattr(httpx, "post", provider)
    result = business["client"].post("/integrations/whatsapp/history-sync",
                                     json={"connector_id": business["connector_id"]})
    assert result.json()["status"] == "accepted"
    assert result.json()["history_sharing_verified"] is False
    with app.state.session_factory() as db:
        assert db.get(Connector, business["connector_id"]).capabilities["business_history_sharing"] == "observed"


def test_expired_verification_gap_requires_contact_review_and_new_fence(app, business):
    conversation_id = contact(business).json()["conversation"]["id"]
    with app.state.session_factory() as db:
        row = db.get(Connector, business["connector_id"])
        old_fence = row.fence
        row.lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    result = business["client"].post(f"/connectors/{business['connector_id']}/verify")
    assert result.status_code == 200
    assert result.json()["reconnect_review_required"] is True
    assert result.json()["fence"] == old_fence + 1
    with app.state.session_factory() as db:
        assert db.get(Conversation, conversation_id).control_state == "RECONNECT_REVIEW"


@pytest.mark.parametrize("constraint", ["no_contacts", "expired_lease", "unverified_coexistence", "no_webhook"])
def test_history_sync_requires_eligible_number_and_selected_contacts(app, business, monkeypatch, constraint):
    if constraint != "no_contacts":
        contact(business)
    with app.state.session_factory() as db:
        row = db.get(Connector, business["connector_id"])
        if constraint == "expired_lease":
            row.lease_expires_at = now() - timedelta(seconds=1)
        elif constraint == "unverified_coexistence":
            row.capabilities = {**row.capabilities, "business_app_coexistence": "unknown"}
        db.commit()
    if constraint == "no_webhook":
        app.state.settings.whatsapp_app_secret = ""
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: pytest.fail("No eligible provider submission"))
    result = business["client"].post("/integrations/whatsapp/history-sync",
                                     json={"connector_id": business["connector_id"]})
    assert result.status_code == 409


def coexistence(hook_app):
    with hook_app.db() as db:
        row = db.get(Connector, hook_app.ids.connector)
        row.owner_sender_id = "15550000000"
        row.capabilities = {"business_app_coexistence": "supported"}
        db.get(Permission, hook_app.ids.permission).learn = True
        db.commit()


def coexistence_payload(field, **value):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": field, "value": {
        "metadata": {"phone_number_id": "12345", "display_phone_number": "15550000000"}, **value}}]}]}


def history_payload(*messages, contact_id="11111", **metadata):
    return coexistence_payload("history", history=[{
        "metadata": {"phase": 0, "chunk_order": 1, "progress": 100, **metadata},
        "threads": [{"id": contact_id, "messages": list(messages)}]}])


def test_business_history_is_contact_scoped_deduplicated_and_cannot_open_send_window(hook_app):
    coexistence(hook_app)
    payload = history_payload(incoming(timestamp=str(int(now().timestamp()))),
                              incoming(id="wamid.owner", **{"from": "15550000000"}))
    assert signed_post(hook_app, payload).json()["accepted"] == 2
    assert signed_post(hook_app, payload).json()["accepted"] == 2
    with hook_app.db() as db:
        rows = list(db.scalars(select(Message).order_by(Message.direction)))
        assert len(rows) == 2
        assert all(row.origin == "history" for row in rows)
        assert rows[1].author_kind == "unknown_owner_outgoing"
        assert all(row.excluded_from_learning for row in rows)
        assert db.get(Conversation, hook_app.ids.conversation).last_inbound_at is None
        assert db.scalar(select(func.count()).select_from(StyleProfile)) == 0
        progress = db.get(Connector, hook_app.ids.connector).capabilities["business_history_last_chunk"]
        assert progress["progress"] == 100
        assert progress["completeness_verified"] is False


@pytest.mark.parametrize("constraint", ["no_retain", "wrong_sender", "unknown_contact", "group"])
def test_history_never_retains_unselected_or_unconsented_chats(hook_app, constraint):
    coexistence(hook_app)
    with hook_app.db() as db:
        if constraint == "no_retain":
            db.get(Permission, hook_app.ids.permission).retain = False
        elif constraint == "group":
            db.get(Conversation, hook_app.ids.conversation).kind = "group"
        db.commit()
    message = incoming(**({"from": "33333"} if constraint == "wrong_sender" else {}))
    payload = history_payload(message, contact_id="22222" if constraint == "unknown_contact" else "11111")
    result = signed_post(hook_app, payload)
    assert result.status_code == 200
    assert result.json()["accepted"] == 0
    with hook_app.db() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 0


def test_history_requires_graph_verified_coexistence_and_matching_business_phone(hook_app):
    payload = history_payload(incoming())
    assert signed_post(hook_app, payload).json()["accepted"] == 0
    coexistence(hook_app)
    payload["entry"][0]["changes"][0]["value"]["metadata"]["display_phone_number"] = "15559999999"
    assert signed_post(hook_app, payload).json()["accepted"] == 0
    with hook_app.db() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 0


def test_business_app_echo_triggers_human_takeover_without_unsafe_style_learning(hook_app):
    coexistence(hook_app)
    payload = coexistence_payload("smb_message_echoes", message_echoes=[incoming(
        **{"from": "15550000000"}, to="11111", timestamp=str(int(now().timestamp())))])
    assert signed_post(hook_app, payload).json()["accepted"] == 1
    assert signed_post(hook_app, payload).json()["accepted"] == 1
    with hook_app.db() as db:
        row = db.scalar(select(Message))
        assert row.direction == "outbound"
        assert row.origin == "live"
        assert row.author_kind == "unknown_owner_outgoing"
        assert row.excluded_from_learning
        conversation = db.get(Conversation, hook_app.ids.conversation)
        assert conversation.control_state == "HUMAN_TAKEOVER"
        assert conversation.control_epoch == 1


def test_signed_history_sharing_denial_is_truthful(hook_app):
    coexistence(hook_app)
    payload = coexistence_payload("history", history=[{"errors": [{"code": 2593109}]}])
    assert signed_post(hook_app, payload).status_code == 200
    with hook_app.db() as db:
        assert db.get(Connector, hook_app.ids.connector).capabilities["business_history_sharing"] == "declined"
        assert db.scalar(select(func.count()).select_from(Message)) == 0


def test_history_progress_validation_rolls_back_entire_batch(hook_app):
    coexistence(hook_app)
    payload = history_payload(incoming())
    payload["entry"][0]["changes"] += history_payload(incoming(id="wamid.next"), progress=True)[
        "entry"][0]["changes"]
    assert signed_post(hook_app, payload).status_code == 400
    with hook_app.db() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 0


def test_only_reviewed_owner_messages_can_become_style_evidence(app, business):
    conversation_id = contact(business).json()["conversation"]["id"]
    with app.state.session_factory() as db:
        row = Message(workspace_id=business["workspace_id"], connector_id=business["connector_id"],
                      conversation_id=conversation_id, provider_message_id="wamid.human",
                      sender_id="15550000000", direction="outbound", origin="history",
                      author_kind="unknown_owner_outgoing", text="Hello there", provider_timestamp=now(),
                      excluded_from_learning=True)
        db.add(row)
        db.commit()
        message_id = row.id
    result = business["client"].post("/integrations/whatsapp/owner-authorship", json={
        "conversation_id": conversation_id, "message_ids": [message_id], "confirm_authored_by_owner": True})
    assert result.status_code == 200, result.text
    assert result.json()["confirmed"] == 1
    with app.state.session_factory() as db:
        assert db.get(Message, message_id).author_kind == "human_owner"
        assert db.scalar(select(StyleProfile)).sample_count == 1
        db.get(Message, message_id).author_kind = "assistant"
        db.commit()
    assert business["client"].post("/integrations/whatsapp/owner-authorship", json={
        "conversation_id": conversation_id, "message_ids": [message_id],
        "confirm_authored_by_owner": True}).status_code == 409


@pytest.mark.parametrize("status", [302, 500, 503])
def test_cloud_send_server_error_or_redirect_is_uncertain_without_redirect_follow(monkeypatch, status):
    from assistant.messaging import _transport_send

    original = httpx.AsyncClient
    observed = []

    def provider(request):
        observed.append(request)
        return httpx.Response(status, headers={"Location": "https://other-host.test/collect"})

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(provider), **kwargs)

    monkeypatch.setattr("assistant.messaging.httpx.AsyncClient", client)
    result = asyncio.run(_transport_send(SimpleNamespace(whatsapp_api_version="v25.0", whatsapp_access_token="test"),
                                        "whatsapp_cloud", "12345", "15551111111", "Exact reviewed text", "attempt"))
    assert result == ("uncertain", None, "transport_outcome_unknown")
    assert len(observed) == 1
    assert observed[0].url.host == "graph.facebook.com"


def test_configured_graph_resource_cannot_escape_fixed_origin():
    assert graph_endpoint("v25.0", "x/../../me?token=secret", "messages").endswith(
        "/x%2F..%2F..%2Fme%3Ftoken%3Dsecret/messages")
    with pytest.raises(ValueError):
        graph_endpoint("v25.0/../../me", "123")
