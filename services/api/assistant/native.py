"""Scoped originals and human reaction evidence; no transport or synthetic originals."""

import json
from datetime import UTC
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import conversation_for, permission_for, workspace_for
from .auth import get_current_user
from .db import aware, get_db, now
from .models import Connector, Conversation, Draft, Message, MessageEvent, Permission, ScheduledIntent, Suppression
from .native_models import MessageContext, NativeRecord, ReactionExample

router = APIRouter(tags=["native-records"])


class NativeKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=180)
    remoteJid: str = Field(min_length=1, max_length=160)
    fromMe: StrictBool
    participant: str | None = Field(default=None, min_length=1, max_length=160)


class NativeOriginal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_record_ref: str = Field(min_length=1, max_length=180)
    account_id: str = Field(min_length=1, max_length=120)
    key: NativeKey
    payload: dict
    view_once: bool = False

    @field_validator("payload")
    @classmethod
    def bounded_original(cls, value):
        if len(json.dumps(value, ensure_ascii=False).encode()) > 131072:
            raise ValueError("Native provider record exceeds 128 KiB")
        return value


class ReactionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_provider_message_id: str = Field(min_length=1, max_length=180)
    emoji: str = Field(default="", max_length=32)
    action: Literal["add", "remove"] = "add"

    @field_validator("emoji")
    @classmethod
    def emoji_only(cls, value):
        # Reject arbitrary labels and text. Semantic suitability belongs to action policy.
        if value and (any(c.isalnum() or c.isspace() for c in value)
                      or not any(ord(c) >= 0x2300 for c in value)):
            raise ValueError("Reaction must be an emoji, not text")
        return value


def message_context_for(db: Session, message: Message) -> MessageContext | None:
    return db.scalar(select(MessageContext).where(
        MessageContext.message_id == message.id, MessageContext.workspace_id == message.workspace_id,
        MessageContext.connector_id == message.connector_id,
        MessageContext.conversation_id == message.conversation_id,
    ))


def message_available(db: Session, message: Message) -> bool:
    if message.deleted:
        return False
    context = message_context_for(db, message)
    return not (context and context.expires_at and aware(context.expires_at) <= now())


def _native_text(payload: dict) -> str | None:
    """The initial adapter is text-only; wrappers and mixed media do not qualify."""
    if any(payload.get(key) for key in ("viewOnce", "view_once", "ephemeralMessage", "disappearingMode")):
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    if set(message) == {"conversation"}:
        text = message["conversation"]
    elif set(message) == {"extendedTextMessage"}:
        extended = message["extendedTextMessage"]
        if not isinstance(extended, dict) or set(extended) != {"text"}:
            return None
        text = extended["text"]
    else:
        return None
    return text if isinstance(text, str) and text.strip() and len(text) <= 20000 else None


def native_text_supported(record: NativeRecord) -> bool:
    return bool(record.payload and _native_text(record.payload) is not None)


def native_record_for(db: Session, message: Message) -> NativeRecord | None:
    if not message_available(db, message) or message.provider_message_id.startswith("export:"):
        return None
    for suppression in db.scalars(select(Suppression).where(Suppression.conversation_id == message.conversation_id)):
        if message.id in (suppression.source_message_ids or []):
            return None
    record = db.scalar(select(NativeRecord).where(
        NativeRecord.message_id == message.id, NativeRecord.workspace_id == message.workspace_id,
        NativeRecord.connector_id == message.connector_id,
        NativeRecord.conversation_id == message.conversation_id,
    ))
    connector = db.get(Connector, message.connector_id)
    conversation = db.get(Conversation, message.conversation_id)
    permission = db.scalar(select(Permission).where(Permission.conversation_id == message.conversation_id,
                                                   Permission.workspace_id == message.workspace_id))
    if (record is None or connector is None or conversation is None or record.deleted or record.view_once
            or not record.payload or record.source_revision != message.revision
            or record.account_id != connector.account_id
            or record.provider_chat_id != conversation.provider_chat_id
            or record.provider_message_id != message.provider_message_id
            or not native_text_supported(record) or _native_text(record.payload) != message.text
            or not permission or not permission.read or not permission.retain
            or (permission.expires_at and aware(permission.expires_at) <= now())
            or (record.expires_at and aware(record.expires_at) <= now())):
        return None
    key = record.payload.get("key", {})
    if (key.get("id") != message.provider_message_id
            or key.get("remoteJid") != record.provider_chat_id
            or key.get("fromMe") != (message.direction == "outbound")):
        return None
    return record


def native_authority_payload(record: NativeRecord) -> dict:
    """Expose a validated original only at the authenticated dispatch authority boundary."""
    original_key = record.payload["key"]
    key = {"remote_jid": original_key["remoteJid"], "message_id": original_key["id"],
           "from_me": original_key["fromMe"]}
    if original_key.get("participant"):
        key["participant"] = original_key["participant"]
    return {"ref": record.id, "key": key, "revision": record.source_revision,
            "deleted": record.deleted,
            "expires_at": aware(record.expires_at).isoformat() if record.expires_at else None,
            "record": record.payload}


def store_native_observation(db, message, connector, conversation, event):
    """Persist only authenticated connector metadata, enforcing original key boundaries."""
    original = event.native_record
    context = message_context_for(db, message)
    if context is None:
        context = MessageContext(workspace_id=message.workspace_id, message_id=message.id,
                                 connector_id=connector.id, conversation_id=conversation.id)
        db.add(context)
    # Edits can remove metadata. They cannot silently extend an original expiry.
    if event.expires_at is not None:
        if context.expires_at is None or event.expires_at < aware(context.expires_at):
            context.expires_at = event.expires_at
    context.owner_addressed = event.owner_addressed
    context.sender_identity = event.sender_identity or {"id": message.sender_id}
    context.participant_identity = event.participant_identity or {}
    context.view_once = context.view_once or bool(original and original.view_once)
    record = db.scalar(select(NativeRecord).where(NativeRecord.message_id == message.id))
    suppressed = any(message.id in (row.source_message_ids or []) for row in db.scalars(
        select(Suppression).where(Suppression.conversation_id == message.conversation_id)))
    if message.deleted or suppressed:
        if record:
            record.deleted = True
            record.payload = {}
        return
    if original is None:
        # An edit without a refreshed original invalidates the old revision.
        return
    if connector.provider == "export_only" or message.provider_message_id.startswith("export:"):
        raise HTTPException(422, "Text exports cannot supply authentic native provider records")
    key = original.key
    if (original.account_id != connector.account_id or key.id != message.provider_message_id
            or key.remoteJid != conversation.provider_chat_id
            or key.fromMe != (message.direction == "outbound")):
        raise HTTPException(403, "Native record account/chat/message key mismatch")
    payload_key = original.payload.get("key")
    try:
        checked_key = NativeKey.model_validate(payload_key)
    except (ValueError, TypeError):
        raise HTTPException(422, "Native original requires its authentic provider key") from None
    if checked_key != key:
        raise HTTPException(403, "Native payload key mismatch")
    actual_text = _native_text(original.payload)
    if actual_text is not None and actual_text != message.text:
        raise HTTPException(403, "Native original text does not match the canonical source")
    if key.participant:
        participant = context.participant_identity.get("id")
        if participant and participant != key.participant:
            raise HTTPException(403, "Native participant identity mismatch")
    existing = db.scalar(select(NativeRecord).where(
        NativeRecord.connector_id == connector.id,
        NativeRecord.provider_record_ref == original.provider_record_ref,
    ))
    if existing is not None and existing.message_id != message.id:
        raise HTTPException(409, "Native record reference is already bound to another message")
    if record is None:
        record = NativeRecord(workspace_id=message.workspace_id, message_id=message.id,
                              connector_id=connector.id, conversation_id=conversation.id)
        db.add(record)
    record.provider_record_ref = original.provider_record_ref
    record.provider_message_id = message.provider_message_id
    record.account_id = connector.account_id
    record.provider_chat_id = conversation.provider_chat_id
    record.source_revision = message.revision
    record.provenance = "mock" if connector.provider == "mock" else "provider"
    record.payload = original.payload
    record.expires_at = context.expires_at
    record.owner_addressed = context.owner_addressed
    record.view_once = context.view_once
    record.deleted = False


def reaction_target_handled(db, message):
    return db.scalar(select(ReactionExample.id).where(
        ReactionExample.message_id == message.id,
        ReactionExample.workspace_id == message.workspace_id,
        ReactionExample.conversation_id == message.conversation_id,
        ReactionExample.target_revision == message.revision,
        ReactionExample.handled.is_(True),
    ).limit(1)) is not None


def reaction_habit_for(db, message, emoji):
    permission = db.scalar(select(Permission).where(
        Permission.conversation_id == message.conversation_id,
        Permission.workspace_id == message.workspace_id,
    ))
    if not permission or not permission.learn or (permission.expires_at and aware(permission.expires_at) <= now()):
        return None
    suppressed = set()
    for row in db.scalars(select(Suppression).where(Suppression.conversation_id == message.conversation_id)):
        suppressed.update(row.source_message_ids or [])
    examples = db.scalars(select(ReactionExample).where(
        ReactionExample.workspace_id == message.workspace_id,
        ReactionExample.conversation_id == message.conversation_id,
        ReactionExample.author_kind == "human_owner", ReactionExample.learn_eligible.is_(True),
        ReactionExample.active.is_(True), ReactionExample.emoji == emoji,
    ).order_by(ReactionExample.provider_timestamp.desc()).limit(100)).all()
    for example in examples:
        target = db.get(Message, example.message_id)
        if (target and target.id not in suppressed and target.revision == example.target_revision
                and message_available(db, target)):
            return example
    return None


def invalidate_native_source(db, message_id):
    for row in db.scalars(select(ReactionExample).where(ReactionExample.message_id == message_id)):
        row.learn_eligible = False


def ingest_reaction(db, event, connector, conversation, permission, event_key):
    from .messaging import invalidate_conversation
    prior = db.scalar(select(ReactionExample).where(ReactionExample.event_key == event_key,
                                                   ReactionExample.connector_id == connector.id))
    if prior:
        return {"status": "duplicate", "message_id": prior.message_id}
    target = db.scalar(select(Message).where(
        Message.connector_id == connector.id, Message.conversation_id == conversation.id,
        Message.workspace_id == connector.workspace_id,
        Message.provider_message_id == event.reaction.target_provider_message_id,
    ))
    if target is None or not message_available(db, target):
        return {"status": "ignored", "reason": "SOURCE_MISSING"}
    for suppression in db.scalars(select(Suppression).where(Suppression.conversation_id == conversation.id)):
        if target.id in (suppression.source_message_ids or []):
            return {"status": "ignored", "reason": "SOURCE_MISSING"}
    if event.native_record:
        raise HTTPException(422, "Reaction observations cannot replace target provider originals")
    is_owner = event.direction == "outbound" and event.sender_id == connector.owner_sender_id
    author = "contact_human" if event.direction == "inbound" else "unknown_owner_outgoing"
    if is_owner and event.author_kind == "human_owner":
        author = "human_owner"
    # Claimed assistant authorship is insufficient without the immutable submission ledger.
    try:
        from .actions import assistant_reaction_echo
    except ImportError:
        assistant_reaction_echo = None
    if assistant_reaction_echo and assistant_reaction_echo(db, event):
        author = "assistant"
    added = event.event_type == "reaction.added"
    if added and (event.reaction.action != "add" or not event.reaction.emoji):
        raise HTTPException(422, "Added reaction requires an emoji and add operation")
    if not added:
        if event.reaction.action != "remove":
            raise HTTPException(422, "Removed reaction requires remove operation")
        for previous in db.scalars(select(ReactionExample).where(
            ReactionExample.message_id == target.id, ReactionExample.actor_id == event.sender_id,
            ReactionExample.active.is_(True),
        )):
            if not event.reaction.emoji or previous.emoji == event.reaction.emoji:
                previous.active = False
    elif author != "assistant":
        # The provider maintains one current reaction per actor/target.
        for previous in db.scalars(select(ReactionExample).where(
            ReactionExample.message_id == target.id, ReactionExample.actor_id == event.sender_id,
            ReactionExample.active.is_(True),
        )):
            previous.active = False
    handled = author == "human_owner" and event.origin == "live" and added
    example = ReactionExample(workspace_id=connector.workspace_id, connector_id=connector.id,
                              conversation_id=conversation.id, message_id=target.id,
                              event_key=event_key, target_revision=target.revision,
                              actor_id=event.sender_id, author_kind=author,
                              emoji=event.reaction.emoji, origin=event.origin,
                              provider_timestamp=event.provider_timestamp,
                              context={"text": target.text}, active=added,
                              learn_eligible=bool(author == "human_owner" and added and permission.learn
                                                  and event.origin in {"live", "history"}),
                              handled=handled)
    db.add(example)
    db.add(MessageEvent(workspace_id=connector.workspace_id, event_id=event_key,
                        conversation_id=conversation.id, message_id=target.id,
                        event_type=event.event_type, source_revision=target.revision))
    if handled:
        try:
            from .actions import invalidate_target
        except ImportError:
            invalidate_target = None
        if invalidate_target:
            invalidate_target(db, target.id, reason="HUMAN_REACTION")
        # Legacy draft responses have no explicit trigger field. Cancel drafts grounded
        # in this handled target as well, without placing the entire chat in takeover.
        for draft in db.scalars(select(Draft).where(
            Draft.conversation_id == conversation.id, Draft.status.in_({"needs_approval", "approved", "ready"}),
        )):
            if target.id in (draft.evidence_message_ids or []):
                draft.status = "cancelled"
                draft.approved_hash = None
                draft.approval_expires_at = None
                for intent in db.scalars(select(ScheduledIntent).where(
                    ScheduledIntent.draft_id == draft.id, ScheduledIntent.status.in_({"scheduled", "held"}),
                )):
                    intent.status = "cancelled"
    elif author == "unknown_owner_outgoing" and event.origin == "live":
        conversation.control_epoch += 1
        conversation.control_state = "HUMAN_TAKEOVER"
        conversation.revision += 1
        invalidate_conversation(db, conversation, "unknown_owner_reaction")
    return {"status": "accepted", "message_id": target.id, "author_kind": author,
            "target_handled": handled, "live_eligible": False, "automatic_reply": False}


def purge_native_data(db, conversation_id):
    for row in db.scalars(select(NativeRecord).where(NativeRecord.conversation_id == conversation_id)):
        row.payload = {}
        row.deleted = True
    for row in db.scalars(select(MessageContext).where(MessageContext.conversation_id == conversation_id)):
        row.sender_identity = {}
        row.participant_identity = {}
    for row in db.scalars(select(ReactionExample).where(ReactionExample.conversation_id == conversation_id)):
        row.context = {}
        row.emoji = ""
        row.learn_eligible = False


def forget_native_sources(db, conversation_id, source_ids):
    if not source_ids:
        return
    for row in db.scalars(select(NativeRecord).where(
        NativeRecord.conversation_id == conversation_id, NativeRecord.message_id.in_(source_ids),
    )):
        row.payload = {}
        row.deleted = True
    for row in db.scalars(select(ReactionExample).where(
        ReactionExample.conversation_id == conversation_id, ReactionExample.message_id.in_(source_ids),
    )):
        row.context = {}
        row.learn_eligible = False


def redact_native_source_identity(db, conversation_id, source_ids):
    """Raw-data expiration also removes private JID/LID/phone alias dictionaries."""
    if not source_ids:
        return
    for row in db.scalars(select(MessageContext).where(
        MessageContext.conversation_id == conversation_id, MessageContext.message_id.in_(source_ids),
    )):
        row.sender_identity = {}
        row.participant_identity = {}


def export_native_data(db, conversation_id):
    """Export evidence and lifecycle metadata without private original transport material."""
    records = db.scalars(select(NativeRecord).where(NativeRecord.conversation_id == conversation_id)).all()
    reactions = db.scalars(select(ReactionExample).where(ReactionExample.conversation_id == conversation_id)).all()
    return {"native_records": [{"id": row.id, "message_id": row.message_id,
                                "source_revision": row.source_revision, "provenance": row.provenance,
                                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                                "deleted": row.deleted, "view_once": row.view_once} for row in records],
            "reaction_examples": [{"message_id": row.message_id, "target_revision": row.target_revision,
                                   "emoji": row.emoji, "author_kind": row.author_kind,
                                   "origin": row.origin, "active": row.active,
                                   "learn_eligible": row.learn_eligible,
                                   "provider_timestamp": row.provider_timestamp.isoformat()} for row in reactions]}


@router.get("/messages/{message_id}/native-status")
def native_status(message_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(404, "Message not found")
    workspace_for(db, user, message.workspace_id)
    conversation = conversation_for(db, user, message.conversation_id)
    permission_for(db, conversation, "read")
    record = native_record_for(db, message)
    return {"message_id": message.id, "available": record is not None,
            "native_ref": record.id if record else None,
            "provenance": record.provenance if record else None,
            "source_revision": message.revision,
            "reason": None if record else "SOURCE_MISSING"}


@router.get("/conversations/{conversation_id}/reaction-profile")
def reaction_profile(conversation_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = conversation_for(db, user, conversation_id)
    permission_for(db, conversation, "read")
    permission_for(db, conversation, "learn")
    samples = db.scalars(select(ReactionExample).where(
        ReactionExample.conversation_id == conversation.id,
        ReactionExample.author_kind == "human_owner", ReactionExample.learn_eligible.is_(True),
        ReactionExample.active.is_(True),
    ).order_by(ReactionExample.provider_timestamp.desc()).limit(500)).all()
    counts = {}
    suppressed = set()
    for suppression in db.scalars(select(Suppression).where(Suppression.conversation_id == conversation.id)):
        suppressed.update(suppression.source_message_ids or [])
    for sample in samples:
        target = db.get(Message, sample.message_id)
        if (target and target.id not in suppressed and target.revision == sample.target_revision
                and message_available(db, target)):
            counts[sample.emoji] = counts.get(sample.emoji, 0) + 1
    return {"conversation_id": conversation.id, "sample_count": sum(counts.values()),
            "palette": counts, "evidence_kind": "verified_human_owner_reactions",
            "semantics_required": True}


def utc_datetime(value):
    if value is not None and value.tzinfo is None:
        raise ValueError("Timestamp requires an explicit timezone")
    return value.astimezone(UTC) if value else None
