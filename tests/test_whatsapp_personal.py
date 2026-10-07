"""Owner/account/fence/grant boundaries for the synthetic personal session bridge."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic

import httpx
import pytest
from sqlalchemy import func, select

from assistant.db import now, uid
from assistant.models import Connector, Conversation, Draft, Message, MessageEvent, Permission, SendAttempt
from assistant.whatsapp_personal import (
    AuthorityInput, SessionEvent, ReceiptData, apply_receipt, identity, session_authority,
)
from assistant.whatsapp_personal_models import PersonalAccountAlias, PersonalWhatsAppSession
from conftest import login
from test_business_owner_binding import google_login, workspace
from test_messaging import approve, make_draft

PREFIX = "/integrations/whatsapp/personal"
INTERNAL = {"Authorization": "Bearer test-internal-service-token"}
OWNER = "15550000000@s.whatsapp.net"
OWNER_LID = "98765432100@lid"
CONTACT = "15551111111@s.whatsapp.net"


def settings(app):
    app.state.settings.whatsapp_personal_enabled = True
    app.state.settings.whatsapp_personal_session_url = "http://127.0.0.1:8091"
    app.state.settings.whatsapp_personal_session_token = "synthetic-gateway-token-32-bytes-minimum"
    app.state.settings.enable_external_sends = True


def envelope(app, connector_id):
    with app.state.session_factory() as db:
        return identity(db.get(Connector, connector_id))


def post_event(client, ident, event_type, **data):
    return client.post("/internal/whatsapp-session-events", headers=INTERNAL,
                       json={**ident, "event_type": event_type, "data": data})


def message(client, ident, **changes):
    data = {"provider_message_id": uid(), "provider_chat_id": CONTACT, "sender_id": CONTACT,
            "direction": "inbound", "origin": "live", "provider_timestamp": now().isoformat(),
            "content": {"type": "text", "text": "Synthetic private content"}}
    data.update(changes)
    with client.app.state.session_factory() as db:
        conv = db.scalar(select(Conversation).where(Conversation.connector_id == ident["connector_id"],
            Conversation.workspace_id == ident["workspace_id"],
            Conversation.provider_chat_id == data["provider_chat_id"]))
        data.setdefault("conversation_id", conv.id if conv else uid())
    return post_event(client, ident, "message", **data)


def authorize(client, connector_id, **changes):
    return client.post(PREFIX + "/chats/authorize", json={"connector_id": connector_id,
        "provider_chat_id": CONTACT, "title": "Synthetic approved contact", "read": True, "retain": True,
        "learn": True, "draft": True, "send": True, "recipient_opted_in": True, **changes})


@pytest.fixture
def personal(app, owner_client, monkeypatch):
    settings(app)
    wid = workspace(owner_client)
    state = {"schema_version": 1, "state": "starting", "account_id": None, "chats": [
        {"provider_chat_id": CONTACT, "title": "Contact", "kind": "contact"},
        {"provider_chat_id": OWNER, "title": "Owner", "kind": "contact"},
        {"provider_chat_id": OWNER_LID, "title": "Owner alias", "kind": "contact"}]}
    calls = []

    def private(_settings, operation, payload):
        calls.append((operation, payload))
        # Start commits session metadata before the private actor can call back.
        with app.state.session_factory() as db:
            row = db.get(Connector, payload["connector_id"])
            assert row is not None and row.fence == payload["connector_fence"]
            if operation != "sessions/disconnect":
                assert db.scalar(select(PersonalWhatsAppSession).where(
                    PersonalWhatsAppSession.connector_id == row.id)) is not None
        return dict(state, state="disconnected") if operation == "sessions/disconnect" else dict(state)

    monkeypatch.setattr("assistant.whatsapp_personal.private_request", private)
    started = owner_client.post(PREFIX + "/start", json={"workspace_id": wid})
    assert started.status_code == 200, started.text
    cid = started.json()["connector"]["id"]
    paired = post_event(owner_client, envelope(app, cid), "connection", state="connected",
                        account_id=OWNER, account_aliases=[OWNER, OWNER_LID])
    assert paired.status_code == 200, paired.text
    state.update(state="connected", account_id=OWNER)
    return {"workspace": {"id": wid}, "connector": {"id": cid}, "state": state, "calls": calls,
            "client": owner_client, "identity": envelope(app, cid)}


@pytest.fixture
def personal_chat(personal):
    result = authorize(personal["client"], personal["connector"]["id"])
    assert result.status_code == 200, result.text
    return {**personal, "conversation": result.json()["conversation"], "permissions": result.json()["permissions"]}


def test_disabled_and_owner_scoped_status(app, owner_client):
    wid = workspace(owner_client)
    result = owner_client.get(PREFIX + "/status", params={"workspace_id": wid})
    assert result.json()["configured"] is False
    assert result.headers["cache-control"] in {"no-store", "private, no-store"}
    assert owner_client.post(PREFIX + "/start", json={"workspace_id": wid}).status_code == 503
    assert owner_client.get(PREFIX + "/status", params={"workspace_id": uid()}).status_code == 404


def test_verified_google_is_required_in_production(app, owner_client, monkeypatch):
    settings(app)
    wid = workspace(owner_client)
    app.state.settings.environment = "production"
    monkeypatch.setattr("assistant.whatsapp_personal.private_request", lambda *_: pytest.fail("No actor for dev owner"))
    assert owner_client.post(PREFIX + "/start", json={"workspace_id": wid}).status_code == 403
    app.state.settings.environment = "test"
    google_login(owner_client, app, monkeypatch, "personal-google-owner")
    wid = workspace(owner_client)
    app.state.settings.environment = "production"
    monkeypatch.setattr("assistant.whatsapp_personal.private_request",
                        lambda *_: {"schema_version": 1, "state": "starting", "account_id": None})
    assert owner_client.post(PREFIX + "/start", json={"workspace_id": wid}).status_code == 200


def test_pairing_owner_scope_short_expiry_and_no_secret_logs(app, personal, caplog):
    cid, client = personal["connector"]["id"], personal["client"]
    with app.state.session_factory() as db:
        db.get(Connector, cid).status = "pairing"
        db.commit()
    personal["state"].update(state="qr", qr={"value": "synthetic-pairing-private-value",
                                                "expires_at": (now() + timedelta(seconds=30)).isoformat()})
    result = client.get(PREFIX + "/pairing", params={"connector_id": cid})
    assert result.json()["qr"]["value"] == "synthetic-pairing-private-value"
    assert result.headers["cache-control"] in {"no-store", "private, no-store"}
    assert result.headers["pragma"] == "no-cache"
    assert "synthetic-pairing-private-value" not in caplog.text
    login(client, "other@example.test")
    assert client.get(PREFIX + "/pairing", params={"connector_id": cid}).status_code == 404


@pytest.mark.parametrize("expires", [-1, 90])
def test_pairing_rejects_expired_or_long_lived_qr(app, personal, expires):
    with app.state.session_factory() as db:
        db.get(Connector, personal["connector"]["id"]).status = "pairing"
        db.commit()
    personal["state"].update(state="qr", qr={"value": "synthetic", "expires_at": (
        now() + timedelta(seconds=expires)).isoformat()})
    result = personal["client"].get(PREFIX + "/pairing", params={"connector_id": personal["connector"]["id"]})
    assert result.status_code == 200 and result.json()["qr"] is None


def test_only_discovered_individual_contacts_receive_explicit_grants(app, personal):
    client, cid = personal["client"], personal["connector"]["id"]
    assert authorize(client, cid, provider_chat_id="15552222222@s.whatsapp.net").status_code == 404
    assert authorize(client, cid, provider_chat_id="15551111111@g.us").status_code == 422
    selected = authorize(client, cid, send=False, recipient_opted_in=False).json()
    assert selected["permissions"]["send"] is False and selected["permissions"]["share"] is False
    changed = authorize(client, cid, learn=False).json()
    assert changed["conversation"]["id"] == selected["conversation"]["id"]
    assert changed["permissions"]["version"] > selected["permissions"]["version"]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Conversation)) == 1
        assert db.scalar(select(func.count()).select_from(Message)) == 0


@pytest.mark.parametrize("changes", [{"read": False}, {"retain": False}, {"recipient_opted_in": False}])
def test_grants_require_read_retention_and_send_opt_in(personal, changes):
    assert authorize(personal["client"], personal["connector"]["id"], **changes).status_code == 422


@pytest.mark.parametrize("jid", [OWNER, OWNER_LID])
def test_owner_self_contact_and_alias_cannot_be_authorized(personal, jid):
    assert authorize(personal["client"], personal["connector"]["id"], provider_chat_id=jid).status_code == 403


def test_global_account_alias_binding_survives_disconnect(app, personal):
    client, cid = personal["client"], personal["connector"]["id"]
    assert client.post(PREFIX + "/disconnect", json={"connector_id": cid}).status_code == 200
    login(client, "second@example.test")
    wid = workspace(client)
    personal["state"].update(state="starting", account_id=None)
    started = client.post(PREFIX + "/start", json={"workspace_id": wid})
    other_id = started.json()["connector"]["id"]
    result = post_event(client, envelope(app, other_id), "connection", state="connected", account_id=OWNER_LID)
    assert result.status_code == 409
    with app.state.session_factory() as db:
        assert db.get(Connector, other_id).status == "disconnected"
        assert db.get(PersonalAccountAlias, OWNER_LID).connector_id == cid


@pytest.mark.parametrize("mutation", ["workspace", "fence", "account"])
def test_stale_or_cross_tenant_events_cannot_ingest(app, personal_chat, mutation):
    ident = dict(personal_chat["identity"])
    if mutation == "workspace":
        ident["workspace_id"] = uid()
    elif mutation == "fence":
        ident["connector_fence"] += 1
    else:
        ident["account_id"] = OWNER_LID
    assert message(personal_chat["client"], ident).status_code == 409
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 0


@pytest.mark.parametrize("grant", ["unselected", "no_read", "no_retain", "expired"])
def test_ungranted_message_body_leaves_no_content_or_outbox(app, personal_chat, grant):
    cid = personal_chat["conversation"]["id"]
    if grant != "unselected":
        with app.state.session_factory() as db:
            permission = db.scalar(select(Permission).where(Permission.conversation_id == cid))
            if grant == "no_read":
                permission.read = False
            elif grant == "no_retain":
                permission.retain = False
            else:
                permission.expires_at = now() - timedelta(seconds=1)
            db.commit()
    result = message(personal_chat["client"], personal_chat["identity"],
                     **({"provider_chat_id": "15559999999@s.whatsapp.net"} if grant == "unselected" else {}))
    assert result.json()["status"] == "ignored"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 0
        assert db.scalar(select(func.count()).select_from(MessageEvent)) == 0


@pytest.mark.parametrize("origin", ["history", "replay", "unknown"])
def test_nonlive_ingestion_never_automatically_replies(app, personal_chat, origin):
    result = message(personal_chat["client"], personal_chat["identity"], origin=origin)
    assert result.status_code == 200, result.text
    assert result.json()["automatic_reply"] is False and result.json()["live_eligible"] is False
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(SendAttempt)) == 0


def test_outgoing_authorship_requires_owner_review_and_cannot_confirm_assistant(app, personal_chat):
    client = personal_chat["client"]
    result = message(client, personal_chat["identity"], direction="outbound", sender_id=OWNER,
                     origin="history", author_kind="human_owner")
    assert result.json()["author_kind"] == "unknown_owner_outgoing"
    mid = result.json()["message_id"]
    with app.state.session_factory() as db:
        assert db.get(Message, mid).excluded_from_learning is True
    confirmed = client.post(PREFIX + "/authorship/confirm", json={
        "conversation_id": personal_chat["conversation"]["id"], "message_ids": [mid],
        "confirm_authored_by_owner": True})
    assert confirmed.json()["confirmed_count"] == 1
    with app.state.session_factory() as db:
        assert db.get(Message, mid).author_kind == "human_owner"
        assert db.get(Message, mid).excluded_from_learning is False
        db.get(Message, mid).author_kind = "assistant"
        db.commit()
    assert client.post(PREFIX + "/authorship/confirm", json={
        "conversation_id": personal_chat["conversation"]["id"], "message_ids": [mid],
        "confirm_authored_by_owner": True}).status_code == 409


def test_lease_loss_and_reconnect_require_contact_review(app, personal_chat):
    client, cid = personal_chat["client"], personal_chat["connector"]["id"]
    draft = make_draft(app, personal_chat)
    approve(client, draft)
    with app.state.session_factory() as db:
        db.get(Connector, cid).lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.get(PREFIX + "/status", params={"workspace_id": personal_chat["workspace"]["id"]}).json()[
        "status"] == "unavailable"
    denied = client.post("/internal/whatsapp-session-authority", headers=INTERNAL,
                         json={**personal_chat["identity"], "operation": "status"})
    assert denied.json()["allowed"] is False
    assert message(client, personal_chat["identity"]).status_code == 409
    assert post_event(client, personal_chat["identity"], "connection", state="connected", account_id=OWNER).status_code == 200
    with app.state.session_factory() as db:
        assert db.get(Conversation, personal_chat["conversation"]["id"]).control_state == "RECONNECT_REVIEW"
        assert db.get(Draft, draft[0]).status == "cancelled"


def test_slow_private_reads_do_not_block_owner_pause(app, personal, monkeypatch):
    entered, release = Event(), Event()

    def slow(*_):
        entered.set()
        assert release.wait(timeout=8)
        return personal["state"]

    monkeypatch.setattr("assistant.whatsapp_personal.private_request", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(personal["client"].get, PREFIX + "/chats",
                             params={"connector_id": personal["connector"]["id"]})
        try:
            assert entered.wait(timeout=3)
            started = monotonic()
            result = personal["client"].post("/pause-all", params={"workspace_id": personal["workspace"]["id"]})
            assert result.status_code == 200 and monotonic() - started < 2
        finally:
            release.set()
        assert future.result(timeout=3).status_code == 200


def test_private_read_rechecks_fence_after_network(app, personal, monkeypatch):
    def fencing(*_):
        with app.state.session_factory() as db:
            db.get(Connector, personal["connector"]["id"]).fence += 1
            db.commit()
        return personal["state"]
    monkeypatch.setattr("assistant.whatsapp_personal.private_request", fencing)
    result = personal["client"].get(PREFIX + "/chats", params={"connector_id": personal["connector"]["id"]})
    assert result.status_code == 409


def synthetic_http(app, monkeypatch, callback):
    original = httpx.AsyncClient
    async def handle(request):
        import json
        return await callback(json.loads(request.content))
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(handle))
    monkeypatch.setattr("assistant.whatsapp_personal.httpx.AsyncClient", client)


def submitting(app, send, provider_id="synthetic-preallocated-id"):
    from types import SimpleNamespace
    with app.state.session_factory() as db:
        result = session_authority(AuthorityInput(**{**identity(db.get(Connector, send["connector_id"])),
                                   "operation": "send", "send": send}),
                                   SimpleNamespace(app=app), db)
        assert result["allowed"] is True
    ident = {key: send[key] for key in ("schema_version", "workspace_id", "connector_id", "connector_fence", "account_id")}
    with app.state.session_factory() as db:
        body = SessionEvent(**ident, event_type="receipt", data={})
        data = ReceiptData(attempt_id=send["attempt_id"], draft_id=send["draft_id"],
                           payload_hash=send["payload_hash"], provider_message_id=provider_id, status="submitting")
        apply_receipt(db, body, data, app.state.settings)
        db.commit()
    return ident


def test_exact_sdk_submission_is_saved_once_and_receipt_correlated(app, personal_chat, monkeypatch):
    sent = []
    async def callback(send):
        sent.append(send)
        submitting(app, send)
        return httpx.Response(200, json={"status": "accepted", "provider_message_id": "synthetic-preallocated-id"})
    synthetic_http(app, monkeypatch, callback)
    draft = make_draft(app, personal_chat)
    approve(personal_chat["client"], draft)
    first = personal_chat["client"].post(f"/drafts/{draft[0]}/dispatch")
    assert first.status_code == 200 and first.json()["status"] == "accepted", first.text
    assert personal_chat["client"].post(f"/drafts/{draft[0]}/dispatch").json() == first.json()
    assert len(sent) == 1
    receipt = post_event(personal_chat["client"], personal_chat["identity"], "receipt",
                         provider_message_id="synthetic-preallocated-id", status="delivered")
    assert receipt.status_code == 200, receipt.text
    echo = message(personal_chat["client"], personal_chat["identity"], direction="outbound", sender_id=OWNER,
                   provider_message_id="synthetic-preallocated-id")
    assert echo.json()["author_kind"] == "assistant"
    with app.state.session_factory() as db:
        assert db.scalar(select(SendAttempt)).status == "delivered"
        assert db.scalar(select(func.count()).select_from(SendAttempt)) == 1
        assert db.get(Conversation, personal_chat["conversation"]["id"]).control_state == "DRAFT_MODE"


@pytest.mark.parametrize("failure", ["timeout", "different_reference", "malformed"])
def test_unknown_http_outcome_preserves_durable_provider_id_without_retry(app, personal_chat, monkeypatch, failure):
    calls = []
    async def callback(send):
        calls.append(send)
        submitting(app, send)
        if failure == "timeout":
            raise httpx.ReadTimeout("Synthetic timeout")
        if failure == "malformed":
            return httpx.Response(200, json=["invalid metadata"])
        return httpx.Response(200, json={"status": "accepted", "provider_message_id": "different-id"})
    synthetic_http(app, monkeypatch, callback)
    draft = make_draft(app, personal_chat)
    approve(personal_chat["client"], draft)
    result = personal_chat["client"].post(f"/drafts/{draft[0]}/dispatch")
    assert result.status_code == 200 and result.json()["status"] == "uncertain", result.text
    assert result.json()["provider_message_id"] == "synthetic-preallocated-id"
    assert personal_chat["client"].post(f"/drafts/{draft[0]}/dispatch").json() == result.json()
    assert len(calls) == 1
    assert post_event(personal_chat["client"], personal_chat["identity"], "receipt",
                      provider_message_id="synthetic-preallocated-id", status="delivered").status_code == 200


@pytest.mark.parametrize("field", ["text", "recipient_id", "connector_fence", "attempt_id"])
def test_private_send_requires_exact_current_attempt(app, personal_chat, monkeypatch, field):
    async def callback(send):
        from types import SimpleNamespace
        send[field] = uid() if field == "attempt_id" else (
            send[field] + " changed" if isinstance(send[field], str) else send[field] + 1)
        with app.state.session_factory() as db:
            result = session_authority(AuthorityInput(**{**personal_chat["identity"], "operation": "send", "send": send}),
                                       SimpleNamespace(app=app), db)
            assert result["allowed"] is False
        return httpx.Response(409, json={"reason_code": "EXACT_AUTHORITY_CHANGED"})
    synthetic_http(app, monkeypatch, callback)
    draft = make_draft(app, personal_chat)
    approve(personal_chat["client"], draft)
    assert personal_chat["client"].post(f"/drafts/{draft[0]}/dispatch").json()["status"] == "failed"


def test_internal_token_is_required_for_authority_and_events(personal):
    client, ident = personal["client"], personal["identity"]
    assert client.post("/internal/whatsapp-session-authority", json={**ident, "operation": "status"}).status_code == 401
    assert client.post("/internal/whatsapp-session-events", json={**ident, "event_type": "connection",
                       "data": {"state": "connected", "account_id": OWNER}}).status_code == 401


@pytest.mark.parametrize("changes", [
    {"content": {"type": "media", "text": "Sensitive callback body"}},
    {"provider_timestamp": "2026-10-07T10:30:00"},
    {"expires_at": "2026-10-07T10:30:00"},
    {"content": {"type": "text", "text": "Sensitive callback body", "secret_extra": "Private extra"}},
])
def test_malformed_private_message_rejected_without_input_echo(personal_chat, changes):
    result = message(personal_chat["client"], personal_chat["identity"], **changes)
    assert result.status_code == 422
    assert "Sensitive callback body" not in result.text and "Private extra" not in result.text


def test_private_message_requires_exact_granted_conversation(personal_chat):
    assert message(personal_chat["client"], personal_chat["identity"], conversation_id=uid()).status_code == 409


def test_long_provider_reference_and_revision_remain_bounded_and_deduplicate(personal_chat):
    payload = {"provider_message_id": "x" * 180, "source_revision": 2147483647}
    assert message(personal_chat["client"], personal_chat["identity"], **payload).json()["status"] == "accepted"
    assert message(personal_chat["client"], personal_chat["identity"], **payload).json()["status"] == "duplicate"


def test_explicit_contact_grant_renews_expired_consent(app, personal_chat):
    with app.state.session_factory() as db:
        permission = db.scalar(select(Permission).where(Permission.conversation_id == personal_chat["conversation"]["id"]))
        permission.expires_at = now() - timedelta(seconds=1)
        db.commit()
    renewed = authorize(personal_chat["client"], personal_chat["connector"]["id"])
    assert renewed.status_code == 200 and renewed.json()["permissions"]["expires_at"] is None
    assert message(personal_chat["client"], personal_chat["identity"]).json()["status"] == "accepted"


def test_pause_authority_removes_content_grants_without_revoking_managed_session(app, personal_chat):
    client, ident = personal_chat["client"], personal_chat["identity"]
    assert client.post("/pause-all", params={"workspace_id": ident["workspace_id"]}).status_code == 200
    result = client.post("/internal/whatsapp-session-authority", headers=INTERNAL,
                         json={**ident, "operation": "ingest"})
    assert result.json()["allowed"] is True and result.json()["grants"] == []
    assert message(client, ident).json()["reason"] == "workspace_paused"
    with app.state.session_factory() as db:
        assert db.get(Connector, ident["connector_id"]).status == "connected"
        assert db.scalar(select(func.count()).select_from(Message)) == 0
