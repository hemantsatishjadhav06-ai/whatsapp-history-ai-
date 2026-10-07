from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from assistant.db import now
from assistant.models import Conversation, Message, Permission, Suppression, Task
from assistant.tasks import invalidate_task_sources
from conftest import create_chat, login


def seed_message(db, chat, text_value="Please call me tomorrow", *, deleted=False):
    row = Message(workspace_id=chat["workspace"]["id"], connector_id=chat["connector"]["id"],
                  conversation_id=chat["conversation"]["id"], provider_message_id=str(now().timestamp()),
                  sender_id="Client", direction="inbound", author_kind="contact_human", origin="history",
                  text=text_value, provider_timestamp=now(), deleted=deleted)
    db.add(row)
    db.commit()
    return row


def task_create(client, chat, **fields):
    body = {"workspace_id": chat["workspace"]["id"], "title": "Owner follow-up"}
    body.update(fields)
    return client.post("/tasks", json=body)


def linked_task(client, chat, message, **fields):
    return task_create(client, chat, conversation_id=chat["conversation"]["id"],
                       source_message_ids=[message.id], **fields)


def related_chat(client, chat, *, recipient="second-contact", kind="contact", read=True):
    response = client.post("/conversations", json={"connector_id": chat["connector"]["id"],
                                                  "provider_chat_id": recipient, "title": recipient, "kind": kind})
    assert response.status_code == 201, response.text
    conversation = response.json()
    client.put(f"/conversations/{conversation['id']}/permissions",
               json={"read": read, "retain": True, "learn": True, "draft": True})
    return {**chat, "conversation": conversation}


def test_manual_task_no_inferred_date_and_encrypted_title(owner_client, chat, db):
    result = task_create(owner_client, chat, title="Call Client tomorrow")
    assert result.status_code == 201, result.text
    task = result.json()
    assert task["due_at"] is None and task["status"] == "pending"
    assert task["timezone"] == "Asia/Kolkata" and task["created_by"] == "owner"
    assert task["source_message_ids"] == [] and not task["notifications_enabled"]
    stored = db.execute(text("SELECT title FROM tasks WHERE id=:id"), {"id": task["id"]}).scalar_one()
    assert "Call Client" not in stored


def test_explicit_task_due_offset_preserved_as_utc_and_valid_timezone(owner_client, chat):
    result = task_create(owner_client, chat, due_at="2026-10-10T09:30:00+05:30", timezone="Asia/Kolkata")
    assert result.status_code == 201
    assert result.json()["due_at"] == "2026-10-10T04:00:00+00:00"


@pytest.mark.parametrize("fields", [
    {"title": " "}, {"title": "x" * 1001}, {"timezone": "Not/AZone"}, {"timezone": "/tmp/untrusted"},
    {"due_at": "2026-10-10T09:30:00"}, {"due_at": 1791626400}, {"due_at": "1791626400"},
    {"status": "completed"}, {"tool": "send_message"},
])
def test_task_payload_rejects_invalid_or_unauthorized_fields(owner_client, chat, fields):
    assert task_create(owner_client, chat, **fields).status_code == 422


def test_source_task_exact_conversation_revisions_no_learning_requirement(owner_client, db):
    chat = create_chat(owner_client, learn=False)
    source = seed_message(db, chat)
    response = linked_task(owner_client, chat, source)
    assert response.status_code == 201, response.text
    assert response.json()["source_revision"] == {source.id: 1}
    assert response.json()["conversation_id"] == chat["conversation"]["id"]


def test_task_sources_cannot_cross_workspace_contact_or_group_scope(owner_client, chat, db):
    own = seed_message(db, chat)
    group = related_chat(owner_client, chat, recipient="private-group", kind="group")
    other_source = seed_message(db, group, "Private group message")
    assert linked_task(owner_client, chat, other_source).status_code == 422
    assert task_create(owner_client, chat, source_message_ids=[own.id]).status_code == 422
    assert task_create(owner_client, chat, conversation_id=chat["conversation"]["id"],
                       source_message_ids=[own.id, own.id]).status_code == 422
    foreign = create_chat(owner_client, account="other", recipient="other")
    assert task_create(owner_client, chat, conversation_id=foreign["conversation"]["id"]).status_code == 422


@pytest.mark.parametrize("capability", ["read", "retain"])
def test_sourced_task_requires_read_and_retain(owner_client, chat, db, capability):
    source = seed_message(db, chat)
    permission = db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
    setattr(permission, capability, False)
    db.commit()
    assert linked_task(owner_client, chat, source).status_code == 403
    assert task_create(owner_client, chat, title="Owner private task").status_code == 201


def test_task_deleted_and_suppressed_sources_rejected(owner_client, chat, db):
    deleted = seed_message(db, chat, deleted=True)
    assert linked_task(owner_client, chat, deleted).status_code == 422
    source = seed_message(db, chat)
    db.add(Suppression(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                       content_hash="forgotten", source_message_ids=[source.id]))
    db.commit()
    assert linked_task(owner_client, chat, source).status_code == 409


def test_task_patch_expected_version_clears_due_and_updates_status(owner_client, chat):
    task = task_create(owner_client, chat, due_at=(now() + timedelta(hours=1)).isoformat()).json()
    response = owner_client.patch(f"/tasks/{task['id']}",
                                  json={"expected_version": 1, "title": "Updated owner task", "due_at": None,
                                        "status": "completed"})
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2 and response.json()["due_at"] is None
    assert response.json()["status"] == "completed"
    assert owner_client.patch(f"/tasks/{task['id']}", json={"expected_version": 1, "title": "Stale"}).status_code == 409


@pytest.mark.parametrize("body", [
    {"title": "No version"}, {"expected_version": 1}, {"expected_version": 0, "title": "Invalid"},
    {"expected_version": 1, "title": None}, {"expected_version": 1, "status": None},
    {"expected_version": 1, "status": "sent"}, {"expected_version": 1, "due_at": "2026-10-10T09:30:00"},
])
def test_task_patch_validation(owner_client, chat, body):
    task = task_create(owner_client, chat).json()
    assert owner_client.patch(f"/tasks/{task['id']}", json=body).status_code == 422


def test_concurrent_task_edits_only_one_matching_version_wins(owner_client, chat, db):
    task = task_create(owner_client, chat).json()

    def update_title(title):
        return owner_client.patch(f"/tasks/{task['id']}", json={"expected_version": 1, "title": title})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(update_title, ["First edit", "Second edit"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    db.expire_all()
    stored = db.get(Task, task["id"])
    assert stored.version == 2 and stored.title in {"First edit", "Second edit"}


def test_tasks_filter_limit_and_permission_expiry(owner_client, chat, db):
    source = seed_message(db, chat)
    linked = linked_task(owner_client, chat, source).json()
    personal = task_create(owner_client, chat).json()
    owner_client.patch(f"/tasks/{personal['id']}", json={"expected_version": 1, "status": "completed"})
    params = {"workspace_id": chat["workspace"]["id"], "status": "pending", "limit": 1}
    result = owner_client.get("/tasks", params=params)
    assert [row["id"] for row in result.json()] == [linked["id"]]
    permission = db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
    permission.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert owner_client.get("/tasks", params=params).json() == []
    assert owner_client.get("/tasks", params={"workspace_id": chat["workspace"]["id"], "limit": 201}).status_code == 422
    assert owner_client.get("/tasks", params={"workspace_id": chat["workspace"]["id"], "status": "sent"}).status_code == 422


def test_task_cancellation_after_permission_revocation_is_idempotent(owner_client, chat, db):
    source = seed_message(db, chat)
    task = linked_task(owner_client, chat, source).json()
    owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions", json={})
    assert owner_client.delete(f"/tasks/{task['id']}?expected_version=2").status_code == 409
    assert owner_client.delete(f"/tasks/{task['id']}").status_code == 204
    assert owner_client.delete(f"/tasks/{task['id']}").status_code == 204
    db.expire_all()
    assert db.get(Task, task["id"]).status == "cancelled"


def test_owner_authored_task_without_source_stays_visible_when_chat_read_is_off(owner_client, chat, db):
    source = seed_message(db, chat)
    linked_task(owner_client, chat, source)
    manual = task_create(owner_client, chat, conversation_id=chat["conversation"]["id"],
                         title="Owner-provided follow-up").json()
    owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions", json={})
    listing = owner_client.get("/tasks", params={"workspace_id": chat["workspace"]["id"]})
    assert [row["id"] for row in listing.json()] == [manual["id"]]


def test_task_edit_and_delete_tenant_authorization(owner_client, chat):
    task = task_create(owner_client, chat).json()
    login(owner_client, "another-owner@example.test")
    assert owner_client.get("/tasks", params={"workspace_id": chat["workspace"]["id"]}).status_code == 404
    assert owner_client.post("/tasks", json={"workspace_id": chat["workspace"]["id"], "title": "Foreign"}).status_code == 404
    assert owner_client.patch(f"/tasks/{task['id']}", json={"expected_version": 1, "title": "Foreign"}).status_code == 404
    assert owner_client.delete(f"/tasks/{task['id']}").status_code == 404
    assert owner_client.get("/inbox", params={"workspace_id": chat["workspace"]["id"]}).status_code == 404


def test_source_edit_invalidates_task_requires_explicit_owner_review(owner_client, chat, db):
    source = seed_message(db, chat)
    task = linked_task(owner_client, chat, source).json()
    source.revision += 1
    conversation = db.get(Conversation, chat["conversation"]["id"])
    invalidate_task_sources(db, conversation, source.id)
    db.commit()
    assert db.get(Task, task["id"]).status == "needs_review"
    assert owner_client.patch(f"/tasks/{task['id']}", json={"expected_version": 1, "status": "pending"}).status_code == 409
    result = owner_client.patch(f"/tasks/{task['id']}", json={"expected_version": 2, "status": "pending"})
    assert result.status_code == 200
    assert result.json()["source_revision"] == {source.id: 2}


def test_forget_memory_cancels_and_redacts_derived_task(owner_client, chat, db):
    source = seed_message(db, chat, "Private appointment")
    task = linked_task(owner_client, chat, source, title="Private appointment follow-up").json()
    memory = owner_client.post(f"/conversations/{chat['conversation']['id']}/memories",
                               json={"text": "Private appointment", "source_message_ids": [source.id],
                                     "status": "confirmed"}).json()
    assert owner_client.delete(f"/memories/{memory['id']}").status_code == 204
    db.expire_all()
    stored = db.get(Task, task["id"])
    assert stored.status == "cancelled" and stored.title == "Forgotten task"
    assert stored.source_message_ids == [] and stored.source_revision == {}
    assert stored.version == 2
    assert db.get(Message, source.id).text == "Private appointment"


def test_inbox_has_platform_health_and_per_conversation_task_draft_counts(owner_client, chat, db):
    latest = seed_message(db, chat, "Inbox message")
    task_create(owner_client, chat)
    linked_task(owner_client, chat, latest)
    draft = owner_client.post(f"/conversations/{chat['conversation']['id']}/drafts", json={})
    assert draft.status_code == 201, draft.text
    response = owner_client.get("/inbox", params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 200
    data = response.json()
    assert data["personal_owner_task_count"] == 1 and not data["notifications_enabled"]
    entry = data["conversations"][0]
    assert entry["platform"] == "mock" and entry["connector_health"]["status"] == "connected"
    assert entry["latest_message"]["id"] == latest.id and entry["latest_message"]["text_preview"] == "Inbox message"
    assert entry["owner_task_count"] == 1 and entry["pending_draft_count"] == 1


def test_inbox_excludes_unreadable_expired_deleted_and_suppressed_previews(owner_client, chat, db):
    visible = seed_message(db, chat, "Visible context")
    suppressed = seed_message(db, chat, "PRIVATE FORGOTTEN")
    seed_message(db, chat, "PRIVATE DELETED", deleted=True)
    db.add(Suppression(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                       content_hash="forgotten", source_message_ids=[suppressed.id]))
    db.commit()
    hidden = related_chat(owner_client, chat, recipient="unreadable", read=False)
    seed_message(db, hidden, "PRIVATE UNREADABLE")
    expired = related_chat(owner_client, chat, recipient="expired")
    seed_message(db, expired, "PRIVATE EXPIRED")
    permission = db.scalar(select(Permission).where(Permission.conversation_id == expired["conversation"]["id"]))
    permission.expires_at = now() - timedelta(seconds=1)
    db.commit()
    result = owner_client.get("/inbox", params={"workspace_id": chat["workspace"]["id"]})
    assert "PRIVATE" not in result.text
    assert len(result.json()["conversations"]) == 1
    assert result.json()["conversations"][0]["latest_message"]["id"] == visible.id


def test_inbox_groups_are_separate_and_recent_activity_prioritized_before_limit(owner_client, chat, db):
    group = related_chat(owner_client, chat, recipient="group", kind="group")
    seed_message(db, group, "Group context")
    newest = seed_message(db, chat, "Latest direct context")
    result = owner_client.get("/inbox", params={"workspace_id": chat["workspace"]["id"], "limit": 1})
    assert result.json()["conversations"][0]["conversation_id"] == chat["conversation"]["id"]
    assert result.json()["conversations"][0]["latest_message"]["id"] == newest.id
    full = owner_client.get("/inbox", params={"workspace_id": chat["workspace"]["id"]}).json()
    assert {row["kind"] for row in full["conversations"]} == {"contact", "group"}


def test_chat_and_account_deletion_remove_task_content(owner_client, chat, db):
    source = seed_message(db, chat)
    linked = linked_task(owner_client, chat, source).json()
    personal = task_create(owner_client, chat).json()
    assert owner_client.delete(f"/conversations/{chat['conversation']['id']}/data").status_code == 200
    db.expire_all()
    assert db.get(Task, linked["id"]) is None and db.get(Task, personal["id"]) is not None
    assert owner_client.delete("/account-data", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200
    db.expire_all()
    assert db.get(Task, personal["id"]) is None
