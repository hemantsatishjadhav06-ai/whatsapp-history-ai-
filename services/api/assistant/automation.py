"""Explicitly granted, verified business-hours replies; other intents stay manual.

The SQL message outbox supplies references. A deterministic handled marker records
each decision without stealing the Kafka relay's publication status. This worker
shares the single gateway's policy boundary and never calls a language model.
"""

import argparse
import asyncio
import logging
import re
from datetime import datetime, timedelta
from string import Formatter
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .access import audit, conversation_for, permission_for
from .auth import get_current_user
from .core import serialized_control
from .db import aware, get_db, now
from .intelligence import source_messages
from .messaging import (
    _validate_current, content_hash, dispatch_draft, invalidate_conversation, require_internal, submit_guard,
)
from .models import (
    AuditEvent, Automation, Connector, Conversation, Draft, Memory, Message, Outbox, SendAttempt, StyleProfile,
    Workspace,
)
from .storage_authority import lock_workspace

router = APIRouter(tags=["automation"])
logger = logging.getLogger(__name__)
QUESTION = re.compile(
    r"(?:what are (?:your|the) (?:business|opening|office) hours|"
    r"(?:business|opening|office) hours|when are you open)[?.!]?", re.IGNORECASE,
)
DAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Weekdays|Weekends|Every day)"
CLOCK = r"(?:[01]?[0-9]|2[0-3]):[0-5][0-9]"
CLAUSE = rf"{DAY}(?:\s*[-–]\s*{DAY})?\s+(?:{CLOCK}\s*[-–]\s*{CLOCK}|closed)"
FACT = re.compile(
    rf"(?:(?:Business|Opening|Office) hours:\s*)?{CLAUSE}(?:;\s*{CLAUSE})*"
    r"(?:\s+(?:IST|UTC|GMT))?\.?", re.IGNORECASE,
)


class AutomationGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    allowed_intents: list[Literal["business_hours"]] = Field(default_factory=lambda: ["business_hours"],
                                                            min_length=1, max_length=1)
    memory_id: str | None = None
    reply_template: str = Field(default="{fact}", min_length=6, max_length=200)
    max_replies_per_hour: int = Field(default=3, ge=1, le=10)
    expires_at: datetime
    quiet_start: str = Field(default="21:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    quiet_end: str = Field(default="09:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")

    @field_validator("reply_template")
    @classmethod
    def safe_template(cls, value):
        try:
            fields = [(field, spec, conversion) for _, field, spec, conversion in Formatter().parse(value)
                      if field is not None]
        except ValueError as error:
            raise ValueError("Template must contain exactly one {fact}") from error
        if fields != [("fact", "", None)] or value.count("{fact}") != 1:
            raise ValueError("Only one literal {fact} placeholder is supported")
        if "{" in value.replace("{fact}", "") or "}" in value.replace("{fact}", ""):
            raise ValueError("Other braces/placeholders are unavailable")
        if any(ord(character) < 32 for character in value) or re.search(r"https?://|@", value, re.I):
            raise ValueError("Template must be short static text without links or dynamic fields")
        if value not in {
            "{fact}", "Thanks for asking. {fact}", "Our business hours: {fact}",
            "Our opening hours: {fact}", "{fact} Thank you.", "Thanks for asking. {fact} Thank you.",
        }:
            raise ValueError("Use {fact} with an optional supported hours label or short greeting")
        return value


    @field_validator("expires_at")
    @classmethod
    def explicit_expiry(cls, value):
        if value.tzinfo is None:
            raise ValueError("Expiry requires an explicit timezone")
        return aware(value)


def _fact(db, conversation, memory_id):
    memory = db.get(Memory, memory_id) if memory_id else None
    if (memory is None or memory.workspace_id != conversation.workspace_id
            or memory.conversation_id != conversation.id or memory.visibility != "conversation"
            or memory.status != "confirmed" or (memory.expires_at and aware(memory.expires_at) <= now())):
        raise HTTPException(409, "A confirmed current fact from this conversation is required")
    sources = source_messages(db, conversation, memory.source_message_ids)
    if {source.id: source.revision for source in sources} != memory.source_revision:
        raise HTTPException(409, "Confirmed fact evidence changed")
    if not FACT.fullmatch(memory.text.strip()):
        raise HTTPException(422, "Fact must be an explicit hours schedule, e.g. Monday-Friday 09:00-17:00 IST")
    return memory, sources


def _quiet(rule, workspace):
    local = now().astimezone(ZoneInfo(workspace.timezone)).strftime("%H:%M")
    if rule.quiet_start == rule.quiet_end:
        return True
    if rule.quiet_start < rule.quiet_end:
        return rule.quiet_start <= local < rule.quiet_end
    return local >= rule.quiet_start or local < rule.quiet_end


def validate_automated_draft(db, draft, conversation, workspace):
    rule = db.get(Automation, draft.automation_id)
    if (rule is None or rule.workspace_id != workspace.id or rule.conversation_id != conversation.id
            or not rule.enabled or rule.version != draft.automation_version
            or aware(rule.expires_at) <= now() or rule.allowed_intents != ["business_hours"]
            or conversation.control_state != "AUTO_ENABLED" or conversation.kind != "contact"):
        raise HTTPException(409, "Automation grant expired, changed, or unavailable")
    if _quiet(rule, workspace):
        raise HTTPException(409, "Automation is within quiet hours")
    trigger = db.get(Message, draft.evidence_message_ids[0]) if draft.evidence_message_ids else None
    if (draft.model_version != "verified-fact-template-v1" or trigger is None or trigger.deleted
            or trigger.conversation_id != conversation.id or trigger.workspace_id != workspace.id
            or trigger.direction != "inbound" or trigger.origin != "live"
            or abs((now() - aware(trigger.provider_timestamp)).total_seconds()) > 120
            or not QUESTION.fullmatch(" ".join(trigger.text.strip().split()))):
        raise HTTPException(409, "Automation trigger is stale or outside the verified intent")
    memory, _ = _fact(db, conversation, rule.memory_id)
    if memory.version != rule.memory_version or draft.text != rule.reply_template.format(fact=memory.text):
        raise HTTPException(409, "Automation fact or approved template changed")
    count = db.scalar(select(func.count(SendAttempt.id)).join(Draft, Draft.id == SendAttempt.draft_id).where(
        Draft.automation_id == rule.id, SendAttempt.workspace_id == workspace.id,
        SendAttempt.created_at >= now() - timedelta(hours=1), SendAttempt.draft_id != draft.id,
        SendAttempt.status != "blocked",
    ))
    if count >= rule.max_replies_per_hour:
        raise HTTPException(429, "Automation hourly reply budget exhausted")


def rule_payload(rule):
    return {"id": rule.id, "conversation_id": rule.conversation_id, "enabled": rule.enabled,
            "allowed_intents": rule.allowed_intents, "max_replies_per_hour": rule.max_replies_per_hour,
            "expires_at": aware(rule.expires_at), "quiet_start": rule.quiet_start, "quiet_end": rule.quiet_end,
            "version": rule.version, "memory_id": rule.memory_id, "memory_version": rule.memory_version,
            "reply_template": rule.reply_template}


@router.put("/conversations/{conversation_id}/automation")
@serialized_control
def grant_automation(conversation_id: str, body: AutomationGrant, request: Request,
                     db: Session = Depends(get_db), user=Depends(get_current_user)):
    conversation = conversation_for(db, user, conversation_id)
    workspace = db.get(Workspace, conversation.workspace_id)
    rule = db.scalar(select(Automation).where(Automation.conversation_id == conversation.id))
    if body.expires_at.tzinfo is None or aware(body.expires_at) <= now():
        raise HTTPException(422, "Automation expiry must include a timezone and be in the future")
    if aware(body.expires_at) > now() + timedelta(days=30):
        raise HTTPException(422, "Automation grant may last at most 30 days")
    memory = None
    if body.enabled:
        from .action_models import AutomationGrant as SelectedActionGrant
        from .actions import invalidate_actions
        selected = db.scalar(select(SelectedActionGrant).where(
            SelectedActionGrant.conversation_id == conversation.id,
            SelectedActionGrant.workspace_id == conversation.workspace_id,
        ))
        if selected:
            selected.enabled = False
            selected.version += 1
            invalidate_actions(db, conversation, "automation_engine_changed")
        if conversation.kind != "contact":
            raise HTTPException(403, "Group automation is unavailable")
        for capability in ("read", "retain", "draft", "send"):
            permission_for(db, conversation, capability)
        if workspace.paused or conversation.control_state in {"HUMAN_TAKEOVER", "RECONNECT_REVIEW", "AI_OFF", "READ_ONLY"}:
            raise HTTPException(409, "Resume the authorized conversation before enabling automation")
        connector = db.get(Connector, conversation.connector_id)
        if (connector.status != "connected" or connector.capabilities.get("send_text") != "supported"
                or not connector.lease_expires_at or aware(connector.lease_expires_at) <= now()):
            raise HTTPException(409, "Connected supported transport with a current lease required")
        memory, _ = _fact(db, conversation, body.memory_id)
        if memory.expires_at and aware(body.expires_at) > aware(memory.expires_at):
            raise HTTPException(409, "Automation grant must expire before its selected fact")
        text = body.reply_template.format(fact=memory.text)
        if len(text) > 4096:
            raise HTTPException(422, "Automation reply exceeds the transport text limit")
    if rule is None:
        rule = Automation(workspace_id=conversation.workspace_id, conversation_id=conversation.id,
                          version=1, expires_at=body.expires_at)
        db.add(rule)
    else:
        rule.version += 1
    for key, value in body.model_dump().items():
        setattr(rule, key, value)
    rule.memory_id = memory.id if memory else None
    rule.memory_version = memory.version if memory else None
    conversation.revision += 1
    conversation.control_epoch += 1
    invalidate_conversation(db, conversation, "automation_grant_changed")
    if body.enabled:
        conversation.control_state = "AUTO_ENABLED"
    elif conversation.control_state == "AUTO_ENABLED":
        conversation.control_state = "DRAFT_MODE"
    db.flush()
    audit(db, conversation.workspace_id, user.id, "automation.granted", rule.id, version=rule.version,
          enabled=rule.enabled, allowed_intents=rule.allowed_intents)
    db.commit()
    return rule_payload(rule)


def _stable_id(kind, source_id):
    return str(uuid5(NAMESPACE_URL, f"relationship-assistant:{kind}:{source_id}"))


def _plan(db, source, settings):
    if source.payload.get("live_eligible") is not True:
        return None, "not_live_eligible"
    message = db.get(Message, source.aggregate_id)
    conversation = db.get(Conversation, message.conversation_id) if message else None
    if (message is None or conversation is None or message.workspace_id != source.workspace_id
            or message.id != source.payload.get("message_id")
            or conversation.id != source.payload.get("conversation_id")
            or message.origin != "live" or message.direction != "inbound" or message.deleted
            or abs((now() - aware(message.provider_timestamp)).total_seconds()) > 120
            or source.payload.get("revision") != conversation.revision):
        return None, "stale_or_unauthorized_context"
    rule = db.scalar(select(Automation).where(Automation.conversation_id == conversation.id))
    if rule is None or not rule.enabled or aware(rule.expires_at) <= now():
        return None, "automation_unavailable"
    grant = db.scalar(select(AuditEvent).where(AuditEvent.action == "automation.granted",
                                              AuditEvent.resource_id == rule.id)
                      .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()))
    if (grant is None or grant.details.get("version") != rule.version
            or aware(message.received_at) < aware(grant.created_at)):
        return None, "message_predates_current_grant"
    if not QUESTION.fullmatch(" ".join(message.text.strip().split())):
        return None, "intent_requires_owner_review"
    workspace = db.get(Workspace, conversation.workspace_id)
    connector = db.get(Connector, conversation.connector_id)
    permission = permission_for(db, conversation, "send")
    memory, sources = _fact(db, conversation, rule.memory_id)
    profile = db.scalar(select(StyleProfile).where(StyleProfile.conversation_id == conversation.id)
                        .order_by(StyleProfile.version.desc()))
    text = rule.reply_template.format(fact=memory.text)
    expiry = min(aware(rule.expires_at), aware(message.provider_timestamp) + timedelta(seconds=120),
                 aware(message.received_at) + timedelta(seconds=120))
    if memory.expires_at:
        expiry = min(expiry, aware(memory.expires_at))
    draft = Draft(id=_stable_id("auto-draft", source.id), workspace_id=workspace.id,
                  conversation_id=conversation.id, recipient_id=conversation.provider_chat_id,
                  text=text, evidence_message_ids=[message.id] + [item.id for item in sources], missing_facts=[],
                  model_version="verified-fact-template-v1", profile_version=profile.version if profile else 0,
                  conversation_revision=conversation.revision, control_epoch=conversation.control_epoch,
                  permission_version=permission.version, pause_generation=workspace.pause_generation,
                  connector_fence=connector.fence, content_hash=content_hash(text), status="approved",
                  approved_hash=content_hash(text), approval_expires_at=expiry,
                  context_expires_at=memory.expires_at, automation_id=rule.id, automation_version=rule.version)
    _validate_current(db, draft, settings)
    db.add(draft)
    return draft, "approved_verified_fact"


async def process_automation(session_factory, settings, batch_size: int = 50):
    batch_size = min(max(batch_size, 1), 100)
    # Recover a prepared action if the worker stopped between SQL commit and dispatch.
    # The existing draft/send ledger is the only outbound action queue.
    with session_factory() as db:
        prepared = list(db.scalars(select(Draft.id).where(
            Draft.automation_id.is_not(None), Draft.status == "approved",
            Draft.model_version == "verified-fact-template-v1",
        ).order_by(Draft.created_at, Draft.id).limit(batch_size)))
    results = []
    for draft_id in prepared:
        results.append(await _execute(session_factory, settings, draft_id))
    remaining = batch_size - len(prepared)
    if remaining <= 0:
        return results
    with session_factory() as db:
        handled = select(Outbox.aggregate_id).where(Outbox.kind == "automation.handled")
        refs = db.execute(select(Outbox.id, Outbox.workspace_id).where(
            Outbox.kind == "message.accepted", ~Outbox.id.in_(handled),
            Outbox.payload["live_eligible"].as_boolean().is_(True),
            Outbox.created_at >= now() - timedelta(seconds=120),
        ).order_by(Outbox.created_at, Outbox.id).limit(remaining)).all()
    for source_id, workspace_id in refs:
        with submit_guard(workspace_id), session_factory() as db:
            lock_workspace(db, workspace_id)
            marker_id = _stable_id("auto-handled", source_id)
            if db.get(Outbox, marker_id):
                continue
            source = db.get(Outbox, source_id)
            if source is None:
                continue
            try:
                draft, reason = _plan(db, source, settings)
            except HTTPException as error:
                draft, reason = None, "policy_hold"
                logger.info("Automation event held by policy: HTTP %d", error.status_code)
            db.add(Outbox(id=marker_id, workspace_id=workspace_id, kind="automation.handled",
                          aggregate_id=source_id, payload={"reason": reason}, status="handled"))
            if draft:
                audit(db, workspace_id, "automation", "automation.draft_prepared", draft.id,
                      automation_id=draft.automation_id, source_id=source_id)
            try:
                db.commit()
            except IntegrityError:
                # A duplicate marker means another worker committed this exact
                # reference; it never permits replay or worker termination.
                db.rollback()
                continue
            draft_id = draft.id if draft else None
        if draft_id:
            result = await _execute(session_factory, settings, draft_id)
            results.append({"source_id": source_id, **result})
        else:
            results.append({"source_id": source_id, "status": "ignored", "reason": reason})
    return results


async def _execute(session_factory, settings, draft_id):
    try:
        return await dispatch_draft(session_factory, settings, draft_id, actor_id="automation")
    except HTTPException as error:
        with session_factory() as db:
            workspace_id = db.scalar(select(Draft.workspace_id).where(Draft.id == draft_id))
        if workspace_id:
            with submit_guard(workspace_id), session_factory() as db:
                lock_workspace(db, workspace_id)
                draft = db.get(Draft, draft_id)
                if draft and draft.status == "approved":
                    draft.status = "cancelled"
                    draft.approved_hash = None
                    db.commit()
        return {"status": "held", "reason": error.detail, "draft_id": draft_id}


@router.post("/internal/automation/run-once", dependencies=[Depends(require_internal)])
async def run_once(request: Request):
    return await process_automation(request.app.state.session_factory, request.app.state.settings)


async def run_worker(once=False):
    from .config import Settings
    from .db import make_database
    settings = Settings().prepare()
    _, factory = make_database(settings)
    while True:
        await process_automation(factory, settings)
        if once:
            return
        await asyncio.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(once=arguments.once))


if __name__ == "__main__":
    main()
