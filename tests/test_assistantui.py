from datetime import timedelta

import pytest
from sqlalchemy import select

from assistant.config import Settings
from assistant.db import now
from assistant.models import Conversation, Permission
from conftest import create_chat, login
from test_messaging import event, receive


def test_public_client_configuration_never_exposes_secrets(app, client):
    app.state.settings.google_client_id = "public-google-client-id"
    app.state.settings.whatsapp_access_token = "secret-meta-token"
    app.state.settings.model_api_key = "secret-model-key"
    response = client.get("/v1/auth/config")
    assert response.status_code == 200
    assert response.json()["google"]["client_id"] == "public-google-client-id"
    assert response.json()["providers"]["whatsapp_personal"]["pairing_supported"] is False
    assert "secret-meta-token" not in response.text and "secret-model-key" not in response.text
    assert "encryption_key" not in response.text and "database_url" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_ui_bootstrap_requires_auth_and_owner_scope(client, app):
    assert client.get("/v1/ui/bootstrap").status_code == 401
    login(client)
    chat = create_chat(client)
    response = client.get("/v1/ui/bootstrap", params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["workspace"]["id"] == chat["workspace"]["id"]
    assert snapshot["conversations"][0]["provider_chat_id"] == "15550001234"
    assert snapshot["conversations"][0]["permissions"]["send"] is True
    assert snapshot["messages"] == [] and snapshot["simulation"] is True
    login(client, "intruder@example.test")
    assert client.get("/v1/ui/bootstrap", params={"workspace_id": chat["workspace"]["id"]}).status_code == 404


def test_ui_deep_link_resolver_is_owner_bound_and_respects_read_revocation(owner_client, chat):
    conversation_id = chat["conversation"]["id"]
    result = owner_client.get("/v1/ui/resolve", params={"kind": "conversation", "id": conversation_id})
    assert result.status_code == 200
    assert result.json()["object"]["id"] == conversation_id
    assert result.json()["conversation"]["provider_chat_id"] == "15550001234"
    connection = owner_client.get("/v1/ui/resolve", params={"kind": "connection", "id": chat["connector"]["id"]})
    assert connection.json()["object"]["id"] == chat["connector"]["id"]
    task = owner_client.post("/tasks", json={"workspace_id": chat["workspace"]["id"],
                            "conversation_id": conversation_id, "title": "Owner task"}).json()
    assert owner_client.get("/v1/ui/resolve", params={"kind": "task", "id": task["id"]}).status_code == 200
    owner_client.put(f"/conversations/{conversation_id}/permissions", json={"read": False})
    assert owner_client.get("/v1/ui/resolve", params={"kind": "conversation", "id": conversation_id}).status_code == 403
    assert owner_client.get("/v1/ui/resolve", params={"kind": "task", "id": task["id"]}).status_code == 403
    login(owner_client, "another-reference-owner@example.test")
    assert owner_client.get("/v1/ui/resolve", params={"kind": "connection", "id": chat["connector"]["id"]}).status_code == 404
    assert owner_client.get("/v1/ui/resolve", params={"kind": "task", "id": task["id"]}).status_code == 404


def test_ui_deep_link_resolves_local_job_without_fabricated_chat(owner_client, chat):
    due = now() + timedelta(minutes=10)
    job = owner_client.post("/jobs", json={"workspace_id": chat["workspace"]["id"],
          "idempotency_key": "local-owner-reminder-1", "purpose": "Remember", "action_kind": "REMINDER",
          "content": "Read notes", "due_at": due.isoformat(), "expires_at": (due + timedelta(hours=1)).isoformat()})
    assert job.status_code == 201
    result = owner_client.get("/v1/ui/resolve", params={"kind": "job", "id": job.json()["id"]})
    assert result.status_code == 200 and result.json()["object"]["conversation_id"] is None


def test_ui_draft_deep_link_reads_exact_owner_text_and_blocks_other_owners(owner_client, chat):
    draft = owner_client.post(f"/v1/conversations/{chat['conversation']['id']}/owner-drafts",
                             json={"text": "Exact text for review"}).json()
    result = owner_client.get("/v1/ui/resolve", params={"kind": "draft", "id": draft["id"]})
    assert result.status_code == 200 and result.json()["object"] == draft
    assert result.json()["conversation_id"] == chat["conversation"]["id"]
    assert result.json()["object"]["status"] == "needs_approval"
    owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions", json={"read": False})
    assert owner_client.get("/v1/ui/resolve", params={"kind": "draft", "id": draft["id"]}).status_code == 403
    login(owner_client, "foreign-draft-reviewer@example.test")
    assert owner_client.get("/v1/ui/resolve", params={"kind": "draft", "id": draft["id"]}).status_code == 404


def test_bootstrap_empty_owner_is_valid(owner_client):
    snapshot = owner_client.get("/v1/ui/bootstrap").json()
    assert snapshot["workspace"] is None and snapshot["conversations"] == []


def test_bootstrap_filters_revoked_and_expired_read_scope(app, owner_client, chat):
    with app.state.session_factory() as db:
        permission = db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
        permission.expires_at = now() - timedelta(seconds=1)
        db.commit()
    snapshot = owner_client.get("/v1/ui/bootstrap").json()
    assert snapshot["conversations"] == [] and snapshot["memories"] == [] and snapshot["drafts"] == []


def test_bootstrap_conversation_pages_are_bounded_and_stable(app, owner_client, chat):
    with app.state.session_factory() as db:
        for index in range(5):
            conversation = Conversation(workspace_id=chat["workspace"]["id"], connector_id=chat["connector"]["id"],
                                        provider_chat_id=f"page-{index}", title=f"Person {index}")
            db.add(conversation)
            db.flush()
            db.add(Permission(workspace_id=conversation.workspace_id, conversation_id=conversation.id, read=True))
        db.commit()
    pages, cursor = [], None
    for _ in range(3):
        params = {"conversation_limit": 2}
        if cursor:
            params["conversation_cursor"] = cursor
        result = owner_client.get("/v1/ui/bootstrap", params=params).json()
        assert len(result["conversations"]) == 2
        pages.extend(row["id"] for row in result["conversations"])
        cursor = result["pagination"]["conversation_next_cursor"]
    assert len(set(pages)) == 6 and cursor is None
    assert owner_client.get("/v1/ui/bootstrap", params={"conversation_limit": 101}).status_code == 422
    assert owner_client.get("/v1/ui/bootstrap", params={"conversation_cursor": "bad-cursor"}).status_code == 422


def test_browser_csrf_recovery_rotates_and_rejects_cross_origin(owner_client, chat):
    prior = owner_client.headers["X-CSRF-Token"]
    response = owner_client.get("/v1/auth/csrf", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403
    assert owner_client.get("/v1/auth/csrf").status_code == 403
    response = owner_client.get("/v1/auth/csrf", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    response = owner_client.get("/v1/auth/csrf", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200 and response.json()["csrf_token"] != prior
    assert owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]}).status_code == 403
    owner_client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200


@pytest.mark.parametrize("local,timezone,policy,code", [
    ("2026-03-08T02:30", "America/New_York", "reject", "DST_GAP"),
    ("2026-11-01T01:30", "America/New_York", "reject", "DST_AMBIGUOUS"),
    ("2026-11-01T01:30", "Invalid/Timezone", "reject", "INVALID_TIMEZONE"),
])
def test_schedule_resolver_reports_dst_errors(owner_client, local, timezone, policy, code):
    response = owner_client.post("/v1/schedules/resolve-time", json={"local_datetime": local,
                                "timezone": timezone, "ambiguity_policy": policy})
    assert response.status_code == 422 and response.json()["detail"]["reason_code"] == code


def test_schedule_resolver_uses_selected_timezone_not_browser_offset(owner_client):
    response = owner_client.post("/v1/schedules/resolve-time", json={"local_datetime": "2026-10-07T09:00",
                                "timezone": "Asia/Kolkata"})
    assert response.json()["due_at"] == "2026-10-07T03:30:00+00:00"
    assert response.json()["utc_offset"] == "+0530"


def test_owner_draft_creates_exact_text_without_model_or_send(owner_client, chat, app):
    path = f"/v1/conversations/{chat['conversation']['id']}/owner-drafts"
    headers = {"Idempotency-Key": "native-owner-draft-1"}
    current = owner_client.get("/v1/ui/bootstrap").json()["conversations"][0]
    body = {"text": "Owner chose these exact words.", "expected_revision": current["revision"]}
    response = owner_client.post(path, json=body, headers=headers)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["text"] == body["text"] and draft["status"] == "needs_approval"
    assert owner_client.post(path, json=body, headers=headers).json()["id"] == draft["id"]
    assert owner_client.post(path, json={"text": "Different words"}, headers=headers).status_code == 409
    assert owner_client.post(path, json={"text": "Stale", "expected_revision": 999}).status_code == 409
    assert owner_client.post(f"/v1/drafts/{draft['id']}/approve", json={"content_hash": draft["content_hash"]}).status_code == 200
    assert owner_client.post(f"/v1/drafts/{draft['id']}/dispatch").json()["status"] == "accepted"


def test_owner_draft_scope_and_no_implicit_send_permission(owner_client, chat):
    owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions",
                     json={"read": True, "retain": True, "draft": True, "send": False})
    draft = owner_client.post(f"/v1/conversations/{chat['conversation']['id']}/owner-drafts", json={"text": "Draft only"})
    assert draft.status_code == 201
    assert owner_client.post(f"/v1/drafts/{draft.json()['id']}/approve",
                             json={"content_hash": draft.json()["content_hash"]}).status_code == 403
    login(owner_client, "other-owner@example.test")
    assert owner_client.post(f"/v1/conversations/{chat['conversation']['id']}/owner-drafts", json={"text": "No access"}).status_code == 404


def test_owner_edits_use_optional_compare_and_swap(owner_client, chat):
    body = event(chat, content={"type": "text", "text": "Remember this"})
    source = receive(owner_client, body).json()
    memory = owner_client.post(f"/conversations/{chat['conversation']['id']}/memories",
        json={"text": "A scoped fact", "source_message_ids": [source["message_id"]], "status": "confirmed"}).json()
    path = f"/memories/{memory['id']}"
    assert owner_client.patch(path, json={"text": "Changed fact", "expected_version": 999}).status_code == 409
    assert owner_client.patch(path, json={"expected_version": memory["version"]}).status_code == 422
    assert owner_client.patch(path, json={"text": "Changed fact", "expected_version": memory["version"]}).status_code == 200
    style_path = f"/conversations/{chat['conversation']['id']}/style-profile"
    profile = owner_client.patch(style_path, json={"owner_rules": ["Be concise"], "expected_version": 0}).json()
    assert profile["version"] == 1
    assert owner_client.patch(style_path, json={"reviewed": True, "expected_version": 0}).status_code == 409
    assert owner_client.patch(style_path, json={"reviewed": True, "expected_version": 1}).status_code == 200
    policy = owner_client.get("/privacy/retention", params={"workspace_id": chat["workspace"]["id"]}).json()
    assert owner_client.put("/privacy/retention", json={"workspace_id": chat["workspace"]["id"],
                                  "raw_days": 31, "expected_version": 999}).status_code == 409
    assert owner_client.put("/privacy/retention", json={"workspace_id": chat["workspace"]["id"],
                                  "raw_days": 31, "expected_version": policy["version"]}).status_code == 200


@pytest.mark.parametrize("prefix", ["postgres://", "postgresql://", "postgresql+psycopg://"])
def test_cloud_database_uri_selects_installed_driver(prefix, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from cryptography.fernet import Fernet
    settings = Settings(_env_file=None, database_url=prefix + "u:p%40ss@db.example:5432/app?sslmode=require",
                        encryption_key=Fernet.generate_key().decode())
    assert settings.prepare().database_url == "postgresql+psycopg://u:p%40ss@db.example:5432/app?sslmode=require"
