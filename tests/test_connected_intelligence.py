"""Connected evidence updates, bounded scoped retrieval and owner-only model answers."""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from assistant import intelligence
from assistant.action_models import OutboundAction
from assistant.db import now, uid
from assistant.models import Conversation, Draft, Message, Permission, StyleProfile, Suppression
from assistant.native_models import MessageContext
from conftest import create_chat, login
from test_core import import_body
from test_intelligence import seed_message
from test_messaging import event, receive


def style_url(chat):
    return f"/conversations/{chat['conversation']['id']}/style-profile"


def ask(client, chat, question="What is the delivery schedule?", **fields):
    return client.post("/assistant/commands", json={"command": "ask_me",
        "workspace_id": chat["workspace"]["id"], "conversation_id": chat["conversation"]["id"],
        "question": question, **fields})


def model_capture(monkeypatch, sources, *, mutation=None):
    captured = {}

    def provider(settings, context):
        captured.update(context)
        if mutation:
            mutation()
        return intelligence.ModelResult(text="The contact says delivery is on Friday.",
                                         evidence_message_ids=sources), "approved-real-provider-fixture"

    monkeypatch.setattr(intelligence, "call_model", provider)
    return captured


def test_history_import_refreshes_chat_style_without_model_or_messages(owner_client, chat, db, monkeypatch):
    monkeypatch.setattr(intelligence, "call_model", lambda *args: pytest.fail("Import must never call a model"))
    response = owner_client.post("/imports", json=import_body(chat))
    assert response.status_code == 201, response.text
    profile = owner_client.get(style_url(chat)).json()
    assert profile["sample_count"] == 2 and profile["version"] == 1
    assert profile["features"]["method"] == "local_statistics" and not profile["reviewed"]
    assert "Hello" not in json.dumps(profile["features"])
    assert db.scalar(select(func.count(Draft.id))) == 0
    assert db.scalar(select(func.count(OutboundAction.id))) == 0
    assert owner_client.post("/imports", json=import_body(chat)).json()["replayed"]
    assert owner_client.get(style_url(chat)).json()["version"] == 1


def test_learning_grant_builds_existing_import_only_after_consent(owner_client, db):
    chat = create_chat(owner_client, learn=False)
    assert owner_client.post("/imports", json=import_body(chat)).status_code == 201
    assert owner_client.get(style_url(chat)).json()["version"] == 0
    assert owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions", json={
        "read": True, "retain": True, "learn": True, "draft": True}).status_code == 200
    assert owner_client.get(style_url(chat)).json()["sample_count"] == 2
    assert owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions", json={
        "read": True, "retain": True, "learn": False, "draft": True}).status_code == 200
    assert db.scalar(select(func.count(StyleProfile.id))) == 0


def test_live_owner_style_refreshes_on_edit_and_deletion_preserves_rules(owner_client, chat, db):
    body = event(chat, direction="outbound", sender_id="Owner", author_kind="human_owner",
                 content={"type": "text", "text": "ok thanks"})
    accepted = receive(owner_client, body)
    assert accepted.status_code == 200, accepted.text
    profile = owner_client.get(style_url(chat)).json()
    assert profile["sample_count"] == 1 and profile["features"]["median_words"] == 2
    edited = owner_client.patch(style_url(chat), json={"owner_rules": ["Keep replies brief"], "reviewed": True})
    assert edited.status_code == 200
    body.update(event_id=uid(), event_type="message.edited", source_revision=2,
                content={"type": "text", "text": "Hi, thank you for the useful information"})
    assert receive(owner_client, body).status_code == 200
    profile = owner_client.get(style_url(chat)).json()
    assert profile["sample_count"] == 1 and profile["features"]["median_words"] == 7
    assert profile["owner_rules"] == ["Keep replies brief"] and not profile["reviewed"]
    body.update(event_id=uid(), event_type="message.deleted", source_revision=3, content={"type": "text", "text": ""})
    assert receive(owner_client, body).status_code == 200
    profile = owner_client.get(style_url(chat)).json()
    assert profile["sample_count"] == 0 and profile["evidence_message_ids"] == []
    assert db.scalar(select(func.count(Draft.id))) == 0


@pytest.mark.parametrize("kind", ["contact", "group"])
def test_auto_style_scope_rejects_other_senders_assistant_forwarded_and_ungranted(owner_client, db, kind):
    chat = create_chat(owner_client, kind=kind)
    other = create_chat(owner_client, account="other-statistical-person", kind="contact", learn=False)
    for changes in ({"sender_id": "NotOwner", "author_kind": "human_owner"},
                    {"sender_id": "Owner", "author_kind": None},
                    {"sender_id": "Owner", "author_kind": "human_owner",
                     "content": {"type": "text", "text": "[Forwarded]\nPrivate third party style"}}):
        assert receive(owner_client, event(chat, direction="outbound", **changes)).status_code == 200
    assert owner_client.get(style_url(chat)).json()["sample_count"] == 0
    assert receive(owner_client, event(other, sender_id="Owner", direction="outbound", author_kind="human_owner",
                                       content={"type": "text", "text": "Never learn without consent"})).status_code == 200
    assert owner_client.get(style_url(other)).json()["version"] == 0
    assert receive(owner_client, event(chat, direction="outbound", sender_id="Owner", author_kind="human_owner",
                                       content={"type": "text", "text": "hi thanks"})).status_code == 200
    profile = owner_client.get(style_url(chat)).json()
    assert profile["sample_count"] == 1
    evidence = db.get(Message, profile["evidence_message_ids"][0])
    assert evidence.conversation_id == chat["conversation"]["id"]


def test_pause_prevents_automatic_style_update(owner_client, chat):
    assert owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200
    assert owner_client.post("/imports", json=import_body(chat)).status_code == 201
    assert owner_client.get(style_url(chat)).json()["version"] == 0


def test_retrieval_finds_older_matching_contact_evidence_and_excludes_private_owner_style(owner_client, chat, db,
                                                                                         monkeypatch):
    relevant = seed_message(db, chat, "Delivery schedule is Friday afternoon")
    foreign = create_chat(owner_client, account="irrelevant-private-person")
    seed_message(db, foreign, "Delivery secret private group schedule")
    owner = seed_message(db, chat, "Owner style secret 9381", direction="outbound", author_kind="human_owner")
    for index in range(35):
        seed_message(db, chat, f"Unrelated routine note {index}")
    captured = model_capture(monkeypatch, [relevant.id])
    response = owner_client.post(f"/conversations/{chat['conversation']['id']}/drafts",
                                 json={"instruction": "Respond about delivery schedule"})
    assert response.status_code == 201, response.text
    assert relevant.id in {row["id"] for row in captured["messages"]}
    assert len(captured["messages"]) <= 40
    assert owner.id not in json.dumps(captured) and "9381" not in json.dumps(captured)
    assert "secret private group" not in json.dumps(captured)
    assert response.json()["status"] == "needs_approval"


def test_suppressed_and_expired_sources_filtered_before_recent_retrieval_limit(owner_client, chat, db,
                                                                               monkeypatch):
    visible = seed_message(db, chat, "Delivery schedule is Friday")
    suppressed, expired = [], []
    for index in range(35):
        suppressed.append(seed_message(db, chat, f"Delivery forgotten {index}"))
        row = seed_message(db, chat, f"Delivery expired {index}")
        expired.append(row)
        db.add(MessageContext(workspace_id=row.workspace_id, connector_id=row.connector_id,
            conversation_id=row.conversation_id, message_id=row.id, expires_at=now() - timedelta(seconds=1)))
    db.add(Suppression(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                       content_hash="forgotten-test", source_message_ids=[row.id for row in suppressed]))
    db.commit()
    captured = model_capture(monkeypatch, [visible.id])
    response = ask(owner_client, chat)
    assert response.status_code == 200, response.text
    assert [row["id"] for row in captured["messages"]] == [visible.id]
    assert "forgotten" not in json.dumps(captured) and "expired" not in json.dumps(captured)


def test_owner_answer_works_with_read_without_draft_and_never_creates_external_action(owner_client, db,
                                                                                     monkeypatch):
    chat = create_chat(owner_client, draft=False, send=False)
    source = seed_message(db, chat, "Delivery is Friday")
    captured = model_capture(monkeypatch, [source.id])
    response = ask(owner_client, chat)
    assert response.status_code == 200, response.text
    answer = response.json()["result"]
    assert answer["audience"] == "owner_only" and answer["generated_by_model"]
    assert answer["evidence_message_ids"] == [source.id] and not answer["external_actions"]
    assert captured["purpose"] == "owner_answer" and captured["scope"]["kind"] == "contact"
    assert db.scalar(select(func.count(Draft.id))) == 0
    assert db.scalar(select(func.count(OutboundAction.id))) == 0


@pytest.mark.parametrize("mutation", ["read", "source", "pause", "takeover", "expiry"])
def test_owner_answer_rechecks_consent_and_current_evidence_after_model(app, owner_client, chat, db,
                                                                       monkeypatch, mutation):
    source = seed_message(db, chat, "Delivery is Friday")

    def change():
        with app.state.session_factory() as other:
            if mutation in {"read", "expiry"}:
                row = other.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
                if mutation == "read":
                    row.read = False
                else:
                    row.expires_at = now() - timedelta(seconds=1)
            elif mutation == "source":
                row = other.get(Message, source.id)
                row.text, row.revision = "Changed underlying evidence", row.revision + 1
            elif mutation == "pause":
                from assistant.models import Workspace
                other.get(Workspace, chat["workspace"]["id"]).paused = True
            else:
                other.get(Conversation, chat["conversation"]["id"]).control_state = "HUMAN_TAKEOVER"
            other.commit()

    model_capture(monkeypatch, [source.id], mutation=change)
    response = ask(owner_client, chat)
    assert response.status_code == 409, response.text
    assert db.scalar(select(func.count(Draft.id))) == 0


def test_owner_answer_invalid_evidence_and_unsupported_commands_blocked(owner_client, chat, db, monkeypatch):
    seed_message(db, chat)
    other = create_chat(owner_client, account="owner-answer-foreign-person")
    foreign = seed_message(db, other, "Private other-person evidence")
    model_capture(monkeypatch, [foreign.id])
    assert ask(owner_client, chat).status_code == 502
    assert ask(owner_client, chat, recipient="stranger", forward=True).status_code == 422
    assert ask(owner_client, chat, question=" ").status_code == 422
    assert ask(owner_client, chat, conversation_id=other["conversation"]["id"]).status_code == 404
    login(owner_client, "owner-answer-intruder@example.test")
    assert ask(owner_client, chat).status_code == 404


def test_owner_answer_disabled_and_mock_are_honest(owner_client, chat, app):
    simulated = ask(owner_client, chat)
    assert simulated.status_code == 200, simulated.text
    result = simulated.json()["result"]
    assert result["development_mock"] and not result["generated_by_model"]
    assert result["text"].startswith("Simulation:")
    app.state.settings.model_provider = "disabled"
    assert ask(owner_client, chat).status_code == 503
    app.state.settings.environment = "production"
    app.state.settings.model_provider = "mock"
    assert ask(owner_client, chat).status_code == 503


def test_owner_answer_prompt_has_no_send_or_memory_authority(app):
    app.state.settings.model_provider = "openai"
    payload = intelligence.model_request_payload(app.state.settings, {"purpose": "owner_answer", "messages": []})
    prompt = payload["messages"][0]["content"]
    assert "owner-only answer" in prompt and "UNTRUSTED" in prompt
    assert "cannot send, forward, grant permissions" in prompt


def test_owner_answer_returns_current_server_scope_without_message_text_in_metadata(owner_client, chat, db,
                                                                                   monkeypatch):
    source = seed_message(db, chat, "Private contact phrase 92761")
    model_capture(monkeypatch, [source.id])
    response = ask(owner_client, chat)
    assert response.status_code == 200, response.text
    answer = response.json()["result"]
    context = answer["authorization_context"]
    grant = db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
    conversation = db.get(Conversation, chat["conversation"]["id"])
    assert context["schema_version"] == 1 and context["conversation_id"] == conversation.id
    assert context["workspace_id"] == conversation.workspace_id and context["connector_id"] == conversation.connector_id
    assert context["conversation_revision"] == conversation.revision and context["control_epoch"] == conversation.control_epoch
    assert context["permission_version"] == grant.version and context["permissions"]["read"]
    assert context["permission_expires_at"] is None and context["memory_versions"] == {}
    assert "Private contact phrase" not in json.dumps(context)
    expiry = intelligence.aware(datetime.fromisoformat(context["expires_at"]))
    assert now() < expiry <= now() + timedelta(minutes=5)
    snapshot = owner_client.get("/ui/bootstrap").json()
    assert context["owner_id"] == snapshot["user"]["id"]


@pytest.mark.parametrize("boundary", ["message", "permission", "memory"])
def test_owner_answer_scope_deadline_never_outlives_selected_evidence_or_permission(owner_client, chat, db,
                                                                                   monkeypatch, boundary):
    from assistant.models import Memory
    source = seed_message(db, chat, "Delivery is Friday")
    deadline = now() + timedelta(seconds=30)
    if boundary == "message":
        db.add(MessageContext(workspace_id=source.workspace_id, connector_id=source.connector_id,
            conversation_id=source.conversation_id, message_id=source.id, expires_at=deadline))
    elif boundary == "permission":
        grant = db.scalar(select(Permission).where(Permission.conversation_id == source.conversation_id))
        grant.expires_at = deadline
    else:
        db.add(Memory(workspace_id=source.workspace_id, conversation_id=source.conversation_id,
            text="Delivery confirmed Friday", status="confirmed", source_message_ids=[source.id],
            source_revision={source.id: source.revision}, expires_at=deadline))
    db.commit()
    model_capture(monkeypatch, [source.id])
    response = ask(owner_client, chat)
    assert response.status_code == 200, response.text
    context = response.json()["result"]["authorization_context"]
    assert datetime.fromisoformat(context["expires_at"]) == deadline
    if boundary == "memory":
        assert len(context["memory_versions"]) == 1
