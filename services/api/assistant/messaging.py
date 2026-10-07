"""Durable message intake and the single, conservative outbound policy boundary.

No history event or model output directly sends a message. An owner approves exact
content, and both immediate and scheduled sends use the same ledger and checks.
"""

import asyncio
import hashlib
import hmac
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Literal
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .access import audit, permission_for, workspace_for
from .auth import get_current_user
from .db import aware, get_db, now, uid
from .models import (
    AuditEvent, Connector, Conversation, Draft, Memory, Message, MessageEvent, Outbox, Permission,
    ScheduledIntent, SendAttempt, StyleProfile, User, Workspace,
)
from .native import NativeOriginal, ReactionContent, utc_datetime

router = APIRouter(tags=["messaging"])
PENDING_DRAFTS = {"needs_approval", "approved", "ready"}
_submit_guards = WeakValueDictionary()
_guards_lock = threading.Lock()


@contextmanager
def submit_guard(workspace_id: str):
    """Serialize observed controls and local submit decisions within one gateway process.

    PostgreSQL state, a unique send ledger, and account fences remain authoritative.
    This local guard is not a replacement for a distributed gateway lease.
    """
    with _guards_lock:
        lock = _submit_guards.get(workspace_id)
        if lock is None:
            lock = threading.RLock()
            _submit_guards[workspace_id] = lock
    with lock:
        yield


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def require_internal(request: Request) -> None:
    expected = request.app.state.settings.internal_service_token
    supplied = request.headers.get("authorization", "")
    if not expected or not hmac.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
        raise HTTPException(401, "Valid internal service authentication required")


class EventContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"] = "text"
    text: str = Field(default="", max_length=20000)


class CanonicalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=200)
    workspace_id: str | None = None  # Compatibility only; stored ownership is authoritative.
    connector_id: str
    conversation_id: str
    provider: str | None = None
    account_id: str | None = None
    provider_message_id: str = Field(min_length=1, max_length=180)
    sender_id: str = Field(min_length=1, max_length=160)
    direction: Literal["inbound", "outbound"]
    origin: Literal["live", "history", "replay", "unknown"]
    event_type: Literal[
        "message.created", "message.edited", "message.deleted", "reaction.added", "reaction.removed"
    ] = "message.created"
    author_kind: Literal[
        "contact_human", "human_owner", "assistant", "other_authorized_operator", "unknown_owner_outgoing"
    ] | None = None
    provider_timestamp: datetime
    received_at: datetime | None = None  # Never used to establish freshness.
    content: EventContent = Field(default_factory=EventContent)
    reply_to: str | None = Field(default=None, max_length=180)
    source_revision: int = Field(default=1, ge=1)
    sender_identity: dict = Field(default_factory=dict)
    participant_identity: dict = Field(default_factory=dict)
    provider_record_ref: str | None = Field(default=None, max_length=180)
    native_record: NativeOriginal | None = None
    reaction: ReactionContent | None = None
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    owner_addressed: bool = False

    @model_validator(mode="before")
    @classmethod
    def handoff_aliases(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for alternate, name in (("connection_id", "connector_id"), ("authorship", "author_kind"),
                                ("reply_to_source_id", "reply_to")):
            if alternate in value:
                if name in value and value[name] != value[alternate]:
                    raise ValueError(f"Conflicting {name} aliases")
                value[name] = value.pop(alternate)
        for field in ("sender_identity", "participant_identity"):
            if isinstance(value.get(field), str):
                value[field] = {"id": value[field]}
        identity = value.get("sender_identity") or {}
        if "sender_id" not in value and identity.get("id"):
            value["sender_id"] = identity["id"]
        if identity.get("id") and identity["id"] != value.get("sender_id"):
            raise ValueError("Sender identity does not match sender_id")
        origin = value.get("origin", "unknown")
        if isinstance(origin, str):
            value["origin"] = {"backfill": "history"}.get(origin.lower(), origin.lower())
        author = value.get("author_kind")
        if isinstance(author, str):
            value["author_kind"] = {"other": "contact_human", "unknown_owner": "unknown_owner_outgoing"}.get(
                author.lower(), author.lower())
        return value

    @model_validator(mode="after")
    def validate_reaction(self):
        if self.event_type.startswith("reaction.") and self.reaction is None:
            raise ValueError("Reaction events require an exact target and emoji operation")
        if self.reaction and not self.event_type.startswith("reaction."):
            raise ValueError("Reaction payload requires a reaction event")
        if self.native_record and self.provider_record_ref not in (None, self.native_record.provider_record_ref):
            raise ValueError("Native provider reference mismatch")
        return self

    @field_validator("provider_timestamp")
    @classmethod
    def timestamp_has_zone(cls, value):
        if value.tzinfo is None:
            raise ValueError("provider_timestamp requires an explicit timezone")
        return value.astimezone(UTC)

    @field_validator("expires_at", "deleted_at")
    @classmethod
    def optional_timestamp_has_zone(cls, value):
        return utc_datetime(value)


def invalidate_conversation(db: Session, conversation: Conversation, reason: str) -> None:
    """Cancel pending work after caller changes authoritative revision/control state."""
    drafts = db.scalars(select(Draft).where(
        Draft.conversation_id == conversation.id, Draft.status.in_(PENDING_DRAFTS)
    )).all()
    for draft in drafts:
        draft.status = "cancelled"
        draft.approved_hash = None
        draft.approval_expires_at = None
    intents = db.scalars(select(ScheduledIntent).where(
        ScheduledIntent.conversation_id == conversation.id, ScheduledIntent.status.in_(["scheduled", "held"])
    )).all()
    for intent in intents:
        intent.status = "cancelled"
    audit(db, conversation.workspace_id, "system", "conversation.work_invalidated", conversation.id,
          reason=reason, draft_count=len(drafts), schedule_count=len(intents))
    from .actions import invalidate_actions
    invalidate_actions(db, conversation, reason)


def hold_workspace_schedules(db, workspace):
    """Pause external execution while preserving exact approved durable schedules."""
    held_drafts = set()
    for intent in db.scalars(select(ScheduledIntent).where(
            ScheduledIntent.workspace_id == workspace.id, ScheduledIntent.status.in_(["scheduled", "held"]))):
        intent.status = "held"
        held_drafts.add(intent.draft_id)
    for draft in db.scalars(select(Draft).where(Draft.workspace_id == workspace.id, Draft.status.in_(PENDING_DRAFTS))):
        if draft.id not in held_drafts:
            draft.status, draft.approved_hash, draft.approval_expires_at = "cancelled", None, None


def resume_workspace_schedules(db, workspace, settings):
    """Resume only still-current exact approvals; pause is not permission renewal."""
    for intent in db.scalars(select(ScheduledIntent).where(
            ScheduledIntent.workspace_id == workspace.id, ScheduledIntent.status == "held")):
        draft = db.get(Draft, intent.draft_id)
        if aware(intent.expires_at) <= now():
            intent.status = "expired"
            if draft and draft.status in PENDING_DRAFTS:
                draft.status = "cancelled"
            continue
        if draft is None or draft.status != "approved":
            intent.status = "cancelled"
            continue
        draft.pause_generation = workspace.pause_generation
        try:
            _validate_current(db, draft, settings)
        except HTTPException:
            intent.status, draft.status, draft.approved_hash = "cancelled", "cancelled", None
        else:
            intent.status = "scheduled"


def _invalidate_evidence(db: Session, conversation: Conversation, message_id: str) -> None:
    from .native import invalidate_native_source
    from .tasks import invalidate_task_sources
    invalidate_native_source(db, message_id)
    invalidate_task_sources(db, conversation, message_id)
    for memory in db.scalars(select(Memory).where(Memory.conversation_id == conversation.id)):
        if message_id in (memory.source_message_ids or []):
            memory.status = "invalidated"
            memory.version += 1
    for profile in db.scalars(select(StyleProfile).where(StyleProfile.conversation_id == conversation.id)):
        if message_id in (profile.evidence_message_ids or []):
            profile.features = {}
            profile.evidence_message_ids = []
            profile.sample_count = 0
            profile.sufficiency = "weak"
            profile.version += 1


def ingest_event(db: Session, event: CanonicalEvent) -> dict:
    """Accept a trusted connector observation; caller commits the complete transaction."""
    connector = db.get(Connector, event.connector_id)
    if connector is None or connector.status != "connected":
        raise HTTPException(404, "Connected account not found")
    if ((event.provider is not None and event.provider != connector.provider)
            or (event.account_id is not None and event.account_id != connector.account_id)):
        raise HTTPException(403, "Connector account mapping mismatch")
    conversation = db.get(Conversation, event.conversation_id)
    if (conversation is None or conversation.connector_id != connector.id
            or conversation.workspace_id != connector.workspace_id):
        raise HTTPException(404, "Conversation not found for connector")
    permission = db.scalar(select(Permission).where(Permission.conversation_id == conversation.id))
    if (permission is None or not permission.read or not permission.retain
            or (permission.expires_at and aware(permission.expires_at) <= now())):
        return {"status": "ignored", "reason": "read_and_retain_permission_required"}
    event_key = content_hash(f"{connector.id}\0{conversation.id}\0{event.event_id}")
    if db.scalar(select(MessageEvent).where(MessageEvent.event_id == event_key)):
        return {"status": "duplicate"}
    if event.event_type.startswith("reaction."):
        from .native import ingest_reaction
        return ingest_reaction(db, event, connector, conversation, permission, event_key)
    message = db.scalar(select(Message).where(
        Message.connector_id == connector.id, Message.conversation_id == conversation.id,
        Message.provider_message_id == event.provider_message_id,
    ))
    if event.event_type == "message.created" and message is not None:
        return {"status": "duplicate", "message_id": message.id}
    if event.event_type != "message.created" and message is None:
        return {"status": "ignored", "reason": "unknown_message"}
    if message is not None and event.source_revision <= message.revision:
        return {"status": "duplicate", "message_id": message.id}
    if message is not None and (message.direction != event.direction or message.sender_id != event.sender_id):
        raise HTTPException(409, "Edit/deletion message identity mismatch")
    if message is not None and message.deleted and event.event_type != "message.deleted":
        return {"status": "ignored", "reason": "deleted_message"}

    author = "contact_human"
    if event.direction == "outbound":
        attempt = db.scalar(select(SendAttempt).where(
            SendAttempt.conversation_id == conversation.id,
            SendAttempt.provider_message_id == event.provider_message_id,
        ))
        if attempt:
            author = "assistant"
            if attempt.status in {"dispatching", "uncertain"}:
                attempt.status = "accepted"
                draft = db.get(Draft, attempt.draft_id)
                if draft:
                    draft.status = "accepted"
        else:
            from .actions import assistant_message_echo
            if assistant_message_echo(db, event):
                author = "assistant"
            elif event.sender_id != connector.owner_sender_id:
                author = "other_authorized_operator" if event.author_kind == "other_authorized_operator" else "unknown_owner_outgoing"
            elif event.author_kind == "human_owner":
                author = "human_owner"
            else:
                author = "unknown_owner_outgoing"

    received = now()
    if message is None:
        message = Message(
            id=uid(), workspace_id=connector.workspace_id, connector_id=connector.id,
            conversation_id=conversation.id, provider_message_id=event.provider_message_id,
            sender_id=event.sender_id, direction=event.direction, origin=event.origin,
            author_kind=author, text=event.content.text, provider_timestamp=event.provider_timestamp,
            received_at=received, reply_to=event.reply_to, revision=event.source_revision,
            excluded_from_learning=author != "human_owner",
        )
        if event.deleted_at:
            message.deleted = True
            message.text = ""
            message.excluded_from_learning = True
        db.add(message)
        db.flush()
    else:
        message.revision = event.source_revision
        if event.event_type == "message.deleted":
            message.text = ""
            message.deleted = True
            message.excluded_from_learning = True
        else:
            message.text = event.content.text
        _invalidate_evidence(db, conversation, message.id)
    from .native import store_native_observation
    store_native_observation(db, message, connector, conversation, event)
    # An assistant echo is an observation of our existing send, not new human context.
    if author != "assistant" or event.event_type != "message.created":
        conversation.revision += 1
        if event.direction == "outbound" and event.origin == "live":
            conversation.control_epoch += 1
            conversation.control_state = "HUMAN_TAKEOVER"
        invalidate_conversation(db, conversation, "observed_message_change")
    live_eligible = (event.origin == "live" and event.direction == "inbound"
                     and abs((received - event.provider_timestamp).total_seconds()) <= 120
                     and event.provider_timestamp >= aware(connector.created_at)
                     and event.event_type == "message.created" and not message.deleted
                     and (event.expires_at is None or event.expires_at > received))
    if live_eligible:
        if conversation.last_inbound_at is None or event.provider_timestamp > aware(conversation.last_inbound_at):
            conversation.last_inbound_at = event.provider_timestamp
    if event.event_type == "message.created" and not message.deleted:
        from .people import auto_save_contact
        auto_save_contact(db, conversation, message)
    db.add(MessageEvent(workspace_id=connector.workspace_id, event_id=event_key,
                        conversation_id=conversation.id, message_id=message.id,
                        event_type=event.event_type, source_revision=event.source_revision))
    db.add(Outbox(workspace_id=connector.workspace_id, kind="message.accepted", aggregate_id=message.id,
                  payload={"message_id": message.id, "conversation_id": conversation.id,
                           "event_id": event_key, "live_eligible": live_eligible,
                           "revision": conversation.revision}))
    return {"status": "accepted", "message_id": message.id, "author_kind": author,
            "live_eligible": live_eligible, "automatic_reply": False}


@router.post("/internal/connector-events", dependencies=[Depends(require_internal)])
def connector_event(body: CanonicalEvent, db: Session = Depends(get_db)):
    connector = db.get(Connector, body.connector_id)
    if connector is None:
        raise HTTPException(404, "Connector not found")
    workspace_id = connector.workspace_id
    db.rollback()
    with submit_guard(workspace_id):
        result = ingest_event(db, body)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # Concurrent redelivery may win the same timestamp-independent unique key.
            observed = db.scalar(select(MessageEvent).where(
                MessageEvent.event_id == content_hash(f"{body.connector_id}\0{body.conversation_id}\0{body.event_id}"),
                MessageEvent.workspace_id == workspace_id,
                MessageEvent.conversation_id == body.conversation_id,
            ))
            if observed:
                return {"status": "duplicate", "message_id": observed.message_id}
            message = db.scalar(select(Message).where(Message.connector_id == body.connector_id,
                                                      Message.conversation_id == body.conversation_id,
                                                      Message.provider_message_id == body.provider_message_id))
            if message:
                return {"status": "duplicate", "message_id": message.id}
            raise
        return result


def _draft_for(db: Session, user: User, draft_id: str) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(404, "Draft not found")
    workspace_for(db, user, draft.workspace_id)
    return draft


def serialized_draft(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        db = kwargs["db"]
        draft_id = kwargs.get("draft_id") or kwargs["body"].draft_id
        workspace_id = db.scalar(select(Draft.workspace_id).where(Draft.id == draft_id))
        if workspace_id is None:
            raise HTTPException(404, "Draft not found")
        db.rollback()
        with submit_guard(workspace_id):
            db.expire_all()
            return function(*args, **kwargs)
    return guarded


def draft_payload(draft: Draft) -> dict:
    return {"id": draft.id, "conversation_id": draft.conversation_id, "recipient_id": draft.recipient_id,
            "text": draft.text, "status": draft.status, "content_hash": draft.content_hash,
            "missing_facts": draft.missing_facts, "evidence_message_ids": draft.evidence_message_ids,
            "approved_hash": draft.approved_hash, "approval_expires_at": draft.approval_expires_at}


def _validate_current(db: Session, draft: Draft, settings, approved: bool = True) -> tuple:
    conversation = db.get(Conversation, draft.conversation_id)
    workspace = db.get(Workspace, draft.workspace_id)
    connector = db.get(Connector, conversation.connector_id) if conversation else None
    if (conversation is None or workspace is None or connector is None
            or conversation.workspace_id != workspace.id or connector.workspace_id != workspace.id):
        raise HTTPException(409, "Draft account mapping changed")
    if workspace.paused:
        raise HTTPException(409, "All replies are paused")
    if conversation.control_state in {"AI_OFF", "READ_ONLY", "HUMAN_TAKEOVER", "RECONNECT_REVIEW"}:
        raise HTTPException(409, "Conversation control state blocks sending")
    permission = permission_for(db, conversation, "send")
    for capability in ("read", "retain", "draft"):
        if not getattr(permission, capability):
            raise HTTPException(403, f"Conversation {capability} permission required")
    if (connector.status != "connected" or connector.lease_expires_at is None
            or aware(connector.lease_expires_at) <= now()):
        raise HTTPException(409, "Connector disconnected or lease expired")
    if (draft.conversation_revision != conversation.revision or draft.control_epoch != conversation.control_epoch
            or draft.permission_version != permission.version or draft.pause_generation != workspace.pause_generation
            or draft.connector_fence != connector.fence):
        raise HTTPException(409, "Draft is stale; generate and approve a current draft")
    if draft.profile_version:
        profile = db.scalar(select(StyleProfile).where(StyleProfile.conversation_id == conversation.id)
                            .order_by(StyleProfile.version.desc()))
        if profile is None or profile.version != draft.profile_version:
            raise HTTPException(409, "Style profile changed")
    if draft.recipient_id != conversation.provider_chat_id:
        raise HTTPException(409, "Draft recipient changed")
    if content_hash(draft.text) != draft.content_hash or not draft.text.strip() or len(draft.text) > 4096:
        raise HTTPException(409, "Draft content invalid or changed")
    if draft.missing_facts:
        raise HTTPException(409, "Owner must resolve missing facts before approval")
    if draft.context_expires_at and aware(draft.context_expires_at) <= now():
        raise HTTPException(409, "Draft context expired; generate a current draft")
    for message_id in draft.evidence_message_ids or []:
        from .native import message_available
        evidence = db.get(Message, message_id)
        if (evidence is None or evidence.conversation_id != conversation.id or evidence.deleted
                or evidence.workspace_id != workspace.id or not message_available(db, evidence)):
            raise HTTPException(409, "Draft evidence is no longer available")
    if approved and (draft.approved_hash != draft.content_hash or not draft.approval_expires_at
                     or aware(draft.approval_expires_at) <= now()):
        raise HTTPException(409, "Exact-content approval is missing or expired")
    if conversation.recipient_opted_out:
        raise HTTPException(403, "Recipient opted out")
    if conversation.kind == "group" and (not conversation.group_send_allowed
                                         or connector.capabilities.get("group_send") != "supported"):
        raise HTTPException(403, "Group sending is unavailable or not authorized")
    if connector.capabilities.get("send_text") != "supported":
        raise HTTPException(403, "Connector does not support sending")
    if connector.provider == "whatsapp_cloud":
        if not settings.enable_external_sends:
            raise HTTPException(403, "External sending is disabled")
        if (not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id
                or connector.account_id != settings.whatsapp_phone_number_id):
            raise HTTPException(409, "WhatsApp account credentials are not configured")
        if conversation.kind != "contact":
            raise HTTPException(403, "Cloud API adapter supports contact text messages only")
        if not conversation.recipient_opted_in:
            raise HTTPException(403, "Recipient opt-in required")
        if (not conversation.last_inbound_at or now() - aware(conversation.last_inbound_at) >= timedelta(hours=24)
                or aware(conversation.last_inbound_at) > now()):
            raise HTTPException(403, "Approved templates required outside the customer-service window")
    elif connector.provider != "mock":
        raise HTTPException(403, "Sending adapter unavailable")
    if connector.provider == "mock" and settings.environment == "production":
        raise HTTPException(403, "Mock sending is unavailable in production")
    if draft.automation_id:
        from .automation import validate_automated_draft
        validate_automated_draft(db, draft, conversation, workspace)
    return conversation, workspace, connector


class EditDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4096)


@router.patch("/drafts/{draft_id}")
@serialized_draft
def edit_draft(draft_id: str, body: EditDraft, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    draft = _draft_for(db, user, draft_id)
    if draft.status not in PENDING_DRAFTS:
        raise HTTPException(409, "Draft cannot be edited after cancellation or dispatch")
    draft.text = body.text
    draft.content_hash = content_hash(body.text)
    draft.approved_hash = None
    draft.approval_expires_at = None
    draft.automation_id = None
    draft.automation_version = None
    draft.missing_facts = []  # This is explicit owner-authored content, never a model-invented fact.
    draft.status = "needs_approval"
    for intent in db.scalars(select(ScheduledIntent).where(ScheduledIntent.draft_id == draft.id,
                                                          ScheduledIntent.status.in_(["scheduled", "held"]))):
        intent.status = "cancelled"
    audit(db, draft.workspace_id, user.id, "draft.edited", draft.id)
    db.commit()
    return draft_payload(draft)


class ApproveDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_in_seconds: int = Field(default=3600, ge=60, le=86400)


@router.post("/drafts/{draft_id}/approve")
@serialized_draft
def approve_draft(draft_id: str, body: ApproveDraft, request: Request,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    draft = _draft_for(db, user, draft_id)
    if draft.status not in PENDING_DRAFTS:
        raise HTTPException(409, "Draft is not pending owner approval")
    if not hmac.compare_digest(body.content_hash, draft.content_hash):
        raise HTTPException(409, "Approval must match the exact reviewed content")
    _validate_current(db, draft, request.app.state.settings, approved=False)
    draft.approved_hash = draft.content_hash
    draft.approval_expires_at = now() + timedelta(seconds=body.expires_in_seconds)
    if draft.context_expires_at:
        draft.approval_expires_at = min(draft.approval_expires_at, aware(draft.context_expires_at))
    draft.status = "approved"
    audit(db, draft.workspace_id, user.id, "draft.approved", draft.id, content_hash=draft.content_hash)
    db.commit()
    return draft_payload(draft)


@router.post("/drafts/{draft_id}/reject")
@serialized_draft
def reject_draft(draft_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    draft = _draft_for(db, user, draft_id)
    if draft.status not in PENDING_DRAFTS:
        raise HTTPException(409, "Draft is already final or dispatch has begun")
    draft.status = "rejected"
    draft.approved_hash = None
    draft.approval_expires_at = None
    for intent in db.scalars(select(ScheduledIntent).where(ScheduledIntent.draft_id == draft.id,
                                                          ScheduledIntent.status.in_(["scheduled", "held"]))):
        intent.status = "cancelled"
    audit(db, draft.workspace_id, user.id, "draft.rejected", draft.id)
    db.commit()
    return draft_payload(draft)


def attempt_payload(attempt: SendAttempt) -> dict:
    return {"attempt_id": attempt.id, "draft_id": attempt.draft_id, "status": attempt.status,
            "provider_message_id": attempt.provider_message_id, "error_code": attempt.error_code}


def _validate_intent(db: Session, draft: Draft, intent_id: str | None):
    if intent_id is None:
        # A scheduled draft goes through its durable intent, never an early manual send.
        active = db.scalar(select(ScheduledIntent).where(ScheduledIntent.draft_id == draft.id,
                                                        ScheduledIntent.status.in_(["scheduled", "held"])))
        if active:
            raise HTTPException(409, "Draft belongs to a scheduled intent; cancel or wait for execution")
        return
    intent = db.get(ScheduledIntent, intent_id)
    if (intent is None or intent.draft_id != draft.id or intent.workspace_id != draft.workspace_id
            or intent.status != "scheduled" or aware(intent.due_at) > now()
            or aware(intent.expires_at) <= now()):
        raise HTTPException(409, "Scheduled intent cancelled, expired, not due, or changed")


async def _transport_send(settings, provider: str, account_id: str, recipient: str, text: str, attempt_id: str,
                          final_check=None):
    if provider == "mock":
        if final_check:
            final_check()
        return "accepted", f"mock:{attempt_id}", None
    endpoint = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{account_id}/messages"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15), follow_redirects=False) as client:
            # Client/TLS preparation precedes the latest-state check. There is no
            # queued asynchronous wait between this check and beginning submission.
            if final_check:
                final_check()
            response = await client.post(endpoint,
                                         headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
                                         json={"messaging_product": "whatsapp", "recipient_type": "individual",
                                               "to": recipient, "type": "text", "text": {"body": text}})
        if response.status_code >= 400:
            return "failed", None, f"provider_http_{response.status_code}"
        try:
            message_id = response.json()["messages"][0]["id"]
            if not isinstance(message_id, str) or not 1 <= len(message_id) <= 180:
                raise ValueError("Invalid provider reference")
        except (ValueError, KeyError, IndexError, TypeError):
            return "uncertain", None, "missing_provider_reference"
        return "accepted", message_id, None
    except (httpx.HTTPError, asyncio.TimeoutError):
        # Even a disconnected socket can follow successful provider acceptance.
        return "uncertain", None, "transport_outcome_unknown"


async def dispatch_draft(session_factory, settings, draft_id: str, actor_id: str = "scheduler",
                         scheduled_intent_id: str | None = None) -> dict:
    """Claim once, recheck current state, submit once; retries return the saved outcome."""
    with session_factory() as db:
        workspace_id = db.scalar(select(Draft.workspace_id).where(Draft.id == draft_id))
    if workspace_id is None:
        raise HTTPException(404, "Draft not found")
    with submit_guard(workspace_id), session_factory() as db:
        existing = db.scalar(select(SendAttempt).where(SendAttempt.draft_id == draft_id))
        if existing:
            return attempt_payload(existing)
        draft = db.get(Draft, draft_id)
        if draft is None:
            raise HTTPException(404, "Draft not found")
        if draft.status != "approved":
            raise HTTPException(409, "Approved draft required")
        _validate_intent(db, draft, scheduled_intent_id)
        _, _, connector = _validate_current(db, draft, settings)
        from .companion import reserve_action_budget, settle_action_budget
        reserve_action_budget(db, workspace_id, f"draft:{draft_id}", "SEND_TEXT")
        provider, account_id, recipient, text = connector.provider, connector.account_id, draft.recipient_id, draft.text
        claimed = db.execute(update(Draft).where(Draft.id == draft.id, Draft.status == "approved")
                             .values(status="dispatching")).rowcount
        if not claimed:
            db.rollback()
            raise HTTPException(409, "Draft already claimed")
        attempt = SendAttempt(id=uid(), workspace_id=draft.workspace_id, draft_id=draft.id,
                              conversation_id=draft.conversation_id, status="dispatching",
                              content_hash=draft.content_hash, connector_fence=draft.connector_fence)
        db.add(attempt)
        audit(db, draft.workspace_id, actor_id, "send.claimed", attempt.id)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(SendAttempt).where(SendAttempt.draft_id == draft_id))
            if existing:
                return attempt_payload(existing)
            raise
        attempt_id = attempt.id
    def final_check():
        # Separate transaction immediately before submission. Never hold SQL locks
        # through a provider call; later control events cannot recall an in-flight send.
        with submit_guard(workspace_id), session_factory() as db:
            draft = db.get(Draft, draft_id)
            attempt = db.get(SendAttempt, attempt_id)
            if draft is None or attempt is None:
                raise HTTPException(409, "Dispatch ledger removed before submission")
            _validate_intent(db, draft, scheduled_intent_id)
            _validate_current(db, draft, settings)
            reserve_action_budget(db, workspace_id, f"draft:{draft_id}", "SEND_TEXT")
            if draft.status != "dispatching" or attempt.status != "dispatching":
                raise HTTPException(409, "Dispatch claim is no longer current")
            audit(db, draft.workspace_id, actor_id, "send.submit_started", attempt.id)
            db.commit()
    try:
        outcome, provider_id, error_code = await _transport_send(
            settings, provider, account_id, recipient, text, attempt_id, final_check)
    except HTTPException as error:
        with session_factory() as db:
            attempt = db.get(SendAttempt, attempt_id)
            draft = db.get(Draft, draft_id)
            if attempt is None or draft is None:
                return {"attempt_id": attempt_id, "draft_id": draft_id, "status": "blocked",
                        "provider_message_id": None, "error_code": "ledger_removed"}
            attempt.status = "blocked"
            attempt.error_code = "latest_state_rejected"
            draft.status = "cancelled"
            settle_action_budget(db, workspace_id, f"draft:{draft_id}", "released")
            db.commit()
            return {**attempt_payload(attempt), "reason": error.detail}
    with session_factory() as db:
        attempt = db.get(SendAttempt, attempt_id)
        draft = db.get(Draft, draft_id)
        if attempt is None or draft is None:
            # Data erasure may finish after submission. Do not restore deleted data
            # or retry an outward action whose provider outcome is already fixed.
            return {"attempt_id": attempt_id, "draft_id": draft_id, "status": "uncertain",
                    "provider_message_id": provider_id, "error_code": "ledger_removed_after_submit"}
        # A correlated webhook receipt may have arrived while the HTTP call was in flight.
        if attempt.status not in {"accepted", "delivered", "failed"}:
            attempt.status, attempt.provider_message_id, attempt.error_code = outcome, provider_id, error_code
            draft.status = outcome
        elif provider_id and not attempt.provider_message_id:
            attempt.provider_message_id = provider_id
        audit(db, draft.workspace_id, actor_id, "send.outcome", attempt.id, status=attempt.status)
        settlement = "uncertain" if attempt.status in {"uncertain", "dispatching"} else (
            "consumed" if attempt.status in {"accepted", "delivered"} else "released")
        settle_action_budget(db, workspace_id, f"draft:{draft_id}", settlement)
        db.commit()
        return attempt_payload(attempt)


@router.post("/drafts/{draft_id}/dispatch")
async def dispatch_route(draft_id: str, request: Request, user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    _draft_for(db, user, draft_id)
    db.rollback()
    return await dispatch_draft(request.app.state.session_factory, request.app.state.settings, draft_id, user.id)


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: str
    provider_message_id: str = Field(min_length=1, max_length=180)
    status: Literal["accepted", "delivered", "failed"]


class LeaseHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: str
    fence: int = Field(ge=1)
    lease_seconds: int = Field(default=300, ge=30, le=3600)


@router.post("/internal/connector-leases/renew", dependencies=[Depends(require_internal)])
def renew_lease(body: LeaseHeartbeat, db: Session = Depends(get_db)):
    connector = db.get(Connector, body.connector_id)
    if connector is None:
        raise HTTPException(404, "Connector not found")
    workspace_id = connector.workspace_id
    db.rollback()
    with submit_guard(workspace_id):
        connector = db.get(Connector, body.connector_id)
        if (connector.status != "connected" or connector.fence != body.fence
                or connector.lease_expires_at is None or aware(connector.lease_expires_at) <= now()):
            raise HTTPException(409, "Lease is stale or expired; reconnect and verify account")
        connector.lease_expires_at = now() + timedelta(seconds=body.lease_seconds)
        db.commit()
        return {"connector_id": connector.id, "fence": connector.fence,
                "lease_expires_at": aware(connector.lease_expires_at)}


@router.post("/internal/send-receipts", dependencies=[Depends(require_internal)])
def reconcile_receipt(body: Receipt, db: Session = Depends(get_db)):
    connector = db.get(Connector, body.connector_id)
    if connector is None:
        raise HTTPException(404, "Connector not found")
    attempt = db.scalar(select(SendAttempt).join(Conversation,
                       Conversation.id == SendAttempt.conversation_id).where(
                           Conversation.connector_id == connector.id,
                           SendAttempt.workspace_id == connector.workspace_id,
                           SendAttempt.provider_message_id == body.provider_message_id))
    if attempt is None:
        raise HTTPException(404, "No correlated send attempt; review uncertainty without resending")
    if attempt.status in {"dispatching", "uncertain", "accepted"}:
        attempt.status = body.status
        db.get(Draft, attempt.draft_id).status = body.status
        audit(db, attempt.workspace_id, "connector", "send.receipt", attempt.id, status=body.status)
        db.commit()
    return attempt_payload(attempt)


class ScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    idempotency_key: str = Field(min_length=8, max_length=120)
    due_at: datetime
    expires_at: datetime
    timezone: str = "Asia/Kolkata"
    original_expression: str = Field(default="", max_length=200)

    @field_validator("due_at", "expires_at")
    @classmethod
    def explicit_timestamp(cls, value):
        if value.tzinfo is None:
            raise ValueError("Schedule times require timezone offsets")
        return value.astimezone(UTC)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Unknown IANA timezone") from error
        return value


def schedule_payload(intent: ScheduledIntent) -> dict:
    return {"id": intent.id, "draft_id": intent.draft_id, "conversation_id": intent.conversation_id,
            "idempotency_key": intent.idempotency_key, "due_at": aware(intent.due_at),
            "expires_at": aware(intent.expires_at), "timezone": intent.timezone,
            "original_expression": intent.original_expression, "status": intent.status,
            "workflow_registered": intent.workflow_registered}


@router.post("/scheduled-intents", status_code=201)
@serialized_draft
def schedule(body: ScheduleBody, request: Request, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    draft = _draft_for(db, user, body.draft_id)
    existing = db.scalar(select(ScheduledIntent).where(ScheduledIntent.workspace_id == draft.workspace_id,
                                                       ScheduledIntent.idempotency_key == body.idempotency_key))
    if existing:
        if (existing.draft_id != body.draft_id or aware(existing.due_at) != body.due_at
                or aware(existing.expires_at) != body.expires_at or existing.timezone != body.timezone):
            raise HTTPException(409, "Idempotency key already used for another schedule")
        return schedule_payload(existing)
    if draft.status != "approved":
        raise HTTPException(409, "Scheduling requires an exact-content approved draft")
    _validate_current(db, draft, request.app.state.settings)
    if body.due_at <= now() or body.expires_at <= body.due_at:
        raise HTTPException(422, "Schedule must be future-dated with an expiry after the due time")
    if body.due_at >= aware(draft.approval_expires_at):
        raise HTTPException(409, "Approval expires before the scheduled execution")
    if draft.context_expires_at and body.due_at >= aware(draft.context_expires_at):
        raise HTTPException(409, "Draft context expires before the scheduled execution")
    active = db.scalar(select(ScheduledIntent).where(ScheduledIntent.draft_id == draft.id,
                                                    ScheduledIntent.status.in_(["scheduled", "held"])))
    if active:
        raise HTTPException(409, "Draft already has an active scheduled intent")
    intent = ScheduledIntent(id=uid(), workspace_id=draft.workspace_id, draft_id=draft.id,
                             conversation_id=draft.conversation_id, idempotency_key=body.idempotency_key,
                             due_at=body.due_at, expires_at=body.expires_at, timezone=body.timezone,
                             original_expression=body.original_expression)
    db.add(intent)
    db.add(Outbox(workspace_id=draft.workspace_id, kind="schedule.register", aggregate_id=intent.id,
                  payload={"intent_id": intent.id, "due_at": body.due_at.isoformat()}))
    audit(db, draft.workspace_id, user.id, "schedule.created", intent.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(ScheduledIntent).where(ScheduledIntent.workspace_id == draft.workspace_id,
                                                           ScheduledIntent.idempotency_key == body.idempotency_key))
        if existing and existing.draft_id == body.draft_id and aware(existing.due_at) == body.due_at:
            return schedule_payload(existing)
        raise HTTPException(409, "Schedule idempotency conflict") from None
    return schedule_payload(intent)


@router.get("/scheduled-intents")
def schedules(workspace_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    workspace_for(db, user, workspace_id)
    return [schedule_payload(row) for row in db.scalars(select(ScheduledIntent).where(
        ScheduledIntent.workspace_id == workspace_id).order_by(ScheduledIntent.due_at))]


@router.delete("/scheduled-intents/{intent_id}")
def cancel_schedule(intent_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    intent = db.get(ScheduledIntent, intent_id)
    if intent is None:
        raise HTTPException(404, "Schedule not found")
    workspace_for(db, user, intent.workspace_id)
    workspace_id = intent.workspace_id
    db.rollback()
    with submit_guard(workspace_id):
        intent = db.get(ScheduledIntent, intent_id)
        if intent.status in {"scheduled", "held"}:
            attempt = db.scalar(select(SendAttempt).where(SendAttempt.draft_id == intent.draft_id))
            if attempt:
                started = db.scalar(select(AuditEvent.id).where(AuditEvent.resource_id == attempt.id,
                                                               AuditEvent.action == "send.submit_started"))
                if started:
                    raise HTTPException(409, "Submission already started; delivery cannot be recalled")
            intent.status = "cancelled"
            audit(db, intent.workspace_id, user.id, "schedule.cancelled", intent.id)
            db.commit()
        return schedule_payload(intent)


async def process_due(session_factory, settings, intent_id: str | None = None) -> list[dict]:
    """Temporal activity/development runner: SQL is the source of truth and send ledger."""
    with session_factory() as db:
        query = select(ScheduledIntent).where(ScheduledIntent.status.in_(["scheduled", "held"]), ScheduledIntent.due_at <= now())
        if intent_id:
            query = query.where(ScheduledIntent.id == intent_id)
        ids = list(db.scalars(query.with_only_columns(ScheduledIntent.id)))
    results = []
    for selected_id in ids:
        with session_factory() as db:
            intent = db.get(ScheduledIntent, selected_id)
            if intent is None or intent.status not in {"scheduled", "held"}:
                continue
            if aware(intent.expires_at) <= now():
                intent.status = "expired"
                db.commit()
                results.append({"intent_id": selected_id, "status": "expired"})
                continue
            workspace = db.get(Workspace, intent.workspace_id)
            if workspace and workspace.paused:
                intent.status = "held"
                db.commit()
                results.append({"intent_id": selected_id, "status": "held", "reason_code": "GLOBAL_PAUSE"})
                continue
            if intent.status == "held":
                # Resume is an owner control that revalidates the saved exact
                # approval. A worker cannot independently renew stale authority.
                continue
            draft_id = intent.draft_id
        try:
            result = await dispatch_draft(session_factory, settings, draft_id, scheduled_intent_id=selected_id)
            status = result["status"]
        except HTTPException as error:
            result = {"reason": error.detail}
            status = "cancelled"
        with session_factory() as db:
            intent = db.get(ScheduledIntent, selected_id)
            # A race with cancellation must not restore a cancelled intent.
            if intent.status == "scheduled":
                intent.status = status
                db.commit()
        results.append({**result, "intent_id": selected_id, "status": status})
    return results


@router.post("/internal/scheduled-intents/run-due", dependencies=[Depends(require_internal)])
async def run_due(request: Request):
    return await process_due(request.app.state.session_factory, request.app.state.settings)
