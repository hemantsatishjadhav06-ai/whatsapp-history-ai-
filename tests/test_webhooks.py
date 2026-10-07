import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from assistant.config import Settings
from assistant.db import Base, aware, make_database, now
from assistant.models import (Connector, Conversation, Draft, Message, MessageEvent, Outbox,
                              Permission, SendAttempt, User, Workspace)
from assistant.webhooks import MAX_WEBHOOK_BYTES, router


@pytest.fixture
def hook_app():
    settings = Settings(environment="test", database_url="sqlite:///:memory:",
                        encryption_key=Fernet.generate_key().decode(),
                        whatsapp_app_secret="test-app-secret", whatsapp_verify_token="test-verify")
    engine, factory = make_database(settings)
    Base.metadata.create_all(engine)
    with factory() as db:
        owner = User(subject="webhook-test", email="owner@example.test")
        db.add(owner)
        db.flush()
        workspace = Workspace(owner_id=owner.id, name="Webhook")
        db.add(workspace)
        db.flush()
        connector = Connector(workspace_id=workspace.id, provider="whatsapp_cloud", account_id="12345",
                              owner_sender_id="99999", status="connected", created_at=now() - timedelta(minutes=10))
        db.add(connector)
        db.flush()
        conversation = Conversation(workspace_id=workspace.id, connector_id=connector.id,
                                    provider_chat_id="11111", title="Contact")
        db.add(conversation)
        db.flush()
        permission = Permission(workspace_id=workspace.id, conversation_id=conversation.id,
                                read=True, retain=True)
        db.add(permission)
        db.commit()
        ids = SimpleNamespace(owner=owner.id, workspace=workspace.id, connector=connector.id,
                              conversation=conversation.id, permission=permission.id)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(router)
    with TestClient(app) as client:
        yield SimpleNamespace(app=app, client=client, db=factory, settings=settings, ids=ids)
    engine.dispose()


def envelope(*, messages=None, statuses=None, account="12345"):
    value = {"metadata": {"phone_number_id": account}}
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [
        {"changes": [{"field": "messages", "value": value}]}
    ]}


def incoming(**changes):
    return {"id": "wamid.incoming", "from": "11111", "timestamp": "1750000000",
            "type": "text", "text": {"body": "Hello"}, **changes}


def signed_post(hook, payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(hook.settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hook.client.post("/webhooks/whatsapp", content=raw,
                            headers={"x-hub-signature-256": signature, "content-type": "application/json"})


def test_verification_returns_plain_challenge_and_rejects_wrong_token(hook_app):
    response = hook_app.client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "test-verify", "hub.challenge": "123456"
    })
    assert response.status_code == 200
    assert response.text == "123456"
    assert response.headers["content-type"].startswith("text/plain")
    rejected = hook_app.client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "123456"
    })
    assert rejected.status_code == 403


def test_unsigned_payload_rejected_and_unconfigured_webhook_fails_closed(hook_app):
    response = hook_app.client.post("/webhooks/whatsapp", json=envelope(messages=[incoming()]))
    assert response.status_code == 401
    hook_app.settings.whatsapp_app_secret = ""
    assert hook_app.client.post("/webhooks/whatsapp", json={}).status_code == 503


def test_signature_covers_raw_body_and_size_is_bounded(hook_app):
    raw = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = "sha256=" + hmac.new(b"test-app-secret", raw, hashlib.sha256).hexdigest()
    assert hook_app.client.post("/webhooks/whatsapp", content=raw + b" ", headers={
        "x-hub-signature-256": signature
    }).status_code == 401
    assert hook_app.client.post("/webhooks/whatsapp", content=b"x" * (MAX_WEBHOOK_BYTES + 1)).status_code == 413


def test_trusted_mapping_overrides_supplied_tenant_and_normalizes_message(hook_app, monkeypatch):
    from assistant import messaging

    accepted = []
    monkeypatch.setattr(messaging, "CanonicalEvent", lambda **fields: SimpleNamespace(**fields))
    monkeypatch.setattr(messaging, "ingest_event", lambda db, value: accepted.append(value) or {"status": "accepted"})
    payload = envelope(messages=[incoming(context={"id": "wamid.previous"})])
    payload["workspace_id"] = "another-tenant"
    payload["entry"][0]["changes"][0]["value"]["connector_id"] = "another-connector"
    response = signed_post(hook_app, payload)
    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    event = accepted[0]
    assert event.workspace_id == hook_app.ids.workspace
    assert event.connector_id == hook_app.ids.connector
    assert event.conversation_id == hook_app.ids.conversation
    assert event.origin == "live"
    assert event.direction == "inbound"
    assert event.content == {"type": "text", "text": "Hello"}
    assert event.reply_to == "wamid.previous"
    assert event.provider_timestamp.tzinfo is not None
    assert len(event.event_id) < 200


@pytest.mark.parametrize("case", ["unknown_account", "unknown_chat", "no_retain", "expired"])
def test_unconsented_and_unselected_messages_are_not_ingested(hook_app, monkeypatch, case):
    from assistant import messaging

    accepted = []
    monkeypatch.setattr(messaging, "CanonicalEvent", lambda **fields: SimpleNamespace(**fields))
    monkeypatch.setattr(messaging, "ingest_event", lambda db, value: accepted.append(value) or {"status": "accepted"})
    with hook_app.db() as db:
        grant = db.get(Permission, hook_app.ids.permission)
        if case == "no_retain":
            grant.retain = False
        if case == "expired":
            grant.expires_at = now() - timedelta(seconds=1)
        db.commit()
    response = signed_post(hook_app, envelope(
        account="unknown" if case == "unknown_account" else "12345",
        messages=[incoming(**({"from": "22222"} if case == "unknown_chat" else {}))],
    ))
    assert response.status_code == 200
    assert response.json()["accepted"] == 0
    assert accepted == []


@pytest.mark.parametrize("changes", [
    {"timestamp": True}, {"timestamp": "not-a-date"}, {"timestamp": "999999999999"},
    {"id": ""}, {"text": {"body": 1}}, {"context": []},
])
def test_malformed_supported_fields_return_bad_request(hook_app, changes):
    response = signed_post(hook_app, envelope(messages=[incoming(**changes)]))
    assert response.status_code == 400


def add_attempt(hook):
    with hook.db() as db:
        draft = Draft(workspace_id=hook.ids.workspace, conversation_id=hook.ids.conversation,
                      recipient_id="11111", text="A reply", model_version="test", conversation_revision=0,
                      control_epoch=0, permission_version=1, pause_generation=0, connector_fence=1,
                      content_hash="0" * 64, status="uncertain")
        db.add(draft)
        db.flush()
        attempt = SendAttempt(workspace_id=hook.ids.workspace, draft_id=draft.id,
                              conversation_id=hook.ids.conversation, provider_message_id="wamid.sent",
                              status="uncertain", content_hash="0" * 64, connector_fence=1)
        db.add(attempt)
        db.commit()
        return attempt.id, draft.id


def receipt(state, **changes):
    return {"id": "wamid.sent", "recipient_id": "11111", "status": state,
            "timestamp": "1750000001", **changes}


def test_receipts_resolve_uncertainty_without_regressing_delivery(hook_app):
    attempt_id, draft_id = add_attempt(hook_app)
    assert signed_post(hook_app, envelope(statuses=[receipt("sent")])).json()["receipts"] == 1
    with hook_app.db() as db:
        assert db.get(SendAttempt, attempt_id).status == "accepted"
        assert db.get(Draft, draft_id).status == "accepted"
    assert signed_post(hook_app, envelope(statuses=[receipt("delivered")])).json()["receipts"] == 1
    response = signed_post(hook_app, envelope(statuses=[receipt("sent"), receipt("failed")]))
    assert response.json()["receipts"] == 0
    with hook_app.db() as db:
        assert db.get(SendAttempt, attempt_id).status == "delivered"
        assert db.get(Draft, draft_id).status == "delivered"


def test_receipts_cannot_affect_other_account_or_recipient(hook_app):
    attempt_id, _ = add_attempt(hook_app)
    with hook_app.db() as db:
        other = Connector(workspace_id=hook_app.ids.workspace, provider="whatsapp_cloud", account_id="67890",
                          owner_sender_id="99998", status="connected")
        db.add(other)
        db.flush()
        db.add(Conversation(workspace_id=hook_app.ids.workspace, connector_id=other.id,
                            provider_chat_id="11111", title="Same recipient, other account"))
        db.commit()
    assert signed_post(hook_app, envelope(account="different", statuses=[receipt("delivered")])).status_code == 200
    assert signed_post(hook_app, envelope(account="67890", statuses=[receipt("delivered")])).status_code == 200
    assert signed_post(hook_app, envelope(statuses=[receipt("delivered", recipient_id="22222")])).status_code == 200
    with hook_app.db() as db:
        assert db.get(SendAttempt, attempt_id).status == "uncertain"


def test_failed_receipt_does_not_reopen_on_stale_acceptance(hook_app):
    attempt_id, draft_id = add_attempt(hook_app)
    response = signed_post(hook_app, envelope(statuses=[receipt("failed", errors=[{"code": 131026}])]))
    assert response.status_code == 200
    assert response.json()["receipts"] == 1
    assert signed_post(hook_app, envelope(statuses=[receipt("sent")])).json()["receipts"] == 0
    with hook_app.db() as db:
        assert db.get(SendAttempt, attempt_id).status == "failed"
        assert db.get(SendAttempt, attempt_id).error_code == "131026"
        assert db.get(Draft, draft_id).status == "failed"


def test_malformed_later_change_rolls_back_prior_receipt(hook_app):
    attempt_id, draft_id = add_attempt(hook_app)
    payload = envelope(statuses=[receipt("sent")])
    payload["entry"][0]["changes"].extend(envelope(messages=[incoming(timestamp="bad")])["entry"][0]["changes"])
    assert signed_post(hook_app, payload).status_code == 400
    with hook_app.db() as db:
        assert db.get(SendAttempt, attempt_id).status == "uncertain"
        assert db.get(Draft, draft_id).status == "uncertain"


def test_real_inbound_is_durable_deduplicated_and_never_auto_sends(hook_app):
    timestamp = str(int(now().timestamp()))
    payload = envelope(messages=[incoming(timestamp=timestamp)])
    assert signed_post(hook_app, payload).status_code == 200
    assert signed_post(hook_app, payload).status_code == 200
    with hook_app.db() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 1
        assert db.scalar(select(func.count()).select_from(MessageEvent)) == 1
        row = db.scalar(select(Message))
        assert row.text == "Hello"
        assert row.workspace_id == hook_app.ids.workspace
        assert row.origin == "live"
        assert row.author_kind == "contact_human"
        assert row.excluded_from_learning
        conversation = db.get(Conversation, hook_app.ids.conversation)
        assert conversation.revision == 1
        assert aware(conversation.last_inbound_at).timestamp() == int(timestamp)
        outbox = db.scalar(select(Outbox))
        assert outbox.kind == "message.accepted"
        assert outbox.payload["live_eligible"] is True
        assert db.scalar(select(func.count()).select_from(Draft)) == 0
        assert db.scalar(select(func.count()).select_from(SendAttempt)) == 0


@pytest.mark.parametrize("case", ["too_old", "before_connection", "too_far_future"])
def test_real_stale_or_pre_connection_events_cannot_open_send_window(hook_app, case):
    timestamp = now()
    if case == "too_old":
        timestamp -= timedelta(minutes=5)
    elif case == "too_far_future":
        timestamp += timedelta(minutes=5)
    elif case == "before_connection":
        with hook_app.db() as db:
            db.get(Connector, hook_app.ids.connector).created_at = now() + timedelta(minutes=1)
            db.commit()
    response = signed_post(hook_app, envelope(messages=[incoming(timestamp=str(int(timestamp.timestamp())))]))
    assert response.status_code == 200
    with hook_app.db() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 1
        assert db.get(Conversation, hook_app.ids.conversation).last_inbound_at is None
        assert db.scalar(select(Outbox)).payload["live_eligible"] is False
        assert db.scalar(select(func.count()).select_from(SendAttempt)) == 0


def test_real_webhook_message_can_become_review_only_draft(hook_app):
    from assistant.auth import get_current_user
    from assistant.intelligence import router as intelligence_router

    hook_app.app.include_router(intelligence_router)
    hook_app.settings.model_provider = "mock"
    with hook_app.db() as db:
        owner = db.get(User, hook_app.ids.owner)
        db.get(Permission, hook_app.ids.permission).draft = True
        db.commit()
    hook_app.app.dependency_overrides[get_current_user] = lambda: owner
    assert signed_post(hook_app, envelope(messages=[incoming(timestamp=str(int(now().timestamp())))])).status_code == 200
    result = hook_app.client.post(f"/conversations/{hook_app.ids.conversation}/drafts", json={})
    assert result.status_code == 201
    assert result.json()["status"] == "needs_approval"
    assert result.json()["development_mock"] is True
    with hook_app.db() as db:
        assert db.scalar(select(func.count()).select_from(Draft)) == 1
        assert db.scalar(select(func.count()).select_from(SendAttempt)) == 0


def test_batched_workspaces_hold_sorted_guards_through_commit(hook_app, monkeypatch):
    from assistant import messaging

    with hook_app.db() as db:
        workspace = Workspace(owner_id=hook_app.ids.owner, name="Other workspace")
        db.add(workspace)
        db.flush()
        connector = Connector(workspace_id=workspace.id, provider="whatsapp_cloud", account_id="67890",
                              owner_sender_id="99998", status="connected", created_at=now() - timedelta(minutes=10))
        db.add(connector)
        db.flush()
        conversation = Conversation(workspace_id=workspace.id, connector_id=connector.id,
                                    provider_chat_id="11111", title="Other contact")
        db.add(conversation)
        db.flush()
        db.add(Permission(workspace_id=workspace.id, conversation_id=conversation.id, read=True, retain=True))
        db.commit()
        expected = {hook_app.ids.workspace, workspace.id}
    held = set()
    acquisitions = []
    committed = []
    original = messaging.submit_guard

    @contextmanager
    def observed_guard(workspace_id):
        with original(workspace_id):
            acquisitions.append(workspace_id)
            held.add(workspace_id)
            try:
                yield
            finally:
                held.remove(workspace_id)

    def after_commit(session):
        committed.append(set(held))

    monkeypatch.setattr(messaging, "submit_guard", observed_guard)
    event.listen(hook_app.db.class_, "after_commit", after_commit)
    try:
        payload = envelope(messages=[incoming(timestamp=str(int(now().timestamp())))])
        other = envelope(account="67890", messages=[incoming(timestamp=str(int(now().timestamp())))])
        payload["entry"][0]["changes"].extend(other["entry"][0]["changes"])
        payload["workspace_id"] = "untrusted-workspace"
        response = signed_post(hook_app, payload)
        assert response.status_code == 200
        assert response.json()["accepted"] == 2
        assert acquisitions == sorted(expected)
        assert committed == [expected]
        assert held == set()
    finally:
        event.remove(hook_app.db.class_, "after_commit", after_commit)


def test_connector_disconnect_while_waiting_is_rechecked_under_guard(hook_app, monkeypatch):
    from assistant import messaging

    original = messaging.submit_guard

    @contextmanager
    def disconnect_before_acquire(workspace_id):
        with hook_app.db() as db:
            db.get(Connector, hook_app.ids.connector).status = "disconnected"
            db.commit()
        with original(workspace_id):
            yield

    monkeypatch.setattr(messaging, "submit_guard", disconnect_before_acquire)
    response = signed_post(hook_app, envelope(messages=[incoming(timestamp=str(int(now().timestamp())))]))
    assert response.status_code == 200
    assert response.json()["accepted"] == 0
    with hook_app.db() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 0
