"""Owner-scoped pagination and admission before synchronous private-data work."""

import pytest
from sqlalchemy import select, text

from assistant import core
from assistant.db import now, uid
from assistant.models import Connector, Conversation, Memory, Message, Outbox, Permission, Workspace
from conftest import login
from test_actions import source
from test_jobs import create_job


def _seed_list(app, chat, resource):
    instant = now()
    with app.state.session_factory() as db:
        workspace = db.get(Workspace, chat["workspace"]["id"])
        for index in range(105):
            if resource == "workspaces":
                row = Workspace(owner_id=workspace.owner_id, name=f"Synthetic workspace {index}", created_at=instant)
            elif resource == "connectors":
                row = Connector(workspace_id=workspace.id, provider="mock", account_id=f"pagination:{uid()}",
                                owner_sender_id="Owner", status="connected", created_at=instant)
            else:
                row = Conversation(workspace_id=workspace.id, connector_id=chat["connector"]["id"],
                                   provider_chat_id=f"pagination:{uid()}", title=f"Synthetic chat {index}",
                                   kind="contact", created_at=instant)
            db.add(row)
        db.commit()


@pytest.mark.parametrize("resource", ["workspaces", "connectors", "conversations"])
def test_owner_lists_have_stable_bounded_compatible_array_pages(app, owner_client, chat, resource):
    _seed_list(app, chat, resource)
    params = {} if resource == "workspaces" else {"workspace_id": chat["workspace"]["id"]}
    first = owner_client.get(f"/{resource}", params=params)
    all_rows = owner_client.get(f"/{resource}", params={**params, "limit": 200}).json()
    next_page = owner_client.get(f"/{resource}", params={**params, "limit": 60, "offset": 60}).json()
    assert first.status_code == 200
    assert isinstance(first.json(), list)
    assert len(first.json()) == 100
    assert len(all_rows) == 106
    assert first.json() == all_rows[:100]
    assert next_page == all_rows[60:120]
    assert len({row["id"] for row in all_rows}) == 106
    assert owner_client.get(f"/{resource}", params={**params, "offset": 106}).json() == []


@pytest.mark.parametrize("resource", ["workspaces", "connectors", "conversations"])
@pytest.mark.parametrize("invalid", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"offset": 10001}])
def test_list_page_parameters_reject_unbounded_requests(owner_client, chat, resource, invalid):
    params = {} if resource == "workspaces" else {"workspace_id": chat["workspace"]["id"]}
    assert owner_client.get(f"/{resource}", params={**params, **invalid}).status_code == 422


def test_list_pages_remain_owner_scoped(owner_client, chat):
    login(owner_client, "different-owner@example.test")
    assert owner_client.get("/workspaces", params={"limit": 200}).json() == []
    for resource in ("connectors", "conversations"):
        assert owner_client.get(f"/{resource}", params={"workspace_id": chat["workspace"]["id"],
                                                     "limit": 200, "offset": 0}).status_code == 404


def test_export_admission_counts_before_decrypting_private_rows(app, owner_client, chat, monkeypatch):
    memory_id = uid()
    with app.state.session_factory() as db:
        db.add(Memory(id=memory_id, workspace_id=chat["workspace"]["id"],
                      conversation_id=chat["conversation"]["id"], text="Synthetic private memory"))
        db.commit()
        # An over-limit operation must reject from metadata, before decrypting
        # this deliberately invalid synthetic ciphertext into an export payload.
        db.execute(text("UPDATE memories SET text=:ciphertext WHERE id=:id"),
                   {"ciphertext": "invalid-synthetic-ciphertext", "id": memory_id})
        db.commit()
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_ROWS", 2)
    response = owner_client.post("/data-export", params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 413
    assert response.json()["detail"] == {"code": "PRIVATE_ROW_LIMIT_EXCEEDED", "operation": "export",
                                       "limit": 2, "resource": "private_rows"}


def test_export_admits_exact_total_row_boundary(app, owner_client, chat, monkeypatch):
    with app.state.session_factory() as db:
        db.add(Memory(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                      text="Synthetic memory"))
        db.commit()
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_ROWS", 3)
    response = owner_client.post("/data-export", params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["conversations"][0]["memories"][0]["text"] == "Synthetic memory"


@pytest.mark.parametrize("operation", ["export", "purge"])
def test_private_payload_byte_admission_accepts_exact_boundary_and_rejects_one_byte_less(app, owner_client, chat, monkeypatch, operation):
    with app.state.session_factory() as db:
        memory = Memory(id=uid(), workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                        text="Synthetic unicode: नमस्कार 👩🏽‍💻")
        db.add(memory)
        db.commit()
        cipher = db.execute(text("SELECT text FROM memories WHERE id=:id"), {"id": memory.id}).scalar_one()
        ciphertext_bytes = len(cipher.encode("utf-8"))
        conversation_revision = db.get(Conversation, chat["conversation"]["id"]).revision
    endpoint, params = (("/data-export", {"workspace_id": chat["workspace"]["id"]}) if operation == "export" else
                        (f"/conversations/{chat['conversation']['id']}/data", {}))
    request = owner_client.post if operation == "export" else owner_client.delete
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_CIPHERTEXT_BYTES", ciphertext_bytes - 1)
    rejected = request(endpoint, params=params)
    assert rejected.status_code == 413, rejected.text
    assert rejected.json()["detail"] == {"code": "PRIVATE_PAYLOAD_LIMIT_EXCEEDED",
              "operation": "export" if operation == "export" else "purge_conversation",
              "limit": ciphertext_bytes - 1, "resource": "ciphertext_bytes"}
    with app.state.session_factory() as db:
        assert db.get(Memory, memory.id).text == "Synthetic unicode: नमस्कार 👩🏽‍💻"
        assert db.get(Conversation, chat["conversation"]["id"]).revision == conversation_revision
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_CIPHERTEXT_BYTES", ciphertext_bytes)
    accepted = request(endpoint, params=params)
    assert accepted.status_code == 200, accepted.text
    if operation == "export":
        assert accepted.json()["conversations"][0]["memories"][0]["text"] == "Synthetic unicode: नमस्कार 👩🏽‍💻"
    else:
        with app.state.session_factory() as db:
            assert db.get(Memory, memory.id) is None


def test_private_payload_admission_rejects_before_decrypting_oversized_ciphertext(app, owner_client, chat, monkeypatch):
    with app.state.session_factory() as db:
        memory = Memory(id=uid(), workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"], text="Synthetic")
        db.add(memory)
        db.commit()
        db.execute(text("UPDATE memories SET text=:value WHERE id=:id"), {"value": "🧪" * 30, "id": memory.id})
        db.commit()
    # 30 Unicode characters occupy 120 UTF-8 bytes. This corrupt synthetic value
    # must be rejected from SQL lengths, rather than decrypted or character-counted.
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_CIPHERTEXT_BYTES", 119)
    response = owner_client.post("/data-export", params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 413
    assert response.json()["detail"]["resource"] == "ciphertext_bytes"


@pytest.mark.parametrize("auxiliary", ["native", "local_reminder"])
def test_export_admission_includes_auxiliary_private_material(app, owner_client, chat, monkeypatch, auxiliary):
    if auxiliary == "native":
        source(app, chat, native=True)
        limit = 4  # conversation, permission, message, context, native original = five
    else:
        create_job(owner_client, chat, action_kind="REMINDER", conversation_id=None,
                   content="Synthetic personal reminder")
        limit = 2  # conversation, permission, independently private local reminder = three
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_ROWS", limit)
    response = owner_client.post("/data-export", params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "PRIVATE_ROW_LIMIT_EXCEEDED"


def test_export_ignores_large_revoked_chat_without_decrypting_it(app, owner_client, chat, monkeypatch):
    with app.state.session_factory() as db:
        for index in range(4):
            db.add(Memory(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                          text=f"Hidden memory {index}"))
        db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"])).read = False
        db.commit()
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_ROWS", 1)
    response = owner_client.post("/data-export", params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 200
    assert response.json()["conversations"] == []


def test_private_admission_checks_owner_before_reporting_limits(app, owner_client, chat, monkeypatch):
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_ROWS", 0)
    login(owner_client, "different-owner@example.test")
    workspace_id = chat["workspace"]["id"]
    for method, endpoint, params in (("post", "/data-export", {"workspace_id": workspace_id}),
                                     ("delete", "/account-data", {"workspace_id": workspace_id}),
                                     ("delete", f"/conversations/{chat['conversation']['id']}/data", {})):
        response = getattr(owner_client, method)(endpoint, params=params)
        assert response.status_code == 404
        assert "PRIVATE_ROW_LIMIT_EXCEEDED" not in response.text


def _private_message(db, workspace_id, connector_id, conversation_id):
    row = Message(id=uid(), workspace_id=workspace_id, connector_id=connector_id, conversation_id=conversation_id,
                  provider_message_id=uid(), sender_id="peer", direction="inbound", origin="history",
                  author_kind="contact_human", text="Synthetic retained history", provider_timestamp=now())
    db.add(row)
    return row.id


def test_workspace_purge_admits_aggregate_before_any_destruction(app, owner_client, chat, monkeypatch):
    workspace_id, first_id = chat["workspace"]["id"], chat["conversation"]["id"]
    with app.state.session_factory() as db:
        second_id = uid()
        db.add(Conversation(id=second_id, workspace_id=workspace_id, connector_id=chat["connector"]["id"],
                            provider_chat_id="synthetic-second-peer", title="Second", kind="contact"))
        db.flush()
        db.add(Permission(workspace_id=workspace_id, conversation_id=second_id, read=True, retain=True))
        messages = [_private_message(db, workspace_id, chat["connector"]["id"], conv_id)
                    for conv_id in (first_id, second_id)]
        db.commit()
        before = {conv_id: db.get(Conversation, conv_id).revision for conv_id in (first_id, second_id)}
    # Each chat individually is below this limit; the full operation exceeds it.
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_ROWS", 5)
    response = owner_client.delete("/account-data", params={"workspace_id": workspace_id})
    assert response.status_code == 413
    assert response.json()["detail"]["operation"] == "purge_workspace"
    with app.state.session_factory() as db:
        workspace = db.get(Workspace, workspace_id)
        assert workspace.paused is False and workspace.pause_generation == 0
        assert db.get(Connector, chat["connector"]["id"]).status == "connected"
        for message_id in messages:
            message = db.get(Message, message_id)
            assert message.text == "Synthetic retained history" and message.deleted is False
        for conv_id in (first_id, second_id):
            assert db.get(Conversation, conv_id).revision == before[conv_id]
            assert db.scalar(select(Permission).where(Permission.conversation_id == conv_id)).read is True


def test_chat_purge_admits_actual_workspace_pending_outbox_scan(app, owner_client, chat, monkeypatch):
    workspace_id, conversation_id = chat["workspace"]["id"], chat["conversation"]["id"]
    with app.state.session_factory() as db:
        message_id = _private_message(db, workspace_id, chat["connector"]["id"], conversation_id)
        for _ in range(5):
            db.add(Outbox(workspace_id=workspace_id, kind="synthetic.pending", aggregate_id=uid(),
                          payload={"conversation_id": "unrelated-synthetic-conversation"}, status="pending"))
        db.commit()
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_ROWS", 5)
    response = owner_client.delete(f"/conversations/{conversation_id}/data")
    assert response.status_code == 413
    assert response.json()["detail"]["operation"] == "purge_conversation"
    with app.state.session_factory() as db:
        assert db.get(Message, message_id).text == "Synthetic retained history"
        assert db.get(Message, message_id).deleted is False
        assert db.scalar(select(Permission).where(Permission.conversation_id == conversation_id)).read is True
        assert all(row.status == "pending" for row in db.scalars(select(Outbox)))


@pytest.mark.parametrize("operation", ["export", "purge_workspace"])
def test_private_operations_bound_conversation_fanout(app, owner_client, chat, monkeypatch, operation):
    with app.state.session_factory() as db:
        second_id = uid()
        db.add(Conversation(id=second_id, workspace_id=chat["workspace"]["id"], connector_id=chat["connector"]["id"],
                            provider_chat_id="fanout-second-peer", title="Second", kind="contact"))
        db.flush()
        db.add(Permission(workspace_id=chat["workspace"]["id"], conversation_id=second_id, read=True))
        db.commit()
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_CONVERSATIONS", 1)
    method, endpoint = ("post", "/data-export") if operation == "export" else ("delete", "/account-data")
    response = getattr(owner_client, method)(endpoint, params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 413
    assert response.json()["detail"] == {"code": "PRIVATE_ROW_LIMIT_EXCEEDED", "operation": operation,
                                       "limit": 1, "resource": "conversations"}
