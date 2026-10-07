from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from assistant.companion import check_action_budget, reserve_action_budget, reserve_usage, settle_action_budget
from assistant.action_models import OutboundAction
from assistant.db import now, uid
from assistant.models import AuditEvent, Message, Permission, Suppression
from assistant.native_models import MessageContext
from assistant.people_models import UsageLedger
from conftest import create_chat, login
from test_people import source


def command(client, chat, kind, **fields):
    return client.post("/assistant/commands", json={"command": kind,
                                                 "workspace_id": chat["workspace"]["id"], **fields})


def budget(client, chat, *, version=0, actions=1, **fields):
    return client.put(f"/workspaces/{chat['workspace']['id']}/budget", json={
        "expected_version": version, "max_actions_per_day": actions, **fields})


def test_typed_commands_reject_unrestricted_tools_and_arguments(owner_client, chat):
    assert command(owner_client, chat, "send_message", recipient="stranger", text="Hello").status_code == 422
    assert command(owner_client, chat, "catch_me_up", tool="network", grant=True).status_code == 422
    assert command(owner_client, chat, "pause", provider="whatsapp").status_code == 422
    assert command(owner_client, chat, "catch_me_up", limit=1000).status_code == 422


def test_companion_catchup_scoped_current_and_suppressed_sources(owner_client, chat, db):
    own = source(db, chat, text_value="Visible selected source")
    other = create_chat(owner_client, account="private", read=False)
    source(db, other, text_value="Secret excluded conversation")
    result = command(owner_client, chat, "catch_me_up")
    assert result.status_code == 200, result.text
    digest = result.json()["result"]
    assert digest["generated_by_model"] is False
    assert len(digest["conversations"]) == 1
    assert digest["conversations"][0]["latest"]["preview"] == "Visible selected source"
    db.add(Suppression(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                       content_hash="private", source_message_ids=[own.id]))
    db.commit()
    result = owner_client.get("/assistant/digest", params={"workspace_id": chat["workspace"]["id"]})
    assert result.json()["conversations"][0]["latest"] is None


def test_digest_excludes_expired_read_permissions(owner_client, chat, db):
    source(db, chat)
    row = db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert command(owner_client, chat, "catch_me_up").json()["result"]["conversations"] == []


def test_digest_excludes_expired_provider_message_context(owner_client, chat, db):
    row = source(db, chat, text_value="Expired provider source")
    db.add(MessageContext(workspace_id=row.workspace_id, connector_id=row.connector_id,
                          conversation_id=row.conversation_id, message_id=row.id,
                          expires_at=now() - timedelta(seconds=1)))
    db.commit()
    data = command(owner_client, chat, "catch_me_up").json()["result"]
    assert data["conversations"][0]["latest"] is None


def another_chat_in_workspace(client, chat):
    response = client.post("/conversations", json={"connector_id": chat["connector"]["id"],
                           "provider_chat_id": "15550005678", "title": "Another private chat", "kind": "contact"})
    assert response.status_code == 201
    conversation = response.json()
    response = client.put(f"/conversations/{conversation['id']}/permissions",
                          json={"read": True, "retain": True, "learn": True, "draft": True, "send": True})
    assert response.status_code == 200
    return {**chat, "conversation": conversation}


def test_contextual_digest_excludes_other_chats_personal_tasks_and_forward_audiences(owner_client, chat, db):
    other = another_chat_in_workspace(owner_client, chat)
    source(db, chat, text_value="Only the selected source")
    source(db, other, text_value="Do not include this other private source")
    selected_task = owner_client.post("/tasks", json={"workspace_id": chat["workspace"]["id"],
        "conversation_id": chat["conversation"]["id"], "title": "Selected chat follow-up"})
    assert selected_task.status_code == 201
    assert owner_client.post("/tasks", json={"workspace_id": chat["workspace"]["id"],
        "conversation_id": other["conversation"]["id"], "title": "Other chat private task"}).status_code == 201
    assert owner_client.post("/tasks", json={"workspace_id": chat["workspace"]["id"],
        "title": "Private owner-only reminder"}).status_code == 201
    due = now() + timedelta(minutes=10)
    assert owner_client.post("/jobs", json={"workspace_id": chat["workspace"]["id"],
        "idempotency_key": "digest-owner-reminder-1", "action_kind": "REMINDER", "content": "Private local reminder",
        "purpose": "Owner only", "due_at": due.isoformat(), "expires_at": (due + timedelta(hours=1)).isoformat()}).status_code == 201
    forward = OutboundAction(workspace_id=chat["workspace"]["id"], connector_id=chat["connector"]["id"],
        conversation_id=chat["conversation"]["id"], destination_conversation_id=other["conversation"]["id"],
        recipient_id="15550005678", kind="FORWARD", intent="forwarding", logical_key=uid(),
        payload={}, payload_hash="0" * 64, source_snapshot={}, destination_snapshot={},
        connector_fence=1, pause_generation=0, expires_at=now() + timedelta(hours=1), status="accepted")
    db.add(forward)
    for resource_id in (chat["workspace"]["id"], chat["conversation"]["id"], other["conversation"]["id"]):
        db.add(AuditEvent(workspace_id=chat["workspace"]["id"], actor_id="synthetic-owner",
                          action="synthetic.scope_test", resource_id=resource_id, details={}))
    db.commit()
    selected = command(owner_client, chat, "catch_me_up", conversation_id=chat["conversation"]["id"])
    assert selected.status_code == 200, selected.text
    digest = selected.json()["result"]
    assert digest["scope"] == "conversation" and digest["conversation_id"] == chat["conversation"]["id"]
    assert [row["conversation_id"] for row in digest["conversations"]] == [chat["conversation"]["id"]]
    assert digest["conversations"][0]["latest"]["preview"] == "Only the selected source"
    assert digest["conversations"][0]["pending_tasks"] == 1 and digest["personal_tasks"] == []
    assert all(row["action_ref"] != forward.id for row in digest["conversations"][0]["recent_receipts"])
    assert all(row["resource_ref"] == chat["conversation"]["id"] for row in digest["activity"])
    assert "Other chat private task" not in selected.text and "Private local reminder" not in selected.text
    assert "Do not include this other private source" not in selected.text
    workspace = command(owner_client, chat, "catch_me_up").json()["result"]
    assert workspace["scope"] == "workspace" and len(workspace["conversations"]) == 2
    assert len(workspace["personal_tasks"]) == 1
    assert any(receipt["action_ref"] == forward.id for row in workspace["conversations"]
               for receipt in row["recent_receipts"])


def test_digest_get_and_command_enforce_same_workspace_chat_scope(owner_client, chat, db):
    another_workspace = create_chat(owner_client, account="other-digest-workspace")
    for conversation_id in (another_workspace["conversation"]["id"], "nonexistent-chat"):
        assert command(owner_client, chat, "catch_me_up", conversation_id=conversation_id).status_code == 404
        assert owner_client.get("/assistant/digest", params={"workspace_id": chat["workspace"]["id"],
                                 "conversation_id": conversation_id}).status_code == 404
    source(db, chat, text_value="Explicit current chat")
    result = owner_client.get("/assistant/digest", params={"workspace_id": chat["workspace"]["id"],
                            "conversation_id": chat["conversation"]["id"]})
    assert result.status_code == 200 and result.json()["scope"] == "conversation"
    login(owner_client, "other-digest-owner@example.test")
    assert owner_client.get("/assistant/digest", params={"workspace_id": chat["workspace"]["id"],
                            "conversation_id": chat["conversation"]["id"]}).status_code == 404
    assert command(owner_client, chat, "catch_me_up", conversation_id=chat["conversation"]["id"]).status_code == 404


@pytest.mark.parametrize("expired", [False, True])
def test_contextual_catchup_blocks_revoked_or_expired_read(owner_client, chat, db, expired):
    source(db, chat, text_value="Must not disclose after revocation")
    permission = db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
    if expired:
        permission.expires_at = now() - timedelta(seconds=1)
    else:
        permission.read = False
    db.commit()
    assert command(owner_client, chat, "catch_me_up", conversation_id=chat["conversation"]["id"]).status_code == 403
    assert owner_client.get("/assistant/digest", params={"workspace_id": chat["workspace"]["id"],
                            "conversation_id": chat["conversation"]["id"]}).status_code == 403


def test_companion_teach_uses_scoped_evidence_and_never_sends(owner_client, chat, db):
    row = source(db, chat)
    result = command(owner_client, chat, "teach_me", conversation_id=chat["conversation"]["id"],
                     text="Owner-reviewed fact", source_message_ids=[row.id], status="confirmed")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["status"] == "confirmed"
    assert result.json()["result"]["source_message_ids"] == [row.id]
    other = create_chat(owner_client, account="other")
    assert command(owner_client, chat, "teach_me", conversation_id=other["conversation"]["id"],
                   text="Escaped scope", source_message_ids=[row.id]).status_code == 404
    assert db.scalar(select(func.count(Message.id))) == 1


def test_write_with_me_stays_draft_and_respects_current_permission(owner_client, chat, db):
    source(db, chat)
    result = command(owner_client, chat, "write_with_me", conversation_id=chat["conversation"]["id"],
                     instruction="Draft a polite reply")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["status"] == "needs_approval"
    row = db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
    row.draft = False
    db.commit()
    assert command(owner_client, chat, "write_with_me", conversation_id=chat["conversation"]["id"]).status_code == 403


def test_commands_pause_resume_workspace_and_explicit_chat_takeover(owner_client, chat):
    assert command(owner_client, chat, "pause").json()["result"]["paused"] is True
    assert command(owner_client, chat, "resume").json()["result"]["paused"] is False
    result = command(owner_client, chat, "pause", conversation_id=chat["conversation"]["id"])
    assert result.json()["result"]["control_state"] == "HUMAN_TAKEOVER"
    result = command(owner_client, chat, "resume", conversation_id=chat["conversation"]["id"])
    assert result.status_code == 200 and result.json()["result"]["control_state"] == "DRAFT_MODE"


def test_companion_and_budget_tenant_isolation(owner_client, chat):
    assert budget(owner_client, chat).status_code == 200
    login(owner_client, "someone@example.test")
    assert command(owner_client, chat, "catch_me_up").status_code == 404
    assert command(owner_client, chat, "pause").status_code == 404
    assert budget(owner_client, chat, version=1).status_code == 404
    assert owner_client.get(f"/workspaces/{chat['workspace']['id']}/budget").status_code == 404
    assert owner_client.get(f"/workspaces/{chat['workspace']['id']}/usage").status_code == 404


def test_action_budget_holds_at_boundary_and_replay_is_single_reservation(owner_client, chat, db):
    assert budget(owner_client, chat, actions=1).status_code == 200
    workspace_id = chat["workspace"]["id"]
    reserve_action_budget(db, workspace_id, "first", "send_text")
    db.commit()
    reserve_action_budget(db, workspace_id, "first", "send_text")
    db.commit()
    assert db.scalar(select(func.count(UsageLedger.id))) == 1
    check_action_budget(db, workspace_id, "first")
    with pytest.raises(HTTPException) as held:
        check_action_budget(db, workspace_id, "second")
    assert held.value.status_code == 429 and held.value.detail["code"] == "QUOTA_HELD"
    with pytest.raises(HTTPException):
        reserve_action_budget(db, workspace_id, "second", "send_text")
    db.rollback()


def test_budget_changes_cas_and_new_lower_limit_holds_existing_reservation(owner_client, chat, db):
    assert budget(owner_client, chat, actions=2).json()["version"] == 1
    assert budget(owner_client, chat, actions=3).status_code == 409
    reserve_action_budget(db, chat["workspace"]["id"], "first", "reaction")
    db.commit()
    assert budget(owner_client, chat, version=1, actions=0).status_code == 200
    with pytest.raises(HTTPException):
        check_action_budget(db, chat["workspace"]["id"], "first")


def test_uncertain_consumes_budget_release_restores_unused_quota(owner_client, chat, db):
    assert budget(owner_client, chat, actions=1).status_code == 200
    workspace_id = chat["workspace"]["id"]
    reserve_action_budget(db, workspace_id, "unused", "send_text")
    settle_action_budget(db, workspace_id, "unused", "released")
    db.commit()
    reserve_action_budget(db, workspace_id, "uncertain", "send_text")
    settle_action_budget(db, workspace_id, "uncertain", "uncertain")
    db.commit()
    with pytest.raises(HTTPException):
        reserve_action_budget(db, workspace_id, "new", "send_text")
    db.rollback()
    result = owner_client.get(f"/workspaces/{workspace_id}/usage").json()
    assert {entry["status"] for entry in result["entries"]} == {"released", "uncertain"}
    assert owner_client.get(f"/workspaces/{workspace_id}/budget").json()["usage"]["action_units"] == 1


def test_token_and_cost_accounting_independent_caps_and_no_private_content(owner_client, chat, db):
    assert budget(owner_client, chat, actions=None, max_tokens_per_day=100,
                  max_cost_microusd_per_day=1000).status_code == 200
    workspace_id = chat["workspace"]["id"]
    reserve_usage(db, workspace_id, "model:run-1", "model", token_units=80, cost_microusd=600)
    db.commit()
    with pytest.raises(HTTPException) as held:
        reserve_usage(db, workspace_id, "model:run-2", "model", token_units=21)
    assert held.value.detail["budget"] == "max_tokens_per_day"
    db.rollback()
    with pytest.raises(HTTPException) as held:
        reserve_usage(db, workspace_id, "model:run-3", "model", cost_microusd=401)
    assert held.value.detail["budget"] == "max_cost_microusd_per_day"
    db.rollback()
    assert "text" not in owner_client.get(f"/workspaces/{workspace_id}/usage").text


def test_daily_rollover_reallocates_pending_reservation(owner_client, chat, db, monkeypatch):
    assert budget(owner_client, chat, actions=1).status_code == 200
    workspace_id = chat["workspace"]["id"]
    first = reserve_action_budget(db, workspace_id, "first", "reaction")
    db.commit()
    old_day = first.window_day
    monkeypatch.setattr("assistant.companion.current_day", lambda: "2099-01-01")
    reserve_action_budget(db, workspace_id, "first", "reaction")
    db.commit()
    assert first.window_day != old_day
    assert db.scalar(select(func.count(UsageLedger.id))) == 1


def test_budget_holds_are_workspace_isolated(owner_client, chat, db):
    other = create_chat(owner_client, account="second")
    assert budget(owner_client, chat, actions=0).status_code == 200
    with pytest.raises(HTTPException):
        reserve_action_budget(db, chat["workspace"]["id"], "first", "send_text")
    db.rollback()
    reserve_action_budget(db, other["workspace"]["id"], "first", "send_text")
    db.commit()
    assert owner_client.get(f"/workspaces/{other['workspace']['id']}/budget").json()["usage"]["action_units"] == 1
@pytest.mark.parametrize("uncertain", [False, True])
def test_daily_action_budget_covers_legacy_drafts(app, owner_client, chat, monkeypatch, uncertain):
    from assistant import messaging
    from test_messaging import approve, make_draft
    workspace_id = chat["workspace"]["id"]
    response = owner_client.put(f"/workspaces/{workspace_id}/budget", json={"expected_version": 0,
                                                                         "max_actions_per_day": 1})
    assert response.status_code == 200
    if uncertain:
        async def transport(settings, provider, account, recipient, text, attempt, final_check=None):
            final_check()
            return "uncertain", None, "synthetic_outcome_unknown"
        monkeypatch.setattr(messaging, "_transport_send", transport)
    first = make_draft(app, chat)
    approve(owner_client, first)
    sent = owner_client.post(f"/drafts/{first[0]}/dispatch")
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == ("uncertain" if uncertain else "accepted")
    assert owner_client.post(f"/drafts/{first[0]}/dispatch").json()["status"] == sent.json()["status"]
    second = make_draft(app, chat)
    approve(owner_client, second)
    rejected = owner_client.post(f"/drafts/{second[0]}/dispatch")
    assert rejected.status_code == 429 and rejected.json()["detail"]["code"] == "QUOTA_HELD"
    assert owner_client.get(f"/workspaces/{workspace_id}/budget").json()["usage"]["action_units"] == 1
