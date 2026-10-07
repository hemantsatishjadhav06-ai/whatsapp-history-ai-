"""Actual-app regression checks for private data boundaries and safe failures."""

from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from assistant.config import Settings
from assistant.db import Base, now, uid
from assistant.main import create_app
from assistant.models import Connector, Message, Workspace
from conftest import create_chat, login


def seed_private_message(db, chat, value="PRIVATE MESSAGE 4921"):
    message = Message(workspace_id=chat["workspace"]["id"], connector_id=chat["connector"]["id"],
                      conversation_id=chat["conversation"]["id"], provider_message_id="private-fixture-message",
                      sender_id="Private Contact", direction="inbound", author_kind="contact_human",
                      origin="history", text=value, provider_timestamp=now())
    db.add(message)
    db.commit()
    return message


@pytest.mark.parametrize("length", ["not-a-number", "-1"])
def test_invalid_content_length_returns_client_error(client, length):
    response = client.get("/health/live", headers={"Content-Length": length})
    assert response.status_code == 400


def test_request_limit_checks_stream_even_without_declared_length(client, app):
    app.state.settings.max_import_bytes = 1
    cap = app.state.settings.max_import_bytes * 6 + 65536

    def chunks():
        yield b"x" * cap
        yield b"extra"

    response = client.post("/auth/dev", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413


@pytest.mark.parametrize("timezone", ["", "/etc/passwd", "../UTC", "Invalid/Zone"])
def test_invalid_timezone_is_rejected_without_server_error(owner_client, timezone):
    response = owner_client.post("/workspaces", json={"name": "Owner", "timezone": timezone})
    assert response.status_code == 422


def test_another_engine_key_cannot_change_existing_database_encryption(owner_client, chat, db, app, tmp_path):
    message = seed_private_message(db, chat)
    ciphertext = db.execute(text("SELECT text FROM messages WHERE id = :message"), {"message": message.id}).scalar()
    assert ciphertext != message.text
    assert "PRIVATE MESSAGE" not in ciphertext
    second_app = create_app(Settings(_env_file=None, environment="test",
                            database_url=f"sqlite:///{tmp_path / 'second.db'}",
                            encryption_key=Fernet.generate_key().decode(), allow_dev_auth=True,
                            session_secure=False, model_provider="mock"))
    try:
        Base.metadata.create_all(second_app.state.engine)
        response = owner_client.get(f"/conversations/{chat['conversation']['id']}/messages")
        assert response.status_code == 200
        assert response.json()[0]["text"] == "PRIVATE MESSAGE 4921"
        new_message = seed_private_message_in_original_engine(app, chat)
        with app.state.session_factory() as fresh:
            assert fresh.get(Message, new_message).text == "ORIGINAL ENGINE CONTENT"
    finally:
        second_app.state.engine.dispose()


def seed_private_message_in_original_engine(app, chat):
    with app.state.session_factory() as db:
        message = Message(workspace_id=chat["workspace"]["id"], connector_id=chat["connector"]["id"],
                          conversation_id=chat["conversation"]["id"], provider_message_id="second-private-message",
                          sender_id="Private Contact", direction="inbound", author_kind="contact_human",
                          origin="history", text="ORIGINAL ENGINE CONTENT", provider_timestamp=now())
        db.add(message)
        db.commit()
        return message.id


def test_other_user_cannot_read_modify_export_or_delete_workspace(owner_client, chat, db):
    seed_private_message(db, chat)
    workspace = chat["workspace"]["id"]
    connector = chat["connector"]["id"]
    conversation = chat["conversation"]["id"]
    login(owner_client, "other-owner@example.test")
    assert owner_client.get("/workspaces").json() == []
    requests = [
        ("get", f"/connectors/{connector}", {}),
        ("delete", f"/connectors/{connector}", {}),
        ("post", f"/connectors/{connector}/verify", {}),
        ("get", "/conversations", {"params": {"workspace_id": workspace}}),
        ("get", f"/conversations/{conversation}/messages", {}),
        ("put", f"/conversations/{conversation}/permissions", {"json": {"read": True}}),
        ("post", f"/conversations/{conversation}/takeover", {}),
        ("post", f"/conversations/{conversation}/resume", {}),
        ("delete", f"/conversations/{conversation}/data", {}),
        ("get", "/activity", {"params": {"workspace_id": workspace}}),
        ("post", "/data-export", {"params": {"workspace_id": workspace}}),
        ("delete", "/account-data", {"params": {"workspace_id": workspace}}),
        ("post", "/pause-all", {"params": {"workspace_id": workspace}}),
        ("post", "/resume-all", {"params": {"workspace_id": workspace}}),
    ]
    for method, path, kwargs in requests:
        response = getattr(owner_client, method)(path, **kwargs)
        assert response.status_code == 404, (method, path, response.text)
        assert "PRIVATE MESSAGE" not in response.text
    login(owner_client)
    response = owner_client.get(f"/conversations/{conversation}/messages")
    assert response.json()[0]["text"] == "PRIVATE MESSAGE 4921"
    db.expire_all()
    assert db.get(Workspace, workspace).paused is False


def test_csrf_failure_prevents_workspace_and_conversation_mutations(owner_client, chat, db):
    csrf = owner_client.headers.pop("X-CSRF-Token")
    workspace = chat["workspace"]["id"]
    conversation = chat["conversation"]["id"]
    requests = [
        ("post", "/workspaces", {"json": {"name": "Unapproved workspace"}}),
        ("post", f"/conversations/{conversation}/takeover", {}),
        ("put", f"/conversations/{conversation}/permissions", {"json": {}}),
        ("delete", f"/conversations/{conversation}/data", {}),
        ("post", "/pause-all", {"params": {"workspace_id": workspace}}),
        ("delete", "/account-data", {"params": {"workspace_id": workspace}}),
    ]
    for method, path, kwargs in requests:
        assert getattr(owner_client, method)(path, **kwargs).status_code == 403
    owner_client.headers["X-CSRF-Token"] = csrf
    db.expire_all()
    assert len(db.scalars(select(Workspace)).all()) == 1
    assert db.get(Workspace, workspace).paused is False


def test_private_direct_message_cannot_become_group_memory(owner_client, chat, db):
    private = seed_private_message(db, chat)
    group = create_chat(owner_client, kind="group", account="group-account", recipient="group-chat")
    response = owner_client.post(f"/conversations/{group['conversation']['id']}/memories",
                                 json={"text": "Private fact from a direct conversation",
                                       "source_message_ids": [private.id], "status": "confirmed"})
    assert response.status_code == 422
    assert owner_client.get("/memories", params={"conversation_id": group["conversation"]["id"]}).json() == []


def test_reconnected_conversation_needs_explicit_resume_before_drafting(owner_client, chat, app):
    connector = chat["connector"]["id"]
    conversation = chat["conversation"]["id"]
    assert owner_client.delete(f"/connectors/{connector}").status_code == 200
    with app.state.session_factory() as db:
        row = db.get(Connector, connector)
        row.status = "connected"
        row.lease_expires_at = now() + timedelta(hours=1)
        db.commit()
    response = owner_client.post(f"/conversations/{conversation}/drafts", json={})
    assert response.status_code == 409
    assert owner_client.post(f"/conversations/{conversation}/resume").status_code == 200
    assert owner_client.post(f"/conversations/{conversation}/drafts", json={}).status_code == 201


def test_non_ascii_internal_authorization_fails_without_server_error(client):
    response = client.post("/internal/scheduled-intents/run-due",
                           headers=[(b"Authorization", b"Bearer \xc3\xa9")])
    assert response.status_code == 401


def test_approval_requires_hexadecimal_content_hash(owner_client, chat):
    draft = owner_client.post(f"/conversations/{chat['conversation']['id']}/drafts", json={}).json()
    response = owner_client.post(f"/drafts/{draft['id']}/approve", json={"content_hash": "\u00e9" * 64})
    assert response.status_code == 422


def test_late_edit_cannot_restore_content_after_provider_deletion(owner_client, chat, app):
    provider_id = uid()
    body = {"event_id": uid(), "connector_id": chat["connector"]["id"],
            "conversation_id": chat["conversation"]["id"], "provider_message_id": provider_id,
            "sender_id": "Contact", "direction": "inbound", "origin": "live",
            "provider_timestamp": now().isoformat(), "content": {"text": "Source before deletion"}}
    internal = {"Authorization": "Bearer test-internal-service-token"}
    created = owner_client.post("/internal/connector-events", json=body, headers=internal)
    assert created.status_code == 200
    message_id = created.json()["message_id"]
    body.update(event_id=uid(), event_type="message.deleted", source_revision=2, content={"text": ""})
    assert owner_client.post("/internal/connector-events", json=body, headers=internal).status_code == 200
    body.update(event_id=uid(), event_type="message.edited", source_revision=3,
                content={"text": "DELETED CONTENT MUST NOT RETURN"})
    assert owner_client.post("/internal/connector-events", json=body, headers=internal).status_code == 200
    with app.state.session_factory() as db:
        message = db.get(Message, message_id)
        assert message.deleted
        assert message.text == ""
    assert owner_client.get(f"/conversations/{chat['conversation']['id']}/messages").json() == []
