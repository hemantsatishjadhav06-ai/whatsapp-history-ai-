"""Bounded automatic operations and an immutable, non-retrying action ledger.

The foundation transport is explicitly simulated. Actual account operations must
use a capability-evidenced adapter and the current dispatch authority boundary.
"""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from .access import audit, conversation_for, permission_for, workspace_for
from .action_models import AutomationGrant, ForwardRoute, OutboundAction, SubmissionAttempt
from .auth import get_current_user
from .core import serialized_control
from .db import aware, get_db, now, uid
from .messaging import require_internal, submit_guard
from .models import Automation, Connector, Conversation, Memory, Message, Outbox, Permission, Suppression, User, Workspace
from .storage_authority import lock_workspace, SUBMISSION_RECOVERY_SECONDS

router = APIRouter(tags=["automatic actions"])
KINDS = {"SEND_TEXT", "QUOTE", "REACTION", "FORWARD"}
CAPABILITIES = {"SEND_TEXT": "send_text", "QUOTE": "quoted_reply",
                "REACTION": "emoji_reaction", "FORWARD": "native_forward"}
INTENTS = {"acknowledgement", "allowed_clarification", "verified_status", "factual_answer", "forwarding"}
PALETTE = {"👍", "❤️", "❤", "🙏", "😊", "✅", "😂", "🎉", "💪", "🙌", "💙", "👌"}
PENDING = {"ready", "held"}
COUNTED = PENDING | {"submitting", "accepted", "delivered", "read", "uncertain"}
SAFE_ACK = {"Thanks!", "Thank you!", "Got it, thanks.", "Noted, thanks.", "Received, thanks.",
            "Thanks for the update.", "धन्यवाद!", "ठीक आहे, धन्यवाद.", "शुक्रिया!"}
SAFE_CLARIFY = {"What time would work for you?", "Could you clarify that?", "Which date do you mean?",
                "Could you share the details?", "What would you like me to confirm?"}
# Independent deterministic semantics: habits never override a veto. This pilot
# permits only short, unambiguously positive acknowledgements as reaction targets.
REACTION_POSITIVE = re.compile(r"\b(thanks|thank you|great|perfect|well done|congratulations|congrats|awesome|received|done)\b", re.I)
REACTION_VETO = re.compile(r"\b(died|death|funeral|passed away|bereavement|cancer|hospital|sad|sorry|depressed|"
                          r"payment|pay|money|transfer|bank|contract|agreement|legal|consent|approve|accept|"
                          r"price|invoice|urgent|emergency|not|never|cancel|failure|failed)\b|[₹$€£]", re.I)
TEXT_COMMITMENT = re.compile(r"\b(i will|i'll|i agree|i accept|i approve|confirmed|guarantee|promise|"
                            r"transfer|payment|contract|password|otp|bank account)\b|[₹$€£]", re.I)


def fail(code: str, status: int = 403):
    raise HTTPException(status, {"reason_code": code})


def hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GrantInput(Payload):
    conversation_id: str
    enabled: bool = True
    mode: Literal["AUTO"] = "AUTO"
    allowed_actions: list[Literal["SEND_TEXT", "QUOTE", "REACTION", "FORWARD"]] = Field(min_length=1, max_length=4)
    allowed_intents: list[str] = Field(min_length=1, max_length=5)
    reaction_palette: list[str] = Field(default_factory=list, max_length=12)
    forward_route_ids: list[str] = Field(default_factory=list, max_length=20)
    require_grounded_facts: Literal[True] = True
    max_outgoing_per_hour: int = Field(default=6, ge=1, le=30)
    max_trigger_age_seconds: int = Field(default=300, ge=30, le=600)
    quiet_start: str = "21:00"
    quiet_end: str = "09:00"
    timezone: str = "Asia/Kolkata"
    expires_at: datetime
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("allowed_actions", mode="before")
    @classmethod
    def action_names(cls, value):
        names = {"send_text": "SEND_TEXT", "quoted_reply": "QUOTE", "reaction": "REACTION", "native_forward": "FORWARD"}
        return [names.get(item, item) for item in value] if isinstance(value, list) else value

    @field_validator("allowed_intents")
    @classmethod
    def known_intents(cls, value):
        if not set(value) <= INTENTS:
            raise ValueError("Only bounded acknowledgement, clarification, factual and forwarding intents are supported")
        return sorted(set(value))

    @field_validator("reaction_palette")
    @classmethod
    def valid_palette(cls, value):
        if not set(value) <= PALETTE:
            raise ValueError("Unsupported reaction emoji")
        return sorted(set(value))

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def clock(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Use HH:MM quiet hours")
        return value

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown timezone")
        return value

    @field_validator("expires_at")
    @classmethod
    def expiry(cls, value):
        if value.tzinfo is None:
            raise ValueError("Expiry requires timezone")
        return value.astimezone(UTC)


class RouteInput(Payload):
    source_conversation_id: str
    destination_conversation_id: str
    categories: list[Literal["owner_selected_text"]] = Field(default_factory=lambda: ["owner_selected_text"], min_length=1, max_length=1)
    audience: Literal["contact", "group"]
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def expiry(cls, value):
        if value.tzinfo is None:
            raise ValueError("Expiry requires timezone")
        return value.astimezone(UTC)


class RouteUpdate(Payload):
    enabled: bool
    expected_version: int = Field(ge=1)


class ActionProposal(Payload):
    kind: Literal["SEND_TEXT", "QUOTE", "REACTION", "FORWARD"]
    intent: Literal["acknowledgement", "allowed_clarification", "verified_status", "factual_answer", "forwarding"]
    trigger_message_id: str | None = None
    text: str = Field(default="", max_length=4000)
    emoji: str | None = Field(default=None, max_length=32)
    target_message_id: str | None = None
    route_id: str | None = None
    evidence_message_ids: list[str] = Field(default_factory=list, max_length=30)
    missing_facts: list[str] = Field(default_factory=list, max_length=20)
    risk_flags: list[str] = Field(default_factory=list, max_length=20)
    expected_context_revision: int | None = Field(default=None, ge=0)
    expected_control_epoch: int | None = Field(default=None, ge=0)
    expected_policy_version: int | None = Field(default=None, ge=1)


class ActionInput(ActionProposal):
    conversation_id: str


def grant_public(row):
    return {key: getattr(row, key) for key in ("id", "conversation_id", "connector_id", "enabled", "mode",
            "allowed_actions", "allowed_intents", "reaction_palette", "forward_route_ids", "require_grounded_facts",
            "max_outgoing_per_hour", "max_trigger_age_seconds", "quiet_start", "quiet_end", "timezone", "expires_at", "version")}


def route_public(row):
    return {key: getattr(row, key) for key in ("id", "source_conversation_id", "destination_conversation_id",
            "connector_id", "categories", "audience", "enabled", "expires_at", "version")}


def action_public(row, *, include_payload=True):
    result = {key: getattr(row, key) for key in ("id", "conversation_id", "destination_conversation_id", "kind",
             "intent", "recipient_id", "payload_hash", "status", "reason_code", "expires_at", "created_at",
             "trigger_message_id", "target_message_id", "evidence_message_ids", "authorized_job_id")}
    if include_payload:
        result["payload"] = row.payload
    result["transport"] = "simulation_only"
    return result


def _scoped_permission(db, conversation, *capabilities):
    permission = db.scalar(select(Permission).where(Permission.conversation_id == conversation.id,
                            Permission.workspace_id == conversation.workspace_id))
    if permission is None or (permission.expires_at and aware(permission.expires_at) <= now()):
        fail("SCOPE_DENIED")
    if not all(getattr(permission, capability) for capability in capabilities):
        fail("SCOPE_DENIED")
    return permission


def _snapshot(conversation, permission):
    return {"revision": conversation.revision, "control_epoch": conversation.control_epoch,
            "permission_version": permission.version, "recipient_id": conversation.provider_chat_id,
            "kind": conversation.kind}


def _chat_state(db, conversation, *, outgoing=True):
    if conversation.control_state == "HUMAN_TAKEOVER":
        fail("HUMAN_TAKEOVER", 409)
    if conversation.control_state in {"RECONNECT_REVIEW", "PAUSED"}:
        fail("CONTEXT_STALE", 409)
    if outgoing and conversation.recipient_opted_out:
        fail("SCOPE_DENIED")
    if outgoing and conversation.kind == "group" and not conversation.group_send_allowed:
        fail("SCOPE_DENIED")
    return _scoped_permission(db, conversation, "read", "retain", *( ["send"] if outgoing else []))


def _connection(db, conversation, kind):
    connector = db.get(Connector, conversation.connector_id)
    if connector is None or connector.workspace_id != conversation.workspace_id:
        fail("SCOPE_DENIED")
    if connector.status != "connected" or connector.lease_expires_at is None or aware(connector.lease_expires_at) <= now():
        fail("CAPABILITY_UNAVAILABLE", 409)
    capability = connector.capabilities.get(CAPABILITIES[kind])
    if isinstance(capability, dict):
        capability = capability.get("status")
    if capability != "supported":
        fail("CAPABILITY_UNAVAILABLE", 409)
    # No current live adapter is connected to this generic ledger. The older
    # provider-specific text dispatcher retains its separately gated behavior.
    if connector.provider != "mock":
        fail("CAPABILITY_UNAVAILABLE", 409)
    return connector


def _quiet(grant):
    if grant.quiet_start == grant.quiet_end:
        return False
    clock = now().astimezone(ZoneInfo(grant.timezone)).strftime("%H:%M")
    if grant.quiet_start < grant.quiet_end:
        return grant.quiet_start <= clock < grant.quiet_end
    return clock >= grant.quiet_start or clock < grant.quiet_end


def _source(db, conversation, message_id):
    from .native import message_available
    message = db.get(Message, message_id) if message_id else None
    if (message is None or message.workspace_id != conversation.workspace_id
            or message.connector_id != conversation.connector_id or message.conversation_id != conversation.id
            or not message_available(db, message)):
        fail("SOURCE_MISSING", 409)
    for suppression in db.scalars(select(Suppression).where(Suppression.conversation_id == conversation.id)):
        if message.id in (suppression.source_message_ids or []):
            fail("SOURCE_MISSING", 409)
    return message


def _live_trigger(db, conversation, grant, message_id):
    from .native import message_context_for, reaction_target_handled
    trigger = _source(db, conversation, message_id)
    if trigger.origin != "live" or trigger.direction != "inbound" or trigger.author_kind != "contact_human":
        fail("HISTORY_ONLY", 409)
    current = now()
    age = (current - aware(trigger.provider_timestamp)).total_seconds()
    if age < -5 or age > grant.max_trigger_age_seconds or aware(trigger.received_at) < aware(grant.created_at):
        fail("EXPIRED", 409)
    if reaction_target_handled(db, trigger):
        fail("HUMAN_TAKEOVER", 409)
    if conversation.kind == "group":
        context = message_context_for(db, trigger)
        if context is None or not context.owner_addressed:
            fail("SCOPE_DENIED")
    latest = db.scalar(select(Message).where(Message.conversation_id == conversation.id,
                       Message.direction == "inbound", Message.origin == "live", Message.deleted.is_(False))
                       .order_by(Message.received_at.desc(), Message.id.desc()))
    if latest is None or latest.id != trigger.id:
        fail("CONTEXT_STALE", 409)
    return trigger


def _text_grounded(db, conversation, proposal, evidence, *, owner_job=False):
    if proposal.missing_facts or proposal.risk_flags:
        fail("MISSING_FACTS", 422)
    if proposal.kind not in {"SEND_TEXT", "QUOTE"}:
        if proposal.text:
            fail("INVALID_PROPOSAL", 422)
        return {}
    text = proposal.text.strip()
    if not text:
        fail("INVALID_PROPOSAL", 422)
    if owner_job:
        # Exact owner-authored job content is independently authorized; it is not
        # an automatic assertion invented by an AI planner.
        return {}
    if TEXT_COMMITMENT.search(text):
        fail("MISSING_FACTS", 422)
    if proposal.intent == "acknowledgement" and text in SAFE_ACK:
        return {}
    if proposal.intent == "allowed_clarification" and text in SAFE_CLARIFY:
        return {}
    if proposal.intent in {"verified_status", "factual_answer"} and evidence:
        human_ids = {source.id: source.revision for source in evidence
                     if source.author_kind == "human_owner" and source.direction == "outbound"}
        for memory in db.scalars(select(Memory).where(Memory.conversation_id == conversation.id,
                                                      Memory.workspace_id == conversation.workspace_id,
                                                      Memory.status == "confirmed")):
            if (memory.text.strip() == text and memory.visibility == "conversation" and memory.source_message_ids
                    and (memory.expires_at is None or aware(memory.expires_at) > now())
                    and all(key in human_ids and human_ids[key] == memory.source_revision.get(key)
                            for key in memory.source_message_ids)):
                return {memory.id: {"version": memory.version, "suppression_version": memory.suppression_version}}
    fail("MISSING_FACTS", 422)


def reaction_suitable(text: str, emoji: str) -> bool:
    # A positive keyword alone is insufficient when the target also has consent,
    # money, grief, negation or other ambiguous context. Broader language support
    # needs evaluated semantic models; unsupported targets abstain.
    if len(text) > 240 or "?" in text or REACTION_VETO.search(text) or not REACTION_POSITIVE.search(text):
        return False
    if emoji in {"😂", "👌"}:
        return False
    return emoji in PALETTE


def _route(db, conversation, route_id):
    route = db.get(ForwardRoute, route_id) if route_id else None
    if (route is None or route.workspace_id != conversation.workspace_id or route.connector_id != conversation.connector_id
            or route.source_conversation_id != conversation.id or not route.enabled or aware(route.expires_at) <= now()):
        fail("ROUTE_DENIED")
    destination = db.get(Conversation, route.destination_conversation_id)
    if (destination is None or destination.workspace_id != conversation.workspace_id
            or destination.connector_id != conversation.connector_id or destination.kind != route.audience
            or destination.id == conversation.id):
        fail("ROUTE_DENIED")
    _scoped_permission(db, conversation, "read", "retain", "share")
    _chat_state(db, destination)
    _scoped_permission(db, destination, "share")
    # Business customer information cannot be shared with another customer, even
    # when an owner grants a route. Mock is the only eligible adapter today.
    connector = db.get(Connector, conversation.connector_id)
    if connector.provider == "whatsapp_cloud":
        fail("ROUTE_DENIED")
    return route, destination


@router.put("/automation/grants")
@serialized_control
def put_grant(body: GrantInput, user=Depends(get_current_user), db=Depends(get_db)):
    conversation = conversation_for(db, user, body.conversation_id)
    if body.enabled:
        _scoped_permission(db, conversation, "read", "retain", "draft", "send")
    if body.expires_at <= now() or body.expires_at > now() + timedelta(days=30):
        fail("INVALID_EXPIRY", 422)
    connector = db.get(Connector, conversation.connector_id)
    if body.enabled:
        for kind in body.allowed_actions:
            _connection(db, conversation, kind)
        if conversation.control_state == "HUMAN_TAKEOVER":
            fail("HUMAN_TAKEOVER", 409)
        if "REACTION" in body.allowed_actions and not body.reaction_palette:
            fail("INVALID_PROPOSAL", 422)
        if "FORWARD" in body.allowed_actions and not body.forward_route_ids:
            fail("ROUTE_DENIED", 422)
        for route_id in body.forward_route_ids:
            _route(db, conversation, route_id)
    row = db.scalar(select(AutomationGrant).where(AutomationGrant.conversation_id == conversation.id))
    if row is not None:
        if body.expected_version != row.version:
            fail("CONTEXT_STALE", 409)
        row.version += 1
        invalidate_actions(db, conversation, "POLICY_CHANGED")
    else:
        if body.expected_version is not None:
            fail("CONTEXT_STALE", 409)
        row = AutomationGrant(workspace_id=conversation.workspace_id, conversation_id=conversation.id,
                              connector_id=connector.id, owner_id=user.id)
        db.add(row)
    for key, value in body.model_dump(exclude={"conversation_id", "expected_version"}).items():
        setattr(row, key, sorted(set(value)) if isinstance(value, list) else value)
    if conversation.control_state not in {"HUMAN_TAKEOVER", "RECONNECT_REVIEW", "PAUSED"}:
        conversation.control_state = "AUTO_ENABLED" if row.enabled else "DRAFT_MODE"
    if row.enabled:
        legacy = db.scalar(select(Automation).where(Automation.conversation_id == conversation.id))
        if legacy and legacy.enabled:
            from .messaging import invalidate_conversation
            legacy.enabled = False
            legacy.version += 1
            invalidate_conversation(db, conversation, "replaced_by_action_grant")
    db.flush()
    audit(db, conversation.workspace_id, user.id, "automation.grant_saved", row.id, version=row.version)
    db.commit()
    return grant_public(row)


@router.get("/automation/grants")
def list_grants(workspace_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    return [grant_public(row) for row in db.scalars(select(AutomationGrant).where(AutomationGrant.workspace_id == workspace_id))]


@router.delete("/automation/grants/{grant_id}")
def revoke_grant(grant_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.get(AutomationGrant, grant_id)
    if row is None:
        raise HTTPException(404, "Grant not found")
    workspace_for(db, user, row.workspace_id)
    with submit_guard(row.workspace_id):
        lock_workspace(db, row.workspace_id)
        db.expire_all()
        db.refresh(row)
        row.enabled = False
        row.version += 1
        conversation = db.get(Conversation, row.conversation_id)
        invalidate_actions(db, conversation, "POLICY_CHANGED")
        if conversation.control_state == "AUTO_ENABLED":
            conversation.control_state = "DRAFT_MODE"
        audit(db, row.workspace_id, user.id, "automation.grant_revoked", row.id, version=row.version)
        db.commit()
        return grant_public(row)


@router.post("/forward-routes", status_code=201)
def create_route(body: RouteInput, user=Depends(get_current_user), db=Depends(get_db)):
    source = conversation_for(db, user, body.source_conversation_id)
    destination = conversation_for(db, user, body.destination_conversation_id)
    with submit_guard(source.workspace_id):
        lock_workspace(db, source.workspace_id)
        db.expire_all()
        if (source.workspace_id != destination.workspace_id or source.connector_id != destination.connector_id
                or source.id == destination.id or destination.kind != body.audience):
            fail("ROUTE_DENIED")
        if body.expires_at <= now() or body.expires_at > now() + timedelta(days=30):
            fail("INVALID_EXPIRY", 422)
        _scoped_permission(db, source, "read", "retain", "share")
        _scoped_permission(db, destination, "read", "retain", "send", "share")
        _connection(db, source, "FORWARD")
        if destination.kind == "group" and not destination.group_send_allowed:
            fail("ROUTE_DENIED")
        row = ForwardRoute(workspace_id=source.workspace_id, connector_id=source.connector_id,
                           owner_id=user.id, **body.model_dump())
        db.add(row)
        db.flush()
        audit(db, source.workspace_id, user.id, "forward_route.granted", row.id)
        db.commit()
        return route_public(row)


@router.get("/forward-routes")
def list_routes(workspace_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    return [route_public(row) for row in db.scalars(select(ForwardRoute).where(ForwardRoute.workspace_id == workspace_id))]


@router.patch("/forward-routes/{route_id}")
def patch_route(route_id: str, body: RouteUpdate, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.get(ForwardRoute, route_id)
    if row is None:
        raise HTTPException(404, "Route not found")
    workspace_for(db, user, row.workspace_id)
    with submit_guard(row.workspace_id):
        lock_workspace(db, row.workspace_id)
        db.expire_all()
        db.refresh(row)
        if row.version != body.expected_version:
            fail("CONTEXT_STALE", 409)
        row.enabled = body.enabled
        row.version += 1
        for action in db.scalars(select(OutboundAction).where(OutboundAction.route_id == row.id,
                                                            OutboundAction.status.in_(PENDING))):
            action.status, action.reason_code = "canceled", "ROUTE_DENIED"
            _release_budget(db, action)
        audit(db, row.workspace_id, user.id, "forward_route.changed", row.id, version=row.version)
        db.commit()
        return route_public(row)


def prepare_action(db, user_id: str, conversation_id: str, proposal: ActionProposal, authorized_job=None):
    from .native import native_record_for, reaction_habit_for
    user = db.get(User, user_id)
    if user is None:
        fail("SCOPE_DENIED")
    conversation = conversation_for(db, user, conversation_id)
    lock_workspace(db, conversation.workspace_id)
    db.refresh(conversation)
    workspace = workspace_for(db, user, conversation.workspace_id)
    if workspace.paused:
        fail("GLOBAL_PAUSE", 409)
    permission = _chat_state(db, conversation, outgoing=proposal.kind != "FORWARD")
    _scoped_permission(db, conversation, "draft")
    connector = _connection(db, conversation, proposal.kind)
    if proposal.expected_context_revision is not None and proposal.expected_context_revision != conversation.revision:
        fail("CONTEXT_STALE", 409)
    if proposal.expected_control_epoch is not None and proposal.expected_control_epoch != conversation.control_epoch:
        fail("CONTEXT_STALE", 409)
    grant = None
    trigger = None
    if authorized_job is None:
        grant = db.scalar(select(AutomationGrant).where(AutomationGrant.conversation_id == conversation.id))
        if (grant is None or not grant.enabled or grant.mode != "AUTO" or grant.owner_id != user_id
                or aware(grant.expires_at) <= now() or proposal.kind not in grant.allowed_actions
                or proposal.intent not in grant.allowed_intents or conversation.control_state != "AUTO_ENABLED"):
            fail("SCOPE_DENIED")
        if proposal.expected_policy_version is not None and proposal.expected_policy_version != grant.version:
            fail("CONTEXT_STALE", 409)
        if _quiet(grant):
            fail("QUIET_HOURS", 429)
        trigger = _live_trigger(db, conversation, grant, proposal.trigger_message_id)
    elif proposal.trigger_message_id is not None:
        fail("INVALID_PROPOSAL", 422)
    sources = {message_id: _source(db, conversation, message_id) for message_id in proposal.evidence_message_ids}
    if trigger is not None:
        sources[trigger.id] = trigger
    native = None
    target = None
    if proposal.kind in {"QUOTE", "REACTION", "FORWARD"}:
        target = _source(db, conversation, proposal.target_message_id)
        native = native_record_for(db, target)
        if native is None:
            fail("SOURCE_MISSING", 409)
        sources[target.id] = target
    elif proposal.target_message_id or proposal.emoji or proposal.route_id:
        fail("INVALID_PROPOSAL", 422)
    if proposal.kind == "REACTION":
        if proposal.emoji not in PALETTE or (grant and proposal.emoji not in grant.reaction_palette):
            fail("SCOPE_DENIED")
        if trigger is not None and target.id != trigger.id:
            fail("INVALID_PROPOSAL", 422)
        habit = reaction_habit_for(db, target, proposal.emoji)
        if habit is None or not reaction_suitable(target.text, proposal.emoji):
            fail("REACTION_UNSUITABLE", 422)
    elif proposal.emoji:
        fail("INVALID_PROPOSAL", 422)
    if proposal.kind == "FORWARD":
        if proposal.intent != "forwarding" or (grant and proposal.route_id not in grant.forward_route_ids):
            fail("ROUTE_DENIED")
        route, destination = _route(db, conversation, proposal.route_id)
        destination_permission = _chat_state(db, destination)
    else:
        if proposal.route_id:
            fail("INVALID_PROPOSAL", 422)
        route, destination, destination_permission = None, conversation, permission
    memory_bindings = _text_grounded(db, conversation, proposal,
                         [sources[message_id] for message_id in proposal.evidence_message_ids], owner_job=authorized_job is not None)
    response_ref = f"job:{authorized_job.run_key}" if authorized_job is not None else f"live:{trigger.id}"
    # Action-kind changes do not create a second response. A separately authorized
    # forward is unique per exact destination, irrespective of route replacement.
    suffix = f"forward:{destination.id}" if proposal.kind == "FORWARD" else "response"
    logical_key = hashlib.sha256(f"{conversation.id}:{response_ref}:{suffix}".encode()).hexdigest()
    existing = db.scalar(select(OutboundAction).where(OutboundAction.workspace_id == conversation.workspace_id,
                                                     OutboundAction.logical_key == logical_key))
    if existing is not None:
        return existing
    if grant:
        count = len(db.scalars(select(OutboundAction.id).where(OutboundAction.grant_id == grant.id,
                         OutboundAction.created_at >= now() - timedelta(hours=1), OutboundAction.status.in_(COUNTED))).all())
        if count >= grant.max_outgoing_per_hour:
            fail("QUOTA_HELD", 429)
    payload = {"text": proposal.text.strip()} if proposal.kind in {"SEND_TEXT", "QUOTE"} else {}
    if proposal.kind == "REACTION":
        payload["emoji"] = proposal.emoji
    if target:
        payload.update(target_message_id=target.id, target_source_revision=target.revision,
                       native_record_ref=native.id)
    if route:
        payload.update(route_id=route.id, route_version=route.version, category="owner_selected_text")
    expiry = (min(aware(grant.expires_at), aware(trigger.provider_timestamp) + timedelta(seconds=grant.max_trigger_age_seconds))
              if grant else aware(authorized_job.expires_at))
    if native and native.expires_at:
        expiry = min(expiry, aware(native.expires_at))
    if route:
        expiry = min(expiry, aware(route.expires_at))
    source_snapshot = _snapshot(conversation, permission)
    if memory_bindings:
        source_snapshot["memory_bindings"] = memory_bindings
        for memory_id in memory_bindings:
            memory = db.get(Memory, memory_id)
            if memory.expires_at:
                expiry = min(expiry, aware(memory.expires_at))
    row = OutboundAction(id=uid(), workspace_id=conversation.workspace_id, connector_id=connector.id,
          conversation_id=conversation.id, destination_conversation_id=destination.id,
          recipient_id=destination.provider_chat_id, kind=proposal.kind, intent=proposal.intent,
          logical_key=logical_key, payload=payload, payload_hash=hash_payload(payload),
          trigger_message_id=trigger.id if trigger else None, target_message_id=target.id if target else None,
          native_record_id=native.id if native else None, evidence_message_ids=list(dict.fromkeys(proposal.evidence_message_ids)),
          source_revisions={key: message.revision for key, message in sources.items()},
          source_snapshot=source_snapshot, destination_snapshot=_snapshot(destination, destination_permission),
          connector_fence=connector.fence, pause_generation=workspace.pause_generation,
          grant_id=grant.id if grant else None, grant_version=grant.version if grant else None,
          route_id=route.id if route else None, route_version=route.version if route else None,
          authorized_job_id=authorized_job.id if authorized_job else None,
          authorized_job_version=authorized_job.version if authorized_job else None,
          authorized_job_run_key=authorized_job.run_key if authorized_job else None,
          expires_at=expiry, status="ready")
    if authorized_job is not None:
        from .jobs import authorize_job_action
        authorize_job_action(db, authorized_job, row)
    from .companion import reserve_action_budget
    reserve_action_budget(db, row.workspace_id, row.id, row.kind)
    db.add(row)
    db.flush()
    db.add(Outbox(workspace_id=row.workspace_id, kind="action.ready", aggregate_id=row.id,
                  payload={"action_id": row.id, "conversation_id": conversation.id, "kind": row.kind}))
    audit(db, row.workspace_id, user_id, "action.prepared", row.id, kind=row.kind, policy_version=row.grant_version)
    return row


@router.post("/actions", status_code=201)
@serialized_control
def create_action(body: ActionInput, user=Depends(get_current_user), db=Depends(get_db)):
    proposal = ActionProposal.model_validate(body.model_dump(exclude={"conversation_id"}))
    try:
        row = prepare_action(db, user.id, body.conversation_id, proposal)
        db.commit()
    except IntegrityError:
        db.rollback()
        fail("CONTEXT_STALE", 409)
    return action_public(row)


@router.post("/internal/actions/prepare", dependencies=[Depends(require_internal)], status_code=201)
def internal_prepare_action(body: ActionInput, db=Depends(get_db)):
    conversation = db.get(Conversation, body.conversation_id)
    if conversation is None:
        fail("SCOPE_DENIED")
    workspace = db.get(Workspace, conversation.workspace_id)
    proposal = ActionProposal.model_validate(body.model_dump(exclude={"conversation_id"}))
    with submit_guard(workspace.id):
        db.expire_all()
        row = prepare_action(db, workspace.owner_id, body.conversation_id, proposal)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            fail("CONTEXT_STALE", 409)
        return action_public(row)


def authorize_action(db, action):
    """Current exact authority; called again at the final transport boundary."""
    from .native import native_record_for, reaction_habit_for, reaction_target_handled
    if action.status not in {"ready", "submitting"}:
        fail("DELIVERY_UNCERTAIN" if action.status == "uncertain" else "ACTION_NOT_READY", 409)
    if aware(action.expires_at) <= now():
        fail("EXPIRED", 409)
    if hash_payload(action.payload) != action.payload_hash:
        fail("PAYLOAD_CHANGED", 409)
    workspace = db.get(Workspace, action.workspace_id)
    conversation = db.get(Conversation, action.conversation_id)
    destination = db.get(Conversation, action.destination_conversation_id)
    if (workspace is None or conversation is None or destination is None
            or conversation.workspace_id != action.workspace_id or destination.workspace_id != action.workspace_id
            or destination.connector_id != action.connector_id or conversation.connector_id != action.connector_id):
        fail("SCOPE_DENIED")
    if workspace.paused:
        fail("GLOBAL_PAUSE", 409)
    if workspace.pause_generation != action.pause_generation:
        fail("CONTEXT_STALE", 409)
    permission = _chat_state(db, conversation, outgoing=action.kind != "FORWARD")
    destination_permission = _chat_state(db, destination)
    source_snapshot = {key: value for key, value in action.source_snapshot.items() if key != "memory_bindings"}
    if (_snapshot(conversation, permission) != source_snapshot
            or _snapshot(destination, destination_permission) != action.destination_snapshot
            or destination.provider_chat_id != action.recipient_id):
        fail("CONTEXT_STALE", 409)
    _scoped_permission(db, conversation, "draft")
    connector = _connection(db, conversation, action.kind)
    if connector.fence != action.connector_fence:
        fail("CONTEXT_STALE", 409)
    sources = {message_id: _source(db, conversation, message_id) for message_id in action.source_revisions}
    if any(sources[key].revision != revision for key, revision in action.source_revisions.items()):
        fail("CONTEXT_STALE", 409)
    for memory_id, snapshot in action.source_snapshot.get("memory_bindings", {}).items():
        memory = db.get(Memory, memory_id)
        if (memory is None or memory.workspace_id != action.workspace_id or memory.conversation_id != conversation.id
                or memory.status != "confirmed" or memory.visibility != "conversation"
                or memory.version != snapshot["version"] or memory.suppression_version != snapshot["suppression_version"]
                or memory.text.strip() != action.text or (memory.expires_at and aware(memory.expires_at) <= now())
                or any(key not in sources or sources[key].revision != revision
                       for key, revision in memory.source_revision.items())):
            fail("CONTEXT_STALE", 409)
    if action.grant_id:
        grant = db.get(AutomationGrant, action.grant_id)
        if (grant is None or grant.workspace_id != action.workspace_id or grant.conversation_id != conversation.id
                or grant.connector_id != action.connector_id or grant.version != action.grant_version
                or not grant.enabled or aware(grant.expires_at) <= now() or action.kind not in grant.allowed_actions
                or action.intent not in grant.allowed_intents or conversation.control_state != "AUTO_ENABLED"):
            fail("SCOPE_DENIED")
        if _quiet(grant):
            fail("QUIET_HOURS", 429)
        _live_trigger(db, conversation, grant, action.trigger_message_id)
        count = len(db.scalars(select(OutboundAction.id).where(OutboundAction.grant_id == grant.id,
                         OutboundAction.id != action.id, OutboundAction.created_at >= now() - timedelta(hours=1),
                         OutboundAction.status.in_(COUNTED))).all())
        if count >= grant.max_outgoing_per_hour:
            fail("QUOTA_HELD", 429)
    elif action.authorized_job_id:
        from .jobs import authorize_job_action
        from .jobs_models import AuthorizedJob
        job = db.get(AuthorizedJob, action.authorized_job_id)
        if job is None:
            fail("SCOPE_DENIED")
        authorize_job_action(db, job, action)
    else:
        fail("SCOPE_DENIED")
    native = None
    if action.kind in {"QUOTE", "REACTION", "FORWARD"}:
        target = sources.get(action.target_message_id)
        native = native_record_for(db, target) if target else None
        if (native is None or native.id != action.native_record_id
                or native.id != action.payload.get("native_record_ref")):
            fail("SOURCE_MISSING", 409)
        if reaction_target_handled(db, target):
            fail("HUMAN_TAKEOVER", 409)
        if action.kind == "REACTION":
            emoji = action.payload.get("emoji")
            if (action.grant_id and emoji not in grant.reaction_palette) or not reaction_suitable(target.text, emoji):
                fail("REACTION_UNSUITABLE", 422)
            if reaction_habit_for(db, target, emoji) is None:
                fail("REACTION_UNSUITABLE", 422)
    if action.kind == "FORWARD":
        route, checked_destination = _route(db, conversation, action.route_id)
        if (route.version != action.route_version or checked_destination.id != destination.id
                or (action.grant_id and route.id not in grant.forward_route_ids)):
            fail("ROUTE_DENIED")
    from .companion import check_action_budget
    check_action_budget(db, action.workspace_id, action.id)
    return connector, native


def invalidate_actions(db, conversation, reason):
    rows = db.scalars(select(OutboundAction).where(OutboundAction.workspace_id == conversation.workspace_id,
          or_(OutboundAction.conversation_id == conversation.id,
              OutboundAction.destination_conversation_id == conversation.id), OutboundAction.status.in_(PENDING))).all()
    for row in rows:
        row.status, row.reason_code = "canceled", reason
        _release_budget(db, row)
    return len(rows)


def hold_workspace_actions(db, workspace):
    """Acknowledge pause with canceled live work and durable held job actions."""
    from .people_models import UsageLedger
    live = (OutboundAction.workspace_id == workspace.id, OutboundAction.status.in_(PENDING),
            OutboundAction.authorized_job_id.is_(None))
    keys = select("action:" + OutboundAction.id).where(*live)
    db.execute(update(UsageLedger).where(UsageLedger.workspace_id == workspace.id,
               UsageLedger.operation_key.in_(keys), UsageLedger.status == "reserved").values(status="released")
               .execution_options(synchronize_session=False))
    db.execute(update(OutboundAction).where(*live).values(status="canceled", reason_code="GLOBAL_PAUSE")
               .execution_options(synchronize_session=False))
    db.execute(update(OutboundAction).where(OutboundAction.workspace_id == workspace.id,
               OutboundAction.authorized_job_id.is_not(None), OutboundAction.status.in_(PENDING),
               or_(OutboundAction.status == "ready", OutboundAction.reason_code == "GLOBAL_PAUSE"))
               .values(status="held", reason_code="GLOBAL_PAUSE").execution_options(synchronize_session=False))


def resume_workspace_actions(db, workspace, settings=None):
    """Explicit owner resume refreshes only its pause fence, then checks every gate."""
    for action in db.scalars(select(OutboundAction).where(OutboundAction.workspace_id == workspace.id,
                      OutboundAction.authorized_job_id.is_not(None), OutboundAction.status == "held",
                      OutboundAction.reason_code == "GLOBAL_PAUSE")):
        action.status, action.reason_code = "ready", None
        action.pause_generation = workspace.pause_generation
        try:
            if settings is not None and settings.environment == "production":
                fail("CAPABILITY_UNAVAILABLE", 409)
            authorize_action(db, action)
        except HTTPException as exc:
            reason = _reason(exc)
            if reason in {"QUIET_HOURS", "QUOTA_HELD", "JOB_NOT_ACTIVE"}:
                action.status, action.reason_code = "held", reason
            else:
                action.status, action.reason_code = "expired" if reason == "EXPIRED" else "canceled", reason
                _release_budget(db, action)


def invalidate_target(db, message_id, reason="HUMAN_REACTION"):
    source = db.get(Message, message_id)
    if source is None:
        return
    for row in db.scalars(select(OutboundAction).where(OutboundAction.workspace_id == source.workspace_id,
                          OutboundAction.conversation_id == source.conversation_id, OutboundAction.status.in_(PENDING))):
        if message_id in row.source_revisions or message_id in {row.target_message_id, row.trigger_message_id}:
            row.status, row.reason_code = "canceled", reason
            _release_budget(db, row)


def forget_action_sources(db, conversation_id, source_ids):
    source_set = set(source_ids)
    for action in db.scalars(select(OutboundAction).where(OutboundAction.conversation_id == conversation_id)):
        if source_set.intersection(action.source_revisions):
            if action.status in PENDING:
                action.status, action.reason_code = "canceled", "SOURCE_MISSING"
                _release_budget(db, action)
            action.payload = {}


def purge_action_data(db, conversation_id):
    for action in db.scalars(select(OutboundAction).where(or_(OutboundAction.conversation_id == conversation_id,
                                                             OutboundAction.destination_conversation_id == conversation_id))):
        if action.status in PENDING:
            action.status, action.reason_code = "canceled", "SCOPE_DENIED"
            _release_budget(db, action)
        action.payload = {}
        action.evidence_message_ids = []
        action.source_revisions = {}
    for grant in db.scalars(select(AutomationGrant).where(AutomationGrant.conversation_id == conversation_id)):
        grant.enabled = False
        grant.version += 1
    for route in db.scalars(select(ForwardRoute).where(or_(ForwardRoute.source_conversation_id == conversation_id,
                                                          ForwardRoute.destination_conversation_id == conversation_id))):
        route.enabled = False
        route.version += 1


def _read_action(db, user, action_id):
    row = db.get(OutboundAction, action_id)
    if row is None:
        raise HTTPException(404, "Action not found")
    workspace_for(db, user, row.workspace_id)
    source = conversation_for(db, user, row.conversation_id)
    destination = conversation_for(db, user, row.destination_conversation_id)
    permission_for(db, source, "read")
    permission_for(db, destination, "read")
    return row


def export_action_data(db, conversation_id):
    result = []
    for row in db.scalars(select(OutboundAction).where(OutboundAction.conversation_id == conversation_id)):
        source = db.get(Conversation, row.conversation_id)
        destination = db.get(Conversation, row.destination_conversation_id)
        try:
            _scoped_permission(db, source, "read")
            _scoped_permission(db, destination, "read")
        except HTTPException:
            continue
        result.append(action_public(row))
    return result


@router.get("/actions")
def list_actions(workspace_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    result = []
    for row in db.scalars(select(OutboundAction).where(OutboundAction.workspace_id == workspace_id)
                          .order_by(OutboundAction.created_at.desc()).limit(200)):
        try:
            _read_action(db, user, row.id)
        except HTTPException:
            continue
        result.append(action_public(row))
    return result


@router.get("/actions/{action_id}")
def get_action(action_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    return action_public(_read_action(db, user, action_id))


@router.get("/actions/{action_id}/evidence")
def action_evidence(action_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    action = _read_action(db, user, action_id)
    conversation = db.get(Conversation, action.conversation_id)
    sources = []
    for message_id, revision in action.source_revisions.items():
        try:
            source = _source(db, conversation, message_id)
        except HTTPException:
            continue
        if source.revision == revision:
            sources.append({"message_id": source.id, "revision": source.revision, "text": source.text})
    return {"action_id": action.id, "sources": sources}


@router.post("/actions/{action_id}/cancel")
def cancel_action(action_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = _read_action(db, user, action_id)
    with submit_guard(row.workspace_id):
        lock_workspace(db, row.workspace_id)
        db.expire_all()
        db.refresh(row)
        if row.status not in PENDING:
            fail("ACTION_NOT_CANCELABLE", 409)
        row.status, row.reason_code = "canceled", "OWNER_CANCELED"
        _release_budget(db, row)
        audit(db, row.workspace_id, user.id, "action.canceled", row.id)
        db.commit()
        return action_public(row)


def envelope_for(db, action):
    connector = db.get(Connector, action.connector_id)
    return {"schema_version": 1, "action_id": action.id, "workspace_id": action.workspace_id,
            "connector_id": action.connector_id, "account_id": connector.account_id,
            "conversation_id": action.destination_conversation_id, "recipient_id": action.recipient_id,
            "kind": action.kind, "payload": action.payload, "payload_hash": action.payload_hash,
            "connector_fence": action.connector_fence}


class DispatchEnvelope(Payload):
    schema_version: Literal[1] = 1
    action_id: str
    workspace_id: str
    connector_id: str
    account_id: str
    conversation_id: str
    recipient_id: str
    kind: Literal["SEND_TEXT", "QUOTE", "REACTION", "FORWARD"]
    payload: dict = Field(max_length=12)
    payload_hash: str = Field(min_length=64, max_length=64)
    connector_fence: int = Field(ge=1)


def _source_record(native, action):
    from .native import native_authority_payload
    return native_authority_payload(native)


@router.post("/internal/dispatch-authority", dependencies=[Depends(require_internal)])
def dispatch_authority(body: DispatchEnvelope, request: Request, db=Depends(get_db)):
    if request.app.state.settings.environment == "production":
        fail("CAPABILITY_UNAVAILABLE", 409)
    row = db.get(OutboundAction, body.action_id)
    if row is None:
        fail("SCOPE_DENIED")
    with submit_guard(row.workspace_id):
        lock_workspace(db, row.workspace_id)
        db.expire_all()
        row = db.get(OutboundAction, body.action_id)
        if body.model_dump() != envelope_for(db, row):
            fail("PAYLOAD_CHANGED", 409)
        attempt = db.scalar(select(SubmissionAttempt).where(SubmissionAttempt.action_id == row.id))
        if (attempt is None or attempt.status != "submitting" or row.status != "submitting"
                or attempt.payload_hash != row.payload_hash or attempt.connector_fence != row.connector_fence):
            fail("ACTION_NOT_READY", 409)
        connector, native = authorize_action(db, row)
        deadline = min(now() + timedelta(seconds=5), aware(row.expires_at), aware(connector.lease_expires_at))
        result = {"schema_version": 1, "allowed": True, "reason_code": "OK", "action_id": row.id,
                  "payload_hash": row.payload_hash, "connector_fence": row.connector_fence,
                  "authority_expires_at": deadline.isoformat()}
        for key in ("workspace_id", "connector_id", "account_id", "conversation_id", "recipient_id", "kind"):
            result[key] = getattr(body, key)
        if native is not None:
            result["source_record"] = _source_record(native, row)
        from .companion import reserve_action_budget
        reserve_action_budget(db, row.workspace_id, row.id, row.kind)
        db.commit()
        return result


async def submit_simulated(envelope, authority):
    """No external side effect; boundary callback still validates current state."""
    await asyncio.sleep(0)
    authority()
    return {"status": "accepted", "provider_message_id": f"mock:action:{envelope['action_id']}"}


async def dispatch_action(factory, settings, action_id):
    with factory() as db:
        action = db.get(OutboundAction, action_id)
        if action is None:
            raise HTTPException(404, "Action not found")
        workspace_id = action.workspace_id
    with submit_guard(workspace_id), factory() as db:
        lock_workspace(db, workspace_id)
        action = db.get(OutboundAction, action_id)
        if action is None:
            raise HTTPException(404, "Action not found")
        attempt = db.scalar(select(SubmissionAttempt).where(SubmissionAttempt.action_id == action.id))
        if attempt is not None:
            # An active duplicate observes the original claim. After a bounded
            # recovery window a crashed claim becomes uncertain, never retryable.
            if (attempt.status == "submitting" and
                    aware(attempt.created_at) <= now() - timedelta(seconds=SUBMISSION_RECOVERY_SECONDS)):
                attempt.status, attempt.error_code = "uncertain", "DELIVERY_UNCERTAIN"
                action.status, action.reason_code = "uncertain", "DELIVERY_UNCERTAIN"
                from .companion import settle_action_budget
                settle_action_budget(db, workspace_id, action.id, "uncertain")
                db.commit()
            return action_public(action)
        if (action.status == "held" and action.authorized_job_id
                and action.reason_code in {"QUIET_HOURS", "QUOTA_HELD"}):
            # The exact durable payload remains bound. These temporary holds can
            # be reconsidered; pause holds require explicit owner Resume above.
            action.status = "ready"
        if action.status != "ready":
            return action_public(action)
        if settings.environment == "production":
            action.status, action.reason_code = "blocked", "CAPABILITY_UNAVAILABLE"
            _release_budget(db, action)
            db.commit()
            return action_public(action)
        try:
            authorize_action(db, action)
        except HTTPException as exc:
            reason = _reason(exc)
            temporary = bool(action.authorized_job_id and reason in {"GLOBAL_PAUSE", "QUIET_HOURS", "QUOTA_HELD"})
            action.status = "held" if temporary else "expired" if reason == "EXPIRED" else "blocked"
            action.reason_code = reason
            from .companion import settle_action_budget
            if not temporary:
                settle_action_budget(db, workspace_id, action.id, "released")
            db.commit()
            return action_public(action)
        attempt = SubmissionAttempt(workspace_id=workspace_id, action_id=action.id,
             connector_id=action.connector_id, destination_conversation_id=action.destination_conversation_id,
             payload_hash=action.payload_hash, connector_fence=action.connector_fence, status="submitting")
        action.status = "submitting"
        db.add(attempt)
        from .companion import reserve_action_budget
        reserve_action_budget(db, workspace_id, action.id, action.kind)
        envelope = envelope_for(db, action)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return action_public(db.get(OutboundAction, action_id))

    def current_authority():
        with submit_guard(workspace_id), factory() as current_db:
            lock_workspace(current_db, workspace_id)
            current = current_db.get(OutboundAction, action_id)
            if current is None or current.status != "submitting":
                fail("DELIVERY_UNCERTAIN", 409)
            if envelope_for(current_db, current) != envelope:
                fail("PAYLOAD_CHANGED", 409)
            authorize_action(current_db, current)
            from .companion import reserve_action_budget
            reserve_action_budget(current_db, workspace_id, action_id, current.kind)
            current_db.commit()

    try:
        gateway_url = getattr(settings, "connector_gateway_url", "")
        if gateway_url:
            from .bridge import dispatch_bridge
            result = await dispatch_bridge(envelope, gateway_url=gateway_url,
                       token=settings.connector_gateway_token, timeout_seconds=settings.connector_gateway_timeout_seconds)
            transport = {"status": result.status, "provider_message_id": result.provider_message_id,
                         "error_code": result.error_code}
        else:
            transport = await submit_simulated(envelope, current_authority)
    except HTTPException as exc:
        transport = {"status": "failed", "provider_message_id": None,
                     "error_code": _reason(exc)}
    except Exception:
        transport = {"status": "uncertain", "provider_message_id": None, "error_code": "DELIVERY_UNCERTAIN"}
    with submit_guard(workspace_id), factory() as db:
        lock_workspace(db, workspace_id)
        action = db.get(OutboundAction, action_id)
        attempt = db.scalar(select(SubmissionAttempt).where(SubmissionAttempt.action_id == action_id))
        if action is None or attempt is None:
            return {"id": action_id, "status": "uncertain", "reason_code": "LEDGER_REMOVED_AFTER_SUBMIT"}
        # Confirmed receipts/echoes can win a race against the transport result.
        if attempt.status not in {"accepted", "delivered", "read"}:
            status = transport["status"]
            status = status if status in {"accepted", "uncertain", "failed"} else "uncertain"
            attempt.status = status
            attempt.provider_message_id = transport.get("provider_message_id")
            attempt.error_code = transport.get("error_code")
            action.status = "blocked" if status == "failed" else status
            action.reason_code = attempt.error_code
        from .companion import settle_action_budget
        settlement = "uncertain" if action.status == "uncertain" else "consumed" if action.status in {"accepted", "delivered", "read"} else "released"
        settle_action_budget(db, workspace_id, action_id, settlement)
        audit(db, workspace_id, "dispatcher", "action.submission_recorded", action.id,
              status=action.status, reason_code=action.reason_code)
        db.commit()
        return action_public(action)


def _reason(exc):
    if isinstance(exc.detail, dict):
        return exc.detail.get("reason_code", exc.detail.get("code", "SCOPE_DENIED"))
    return exc.detail if isinstance(exc.detail, str) else "SCOPE_DENIED"


def _release_budget(db, action):
    from .companion import settle_action_budget
    settle_action_budget(db, action.workspace_id, action.id, "released")


def _automatic_proposal(db, conversation, grant, message):
    """Small evaluated-safe foundation planner; broader AI proposals abstain.

    Six operation contracts are available to structured planners. This default
    admission lane handles positive acknowledgements and a bounded clarification,
    rather than interpreting arbitrary inbound text as executable commands.
    """
    from .native import native_record_for, reaction_habit_for
    if "acknowledgement" in grant.allowed_intents and reaction_suitable(message.text, "👍"):
        if "REACTION" in grant.allowed_actions and native_record_for(db, message):
            for emoji in grant.reaction_palette:
                if reaction_suitable(message.text, emoji) and reaction_habit_for(db, message, emoji):
                    return ActionProposal(kind="REACTION", intent="acknowledgement", trigger_message_id=message.id,
                                          target_message_id=message.id, emoji=emoji)
        if "SEND_TEXT" in grant.allowed_actions:
            return ActionProposal(kind="SEND_TEXT", intent="acknowledgement", trigger_message_id=message.id,
                                  text="Thanks for the update.")
    if ("allowed_clarification" in grant.allowed_intents and "SEND_TEXT" in grant.allowed_actions
            and re.fullmatch(r"(?:what time|when)(?: is| for)? (?:the |our )?meeting\??", message.text.strip(), re.I)):
        return ActionProposal(kind="SEND_TEXT", intent="allowed_clarification", trigger_message_id=message.id,
                              text="What time would work for you?")
    return None


async def process_auto_actions(factory, settings, batch_size=50):
    """Recover ready work and consume SQL event references idempotently."""
    batch_size = min(max(batch_size, 1), 100)
    with factory() as db:
        prepared = list(db.scalars(select(OutboundAction.id).where(OutboundAction.grant_id.is_not(None),
                             OutboundAction.status == "ready").order_by(OutboundAction.created_at).limit(batch_size)))
    results = [await dispatch_action(factory, settings, action_id) for action_id in prepared]
    remaining = batch_size - len(prepared)
    if remaining <= 0:
        return results
    with factory() as db:
        handled = select(Outbox.aggregate_id).where(Outbox.kind == "action.handled")
        refs = db.execute(select(Outbox.id, Outbox.workspace_id).where(
                    Outbox.kind == "message.accepted", ~Outbox.id.in_(handled),
                    Outbox.payload["live_eligible"].as_boolean().is_(True),
                    Outbox.created_at >= now() - timedelta(seconds=600))
                    .order_by(Outbox.created_at, Outbox.id).limit(remaining)).all()
    for outbox_id, workspace_id in refs:
        action_id = None
        with submit_guard(workspace_id), factory() as db:
            lock_workspace(db, workspace_id)
            if db.scalar(select(Outbox).where(Outbox.kind == "action.handled", Outbox.aggregate_id == outbox_id)):
                continue
            source_ref = db.get(Outbox, outbox_id)
            message = db.get(Message, source_ref.aggregate_id) if source_ref else None
            conversation = db.get(Conversation, message.conversation_id) if message else None
            grant = db.scalar(select(AutomationGrant).where(AutomationGrant.conversation_id == conversation.id)) if conversation else None
            reason = "SCOPE_DENIED"
            if (source_ref and message and conversation and grant and source_ref.workspace_id == message.workspace_id
                    and source_ref.payload.get("message_id") == message.id
                    and source_ref.payload.get("conversation_id") == conversation.id
                    and source_ref.payload.get("revision") == conversation.revision):
                try:
                    proposal = _automatic_proposal(db, conversation, grant, message)
                    if proposal:
                        with db.begin_nested():
                            action = prepare_action(db, grant.owner_id, conversation.id, proposal)
                        action_id, reason = action.id, "READY"
                    else:
                        reason = "NO_SAFE_ACTION"
                except HTTPException as exc:
                    reason = _reason(exc)
            db.add(Outbox(id=str(uuid5(NAMESPACE_URL, f"assistant:action-handled:{outbox_id}")),
                          workspace_id=workspace_id, kind="action.handled", aggregate_id=outbox_id,
                          payload={"message_id": message.id if message else None, "reason_code": reason}, status="recorded"))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
        if action_id:
            results.append(await dispatch_action(factory, settings, action_id))
        else:
            results.append({"status": "skipped", "reason_code": reason})
    return results


@router.post("/internal/actions/run-once", dependencies=[Depends(require_internal)])
async def action_worker_once(request: Request):
    return {"results": await process_auto_actions(request.app.state.session_factory, request.app.state.settings)}


def main():
    import argparse
    from .config import Settings
    from .db import make_database
    parser = argparse.ArgumentParser(description="Bounded action admission worker")
    parser.add_argument("--once", action="store_true")
    options = parser.parse_args()
    settings = Settings().prepare()
    engine, factory = make_database(settings)

    async def worker():
        while True:
            await process_auto_actions(factory, settings)
            if options.once:
                return
            await asyncio.sleep(0.5)
    try:
        asyncio.run(worker())
    finally:
        engine.dispose()


@router.post("/actions/{action_id}/dispatch")
async def dispatch_endpoint(action_id: str, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    _read_action(db, user, action_id)
    db.rollback()
    return await dispatch_action(request.app.state.session_factory, request.app.state.settings, action_id)


class ActionReceipt(Payload):
    connector_id: str
    action_id: str
    provider_message_id: str = Field(min_length=1, max_length=180)
    status: Literal["accepted", "delivered", "read"]


@router.post("/internal/action-receipts", dependencies=[Depends(require_internal)])
def action_receipt(body: ActionReceipt, db=Depends(get_db)):
    action = db.get(OutboundAction, body.action_id)
    if action is None:
        fail("SCOPE_DENIED")
    lock_workspace(db, action.workspace_id)
    db.refresh(action)
    attempt = db.scalar(select(SubmissionAttempt).where(SubmissionAttempt.action_id == body.action_id))
    if action is None or attempt is None or action.connector_id != body.connector_id:
        fail("SCOPE_DENIED")
    if attempt.provider_message_id is None or attempt.provider_message_id != body.provider_message_id:
        fail("SOURCE_MISSING", 409)
    ranks = {"submitting": 0, "uncertain": 0, "accepted": 1, "delivered": 2, "read": 3}
    if ranks.get(attempt.status, -1) < ranks[body.status]:
        action.status = attempt.status = body.status
        action.reason_code = attempt.error_code = None
        from .companion import settle_action_budget
        settle_action_budget(db, action.workspace_id, action.id, "consumed")
    db.commit()
    return action_public(action)


def assistant_reaction_echo(db, event):
    provider_id = getattr(event, "provider_message_id", None)
    conversation_id = getattr(event, "conversation_id", None)
    attempt = db.scalar(select(SubmissionAttempt).where(SubmissionAttempt.provider_message_id == provider_id,
                                                         SubmissionAttempt.destination_conversation_id == conversation_id))
    if attempt is None or event.direction != "outbound" or event.reaction is None:
        return False
    action = db.get(OutboundAction, attempt.action_id)
    connector = db.get(Connector, event.connector_id)
    target = db.get(Message, action.target_message_id) if action and action.target_message_id else None
    if not (action and action.kind == "REACTION" and action.connector_id == event.connector_id
            and connector and event.sender_id == connector.owner_sender_id and target
            and event.reaction.target_provider_message_id == target.provider_message_id
            and event.reaction.emoji == action.emoji):
        return False
    if attempt.status in {"submitting", "uncertain"}:
        action.status = attempt.status = "accepted"
        action.reason_code = attempt.error_code = None
        from .companion import settle_action_budget
        settle_action_budget(db, action.workspace_id, action.id, "consumed")
    return True


def assistant_message_echo(db, event):
    if event.direction != "outbound":
        return False
    connector = db.get(Connector, event.connector_id)
    if connector is None or event.sender_id != connector.owner_sender_id:
        return False
    attempt = db.scalar(select(SubmissionAttempt).where(
        SubmissionAttempt.provider_message_id == event.provider_message_id,
        SubmissionAttempt.connector_id == event.connector_id,
        SubmissionAttempt.destination_conversation_id == event.conversation_id))
    action = db.get(OutboundAction, attempt.action_id) if attempt else None
    conversation = db.get(Conversation, event.conversation_id)
    if not (action and action.kind in {"SEND_TEXT", "QUOTE", "FORWARD"} and conversation
            and action.workspace_id == connector.workspace_id == conversation.workspace_id
            and action.recipient_id == conversation.provider_chat_id
            and action.connector_id == connector.id == conversation.connector_id):
        return False
    if attempt.status in {"submitting", "uncertain"}:
        action.status = attempt.status = "accepted"
        action.reason_code = attempt.error_code = None
        from .companion import settle_action_budget
        settle_action_budget(db, action.workspace_id, action.id, "consumed")
    return True


if __name__ == "__main__":
    main()
