from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text

from assistant.db import now, uid
from assistant.action_models import OutboundAction, SubmissionAttempt
from assistant.messaging import CanonicalEvent
from assistant.models import Conversation, Draft, Message, MessageEvent, Permission, SendAttempt, Suppression
from assistant.native import (
    forget_native_sources, message_context_for, native_authority_payload, native_record_for,
    native_text_supported, purge_native_data, reaction_habit_for, reaction_target_handled,
)
from assistant.native_models import NativeRecord, ReactionExample
from conftest import create_chat, login
from test_messaging import approve, event, make_draft, receive


def native_event(chat, **changes):
    remote_jid = changes.pop("native_chat_id", "15550001234")
    body = event(chat, **changes)
    key = {"id": body["provider_message_id"], "remoteJid": remote_jid,
           "fromMe": body["direction"] == "outbound"}
    body["native_record"] = {"provider_record_ref": uid(), "account_id": chat["connector"]["account_id"],
                             "key": key, "payload": {"key": key, "message": {"conversation": body["content"]["text"]}}}
    return body


def reaction(chat, target, *, origin="live", sender="Owner", author="human_owner", emoji="❤️", action="add"):
    return event(chat, direction="outbound", sender_id=sender, author_kind=author, origin=origin,
                 event_type="reaction.added" if action == "add" else "reaction.removed",
                 reaction={"target_provider_message_id": target, "emoji": emoji, "action": action})


def test_handoff_aliases_and_uppercase_origin(chat):
    body = event(chat)
    body["connection_id"] = body.pop("connector_id")
    body.pop("sender_id")
    body["sender_identity"] = "exact@s.whatsapp.net"
    body["authorship"] = "OTHER"
    body["origin"] = "BACKFILL"
    body["reply_to_source_id"] = "source-one"
    parsed = CanonicalEvent.model_validate(body)
    assert parsed.connector_id == chat["connector"]["id"]
    assert parsed.origin == "history" and parsed.author_kind == "contact_human"
    assert parsed.sender_id == "exact@s.whatsapp.net" and parsed.reply_to == "source-one"


@pytest.mark.parametrize("changes", [
    {"connection_id": "another"}, {"sender_identity": {"id": "another"}},
    {"expires_at": "2026-10-06T12:00:00"}, {"origin": "INVALID"},
])
def test_conflicting_alias_or_unbounded_event_rejected(chat, changes):
    with pytest.raises(ValidationError):
        CanonicalEvent.model_validate(event(chat, **changes))


def test_authentic_record_scope_encryption_and_mock_provenance(app, owner_client, chat):
    body = native_event(chat, owner_addressed=True, sender_identity={"id": "Contact", "lid": "123@lid"})
    response = receive(owner_client, body)
    assert response.status_code == 200, response.text
    message_id = response.json()["message_id"]
    with app.state.session_factory() as db:
        message = db.get(Message, message_id)
        record = native_record_for(db, message)
        assert record and record.provenance == "mock" and record.owner_addressed
        assert message_context_for(db, message).sender_identity["lid"] == "123@lid"
        authority = native_authority_payload(record)
        assert authority["key"]["message_id"] == message.provider_message_id
        assert authority["record"] == body["native_record"]["payload"]
        ciphertext = db.execute(text("SELECT payload FROM native_records WHERE id=:id"), {"id": record.id}).scalar_one()
        assert body["content"]["text"] not in ciphertext and body["provider_message_id"] not in ciphertext
    # Owners receive status, never the private provider original.
    status = owner_client.get(f"/messages/{message_id}/native-status")
    assert status.status_code == 200 and status.json()["provenance"] == "mock"
    assert body["content"]["text"] not in status.text


@pytest.mark.parametrize("mutate", ["account", "chat", "id", "direction", "payload"])
def test_native_key_forgery_rejected_atomically(app, owner_client, chat, mutate):
    body = native_event(chat)
    original = body["native_record"]
    if mutate == "account":
        original["account_id"] = "another-owner"
    elif mutate == "chat":
        original["key"]["remoteJid"] = "another-chat"
    elif mutate == "id":
        original["key"]["id"] = "another-source"
    elif mutate == "direction":
        original["key"]["fromMe"] = True
    else:
        original["payload"] = {"key": {"id": "wrong", "remoteJid": "wrong", "fromMe": False}}
    response = receive(owner_client, body)
    assert response.status_code == 403, response.text
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Message.id))) == 0
        assert db.scalar(select(func.count(NativeRecord.id))) == 0


def test_native_original_requires_internal_auth_and_real_key(owner_client, chat):
    body = native_event(chat)
    assert owner_client.post("/internal/connector-events", json=body).status_code == 401
    body["native_record"]["payload"] = {"message": {"conversation": "Synthetic export text"}}
    response = receive(owner_client, body)
    assert response.status_code == 422


def test_text_export_cannot_become_native(owner_client, chat):
    response = receive(owner_client, native_event(chat, provider_message_id="export:hash:1", origin="history"))
    assert response.status_code == 422


@pytest.mark.parametrize("unavailable", ["view_once", "expired", "deleted"])
def test_native_sensitive_or_expired_original_unavailable(app, owner_client, chat, unavailable):
    body = native_event(chat)
    if unavailable == "view_once":
        body["native_record"]["view_once"] = True
    elif unavailable == "expired":
        body["expires_at"] = (now() - timedelta(seconds=1)).isoformat()
    else:
        body["deleted_at"] = now().isoformat()
    response = receive(owner_client, body)
    assert response.status_code == 200, response.text
    with app.state.session_factory() as db:
        assert native_record_for(db, db.get(Message, response.json()["message_id"])) is None
    if unavailable in {"expired", "deleted"}:
        assert not response.json()["live_eligible"]


def test_original_invalid_after_source_revision_and_deletion(app, owner_client, chat):
    body = native_event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    edited = event(chat, provider_message_id=body["provider_message_id"], event_type="message.edited", source_revision=2)
    assert receive(owner_client, edited).status_code == 200
    with app.state.session_factory() as db:
        assert native_record_for(db, db.get(Message, message_id)) is None
    edited.update(event_id=uid(), event_type="message.deleted", source_revision=3)
    assert receive(owner_client, edited).status_code == 200
    with app.state.session_factory() as db:
        record = db.scalar(select(NativeRecord))
        assert record.deleted and record.payload == {}


def test_native_reference_cannot_be_rebound(app, owner_client, chat):
    first = native_event(chat)
    assert receive(owner_client, first).status_code == 200
    second = native_event(chat)
    second["native_record"]["provider_record_ref"] = first["native_record"]["provider_record_ref"]
    assert receive(owner_client, second).status_code == 409
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Message.id))) == 1


def test_native_status_owner_isolation(owner_client, chat):
    message_id = receive(owner_client, native_event(chat)).json()["message_id"]
    login(owner_client, "another-owner@example.test")
    assert owner_client.get(f"/messages/{message_id}/native-status").status_code == 404


def test_live_human_reaction_handles_target_and_is_genuine_evidence(app, owner_client, chat):
    body = native_event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    result = receive(owner_client, reaction(chat, body["provider_message_id"]))
    assert result.status_code == 200, result.text
    assert result.json()["target_handled"]
    with app.state.session_factory() as db:
        message = db.get(Message, message_id)
        assert reaction_target_handled(db, message)
        assert reaction_habit_for(db, message, "❤️") is not None
        assert db.get(Conversation, message.conversation_id).control_state == "DRAFT_MODE"
        raw = db.execute(text("SELECT context FROM reaction_examples")).scalar_one()
        assert "available" not in raw
    profile = owner_client.get(f"/conversations/{chat['conversation']['id']}/reaction-profile")
    assert profile.json()["palette"] == {"❤️": 1} and profile.json()["semantics_required"]


@pytest.mark.parametrize("origin", ["HISTORY", "BACKFILL", "REPLAY", "UNKNOWN"])
def test_historical_reactions_never_take_over_or_handle_target(app, owner_client, chat, origin):
    body = event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    response = receive(owner_client, reaction(chat, body["provider_message_id"], origin=origin))
    assert response.status_code == 200 and not response.json()["target_handled"]
    with app.state.session_factory() as db:
        message = db.get(Message, message_id)
        assert not reaction_target_handled(db, message)
        assert db.get(Conversation, message.conversation_id).control_state == "DRAFT_MODE"
        learned = reaction_habit_for(db, message, "❤️") is not None
        assert learned == (origin in {"HISTORY", "BACKFILL"})


@pytest.mark.parametrize("author,sender", [("assistant", "Owner"), ("human_owner", "another-actor")])
def test_false_owner_or_assistant_claim_never_teaches_and_live_is_conservative(app, owner_client, chat, author, sender):
    body = event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    response = receive(owner_client, reaction(chat, body["provider_message_id"], author=author, sender=sender))
    assert response.json()["author_kind"] == "unknown_owner_outgoing"
    with app.state.session_factory() as db:
        message = db.get(Message, message_id)
        assert reaction_habit_for(db, message, "❤️") is None
        assert db.get(Conversation, message.conversation_id).control_state == "HUMAN_TAKEOVER"


def test_reaction_duplicate_and_removal_do_not_unhandle(app, owner_client, chat):
    body = event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    reacted = reaction(chat, body["provider_message_id"])
    assert receive(owner_client, reacted).json()["target_handled"]
    assert receive(owner_client, reacted).json()["status"] == "duplicate"
    removed = reaction(chat, body["provider_message_id"], action="remove")
    assert receive(owner_client, removed).status_code == 200
    with app.state.session_factory() as db:
        message = db.get(Message, message_id)
        assert reaction_target_handled(db, message)
        assert reaction_habit_for(db, message, "❤️") is None
        assert db.scalar(select(func.count(ReactionExample.id))) == 2
        assert db.scalar(select(func.count(MessageEvent.id))) == 3


def test_reaction_foreign_target_missing_and_wrong_emoji(owner_client, chat):
    other = create_chat(owner_client, account="second-account", recipient="another-chat")
    body = event(other)
    assert receive(owner_client, body).status_code == 200
    response = receive(owner_client, reaction(chat, body["provider_message_id"]))
    assert response.json() == {"status": "ignored", "reason": "SOURCE_MISSING"}
    assert receive(owner_client, reaction(chat, body["provider_message_id"], emoji="payment approved")).status_code == 422


def test_no_learning_permission_still_handles_actual_human_reply(app, owner_client, chat):
    body = event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    with app.state.session_factory() as db:
        permission = db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
        permission.learn = False
        db.commit()
    assert receive(owner_client, reaction(chat, body["provider_message_id"])).json()["target_handled"]
    with app.state.session_factory() as db:
        message = db.get(Message, message_id)
        assert reaction_target_handled(db, message) and reaction_habit_for(db, message, "❤️") is None


def test_forgotten_sources_cannot_teach_or_be_forwarded(app, owner_client, chat):
    body = native_event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    assert receive(owner_client, reaction(chat, body["provider_message_id"], origin="history")).status_code == 200
    with app.state.session_factory() as db:
        db.add(Suppression(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                           content_hash="test", source_message_ids=[message_id]))
        forget_native_sources(db, chat["conversation"]["id"], [message_id])
        db.commit()
        message = db.get(Message, message_id)
        assert native_record_for(db, message) is None
        assert reaction_habit_for(db, message, "❤️") is None
        assert db.scalar(select(ReactionExample)).context == {}
    assert receive(owner_client, reaction(chat, body["provider_message_id"])).json()["reason"] == "SOURCE_MISSING"


def test_purge_redacts_provider_original_and_reaction_context(app, owner_client, chat):
    body = native_event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    assert receive(owner_client, reaction(chat, body["provider_message_id"], origin="history")).status_code == 200
    with app.state.session_factory() as db:
        purge_native_data(db, chat["conversation"]["id"])
        db.commit()
        assert native_record_for(db, db.get(Message, message_id)) is None
        assert db.scalar(select(ReactionExample)).context == {}
        assert message_context_for(db, db.get(Message, message_id)).sender_identity == {}


def test_source_edit_clears_reaction_habit_evidence(app, owner_client, chat):
    body = native_event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    assert receive(owner_client, reaction(chat, body["provider_message_id"], origin="history")).status_code == 200
    edit = event(chat, event_type="message.edited", source_revision=2,
                 provider_message_id=body["provider_message_id"])
    assert receive(owner_client, edit).status_code == 200
    with app.state.session_factory() as db:
        message = db.get(Message, message_id)
        assert reaction_habit_for(db, message, "❤️") is None
        assert not reaction_target_handled(db, message)
    assert owner_client.get(f"/conversations/{chat['conversation']['id']}/reaction-profile").json()["sample_count"] == 0


def test_forgotten_original_cannot_restore_from_later_native_edit(app, owner_client, chat):
    body = native_event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    with app.state.session_factory() as db:
        db.add(Suppression(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                           content_hash="test", source_message_ids=[message_id]))
        forget_native_sources(db, chat["conversation"]["id"], [message_id])
        db.commit()
    edit = native_event(chat, event_type="message.edited", source_revision=2,
                        provider_message_id=body["provider_message_id"])
    assert receive(owner_client, edit).status_code == 200
    with app.state.session_factory() as db:
        assert native_record_for(db, db.get(Message, message_id)) is None
        assert db.scalar(select(NativeRecord)).payload == {}


def test_human_reaction_cancels_grounded_legacy_draft_without_chat_pause(app, owner_client, chat):
    body = native_event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    draft = make_draft(app, chat)
    with app.state.session_factory() as db:
        row = db.get(Draft, draft[0])
        row.evidence_message_ids = [message_id]
        db.commit()
    approve(owner_client, draft)
    assert receive(owner_client, reaction(chat, body["provider_message_id"])).json()["target_handled"]
    with app.state.session_factory() as db:
        assert db.get(Draft, draft[0]).status == "cancelled"
        assert db.get(Conversation, chat["conversation"]["id"]).control_state == "DRAFT_MODE"
        assert db.scalar(select(func.count(SendAttempt.id))) == 0


def test_expired_message_evidence_blocks_legacy_submission(app, owner_client, chat):
    body = native_event(chat, expires_at=(now() + timedelta(hours=2)).isoformat())
    message_id = receive(owner_client, body).json()["message_id"]
    draft = make_draft(app, chat)
    with app.state.session_factory() as db:
        row = db.get(Draft, draft[0])
        row.evidence_message_ids = [message_id]
        db.commit()
    approve(owner_client, draft)
    with app.state.session_factory() as db:
        context = message_context_for(db, db.get(Message, message_id))
        context.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").status_code == 409
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SendAttempt.id))) == 0


def record_generic_attempt(app, chat, *, kind="SEND_TEXT", target_id=None):
    with app.state.session_factory() as db:
        action = OutboundAction(workspace_id=chat["workspace"]["id"], connector_id=chat["connector"]["id"],
                                conversation_id=chat["conversation"]["id"],
                                destination_conversation_id=chat["conversation"]["id"],
                                recipient_id="15550001234", kind=kind, intent="acknowledgement",
                                logical_key=uid(), payload={"text": "An assistant response", "emoji": "❤️"},
                                payload_hash="a" * 64, target_message_id=target_id,
                                connector_fence=1, pause_generation=0,
                                expires_at=now() + timedelta(hours=1), status="uncertain")
        db.add(action)
        db.flush()
        provider_id = f"mock:action:{action.id}"
        attempt = SubmissionAttempt(workspace_id=action.workspace_id, action_id=action.id,
                                    connector_id=action.connector_id,
                                    destination_conversation_id=action.destination_conversation_id,
                                    payload_hash=action.payload_hash, connector_fence=1,
                                    provider_message_id=provider_id, status="uncertain")
        db.add(attempt)
        db.commit()
        return action.id, attempt.id, provider_id


@pytest.mark.parametrize("kind", ["SEND_TEXT", "QUOTE", "FORWARD"])
def test_generic_action_echo_reconciles_without_human_takeover(app, owner_client, chat, kind):
    action_id, attempt_id, provider_id = record_generic_attempt(app, chat, kind=kind)
    response = receive(owner_client, event(chat, provider_message_id=provider_id, direction="outbound",
                                           sender_id="Owner", author_kind="human_owner"))
    assert response.status_code == 200 and response.json()["author_kind"] == "assistant"
    with app.state.session_factory() as db:
        assert db.get(OutboundAction, action_id).status == "accepted"
        assert db.get(SubmissionAttempt, attempt_id).status == "accepted"
        assert db.get(Conversation, chat["conversation"]["id"]).control_state == "DRAFT_MODE"
        assert db.get(Message, response.json()["message_id"]).excluded_from_learning


def test_generic_provider_echo_cannot_cross_account(app, owner_client, chat):
    action_id, attempt_id, provider_id = record_generic_attempt(app, chat)
    other = create_chat(owner_client, account="second-account")
    response = receive(owner_client, event(other, provider_message_id=provider_id, direction="outbound",
                                           sender_id="Owner", author_kind="assistant"))
    assert response.json()["author_kind"] == "unknown_owner_outgoing"
    with app.state.session_factory() as db:
        assert db.get(OutboundAction, action_id).status == "uncertain"
        assert db.get(SubmissionAttempt, attempt_id).status == "uncertain"
        assert db.get(Conversation, other["conversation"]["id"]).control_state == "HUMAN_TAKEOVER"


def test_generic_reaction_echo_never_becomes_owner_habit(app, owner_client, chat):
    body = event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    action_id, attempt_id, provider_id = record_generic_attempt(app, chat, kind="REACTION", target_id=message_id)
    echoed = reaction(chat, body["provider_message_id"], author="human_owner")
    echoed["provider_message_id"] = provider_id
    response = receive(owner_client, echoed)
    assert response.json()["author_kind"] == "assistant" and not response.json()["target_handled"]
    with app.state.session_factory() as db:
        message = db.get(Message, message_id)
        assert reaction_habit_for(db, message, "❤️") is None
        assert not reaction_target_handled(db, message)
        assert db.get(OutboundAction, action_id).status == "accepted"
        assert db.get(SubmissionAttempt, attempt_id).status == "accepted"


def test_generic_reaction_echo_wrong_target_or_emoji_rejected(app, owner_client, chat):
    body = event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    action_id, attempt_id, provider_id = record_generic_attempt(app, chat, kind="REACTION", target_id=message_id)
    forged = reaction(chat, body["provider_message_id"], author="assistant", emoji="👍")
    forged["provider_message_id"] = provider_id
    response = receive(owner_client, forged)
    assert response.json()["author_kind"] == "unknown_owner_outgoing"
    with app.state.session_factory() as db:
        assert db.get(OutboundAction, action_id).status == "uncertain"
        assert db.get(SubmissionAttempt, attempt_id).status == "uncertain"


@pytest.mark.parametrize("unsupported_message", [
    {"imageMessage": {"caption": "Are you available?"}},
    {"videoMessage": {"caption": "Are you available?"}},
    {"conversation": "Are you available?", "documentMessage": {"fileName": "private.pdf"}},
    {"viewOnceMessage": {"message": {"conversation": "Are you available?"}}},
    {"ephemeralMessage": {"message": {"conversation": "Are you available?"}}},
    {"extendedTextMessage": {"text": "Are you available?", "contextInfo": {"quotedMessage": {"imageMessage": {}}}}},
    {"conversation": {"fake_text": "Are you available?"}},
])
def test_media_and_hidden_wrappers_never_become_native_action_sources(app, owner_client, chat, unsupported_message):
    body = native_event(chat)
    body["native_record"]["payload"]["message"] = unsupported_message
    response = receive(owner_client, body)
    assert response.status_code == 200, response.text
    message_id = response.json()["message_id"]
    with app.state.session_factory() as db:
        record = db.scalar(select(NativeRecord))
        assert not native_text_supported(record)
        assert record.payload["message"] == unsupported_message  # Private evidence can be retained.
        assert native_record_for(db, db.get(Message, message_id)) is None
    status = owner_client.get(f"/messages/{message_id}/native-status")
    assert status.status_code == 200 and not status.json()["available"]


def test_extended_authentic_text_supported(app, owner_client, chat):
    body = native_event(chat)
    body["native_record"]["payload"]["message"] = {"extendedTextMessage": {"text": body["content"]["text"]}}
    response = receive(owner_client, body)
    assert response.status_code == 200, response.text
    with app.state.session_factory() as db:
        record = native_record_for(db, db.get(Message, response.json()["message_id"]))
        assert record and native_text_supported(record)


def test_native_text_mismatch_rejected_atomically(app, owner_client, chat):
    body = native_event(chat)
    body["native_record"]["payload"]["message"]["conversation"] = "Unrelated source content"
    response = receive(owner_client, body)
    assert response.status_code == 403
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Message.id))) == 0
        assert db.scalar(select(func.count(NativeRecord.id))) == 0


def test_top_level_view_once_flag_and_changed_original_text_unavailable(app, owner_client, chat):
    body = native_event(chat)
    message_id = receive(owner_client, body).json()["message_id"]
    with app.state.session_factory() as db:
        record = db.scalar(select(NativeRecord))
        record.payload = {**record.payload, "viewOnce": True}
        db.commit()
        assert native_record_for(db, db.get(Message, message_id)) is None
        record.payload = {"key": record.payload["key"], "message": {"conversation": "Other text"}}
        db.commit()
        assert native_record_for(db, db.get(Message, message_id)) is None
