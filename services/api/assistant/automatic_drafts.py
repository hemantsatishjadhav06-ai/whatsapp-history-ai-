"""Opt-in live-message drafts. SQL admission and model work run separately.

No proposal is approved or sent here. Claims are durable before provider calls;
crashed or unknown calls are terminal and are never retried automatically.
"""

import asyncio
from datetime import datetime, timedelta
import hashlib
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from .access import audit, conversation_for, permission_for
from .auth import get_current_user
from .automatic_drafts_models import AutoDraftGrant, AutomaticDraftJob, AutomaticDraftSlot
from .core import serialized_control
from .db import aware, get_db, now, uid
from .messaging import submit_guard
from .models import Connector, Conversation, Draft, Message, MessageEvent, Outbox, User, Workspace
from .native import message_available
from .storage_authority import lock_workspace

router = APIRouter(tags=["automatic drafts"])
JOB_TTL = timedelta(minutes=10)
GLOBAL_MAX_INFLIGHT = 4
_claim_guard = threading.Lock()
_generation_guard = threading.Lock()


class GrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    expected_version: int = Field(ge=0)
    expires_at: datetime | None = None
    max_drafts_per_hour: int = Field(default=3, ge=1, le=10)

    @field_validator("expires_at")
    @classmethod
    def zoned_expiry(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Expiry requires an explicit timezone")
        return aware(value) if value else None


def _model_reason(settings):
    from .intelligence import validate_model_configuration
    if settings.model_provider == "disabled":
        return "MODEL_DISABLED"
    try:
        validate_model_configuration(settings)
    except HTTPException:
        return "MODEL_NOT_CONFIGURED"
    return None


def _readiness_reason(db, workspace_id, settings):
    reason = _model_reason(settings)
    if reason or settings.model_provider == "mock":
        return reason
    from .intelligence import verified_model_pricing
    from .people_models import WorkspaceBudget
    budget = db.scalar(select(WorkspaceBudget).where(WorkspaceBudget.workspace_id == workspace_id))
    if budget and budget.max_cost_microusd_per_day is not None and verified_model_pricing(settings) is None:
        return "MODEL_PRICING_UNVERIFIED"
    return None


def _authority(db, conversation, grant):
    workspace = db.get(Workspace, conversation.workspace_id)
    connector = db.get(Connector, conversation.connector_id)
    if (grant is None or workspace is None or grant.workspace_id != workspace.id
            or grant.conversation_id != conversation.id or grant.owner_id != workspace.owner_id):
        raise HTTPException(409, "GRANT_UNAVAILABLE")
    if not grant.enabled:
        raise HTTPException(409, "GRANT_DISABLED")
    if aware(grant.expires_at) <= now():
        raise HTTPException(409, "GRANT_EXPIRED")
    try:
        for capability in ("read", "retain", "draft"):
            permission = permission_for(db, conversation, capability)
    except HTTPException:
        raise HTTPException(409, "PERMISSION_REVOKED") from None
    if workspace.paused:
        raise HTTPException(409, "GLOBAL_PAUSE")
    if conversation.control_state not in {"DRAFT_MODE", "AUTO_ENABLED"}:
        raise HTTPException(409, "CONTROL_HELD")
    if (connector is None or connector.workspace_id != workspace.id or connector.status != "connected"
            or connector.lease_expires_at is None or aware(connector.lease_expires_at) <= now()):
        raise HTTPException(409, "CONNECTOR_UNAVAILABLE")
    return {"grant_version": grant.version, "owner_id": workspace.owner_id,
            "conversation_revision": conversation.revision, "control_epoch": conversation.control_epoch,
            "permission_version": permission.version, "connector_fence": connector.fence,
            "pause_generation": workspace.pause_generation}


def job_json(job):
    return {"id": job.id, "status": job.status, "reason_code": job.reason_code,
            "draft_id": job.draft_id, "expires_at": aware(job.expires_at).isoformat()}


def grant_json(db, conversation, grant, settings):
    reason = None
    if grant and grant.enabled:
        try:
            _authority(db, conversation, grant)
        except HTTPException as error:
            reason = error.detail
        reason = reason or _readiness_reason(db, conversation.workspace_id, settings)
    latest = db.scalar(select(AutomaticDraftJob).where(
        AutomaticDraftJob.workspace_id == conversation.workspace_id,
        AutomaticDraftJob.conversation_id == conversation.id,
    ).order_by(AutomaticDraftJob.created_at.desc(), AutomaticDraftJob.id.desc()).limit(1))
    return {"conversation_id": conversation.id, "enabled": bool(grant and grant.enabled),
            "version": grant.version if grant else 0,
            "expires_at": aware(grant.expires_at).isoformat() if grant else None,
            "max_drafts_per_hour": grant.max_drafts_per_hour if grant else 3,
            "status": "blocked" if reason else ("ready" if grant and grant.enabled else "disabled"),
            "reason_code": reason, "latest_job": job_json(latest) if latest else None}


@router.get("/conversations/{conversation_id}/automatic-drafts")
def get_grant(conversation_id: str, request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    conversation = conversation_for(db, user, conversation_id)
    grant = db.scalar(select(AutoDraftGrant).where(AutoDraftGrant.conversation_id == conversation.id))
    return grant_json(db, conversation, grant, request.app.state.settings)


def invalidate_automatic_drafts(db, conversation_id, reason="CONTEXT_STALE"):
    db.execute(update(AutomaticDraftJob).where(
        AutomaticDraftJob.conversation_id == conversation_id, AutomaticDraftJob.status == "queued",
    ).values(status="cancelled", reason_code=reason).execution_options(synchronize_session=False))
    # Preserve the inflight slot until the provider returns or the bounded claim
    # expires; revocation cannot admit another call while this one still runs.
    db.execute(update(AutomaticDraftJob).where(
        AutomaticDraftJob.conversation_id == conversation_id, AutomaticDraftJob.status == "generating",
    ).values(reason_code=reason).execution_options(synchronize_session=False))
    drafts = select(AutomaticDraftJob.draft_id).where(AutomaticDraftJob.conversation_id == conversation_id)
    db.execute(update(Draft).where(Draft.id.in_(drafts), Draft.status.in_(["needs_approval", "approved", "ready"]))
               .values(status="cancelled", approved_hash=None, approval_expires_at=None)
               .execution_options(synchronize_session=False))


@router.put("/conversations/{conversation_id}/automatic-drafts")
@serialized_control
def put_grant(conversation_id: str, body: GrantInput, request: Request,
              db=Depends(get_db), user=Depends(get_current_user)):
    conversation = conversation_for(db, user, conversation_id)
    grant = db.scalar(select(AutoDraftGrant).where(AutoDraftGrant.conversation_id == conversation.id))
    if body.expected_version != (grant.version if grant else 0):
        raise HTTPException(409, {"reason_code": "VERSION_CONFLICT"})
    stamp = now()
    expiry = body.expires_at or stamp + timedelta(days=7)
    if body.enabled:
        if expiry <= stamp or expiry > stamp + timedelta(days=30):
            raise HTTPException(422, "Automatic draft grants require a future expiry within 30 days")
        for capability in ("read", "retain", "draft"):
            permission_for(db, conversation, capability)
    if grant is None:
        grant = AutoDraftGrant(workspace_id=conversation.workspace_id, conversation_id=conversation.id,
                               owner_id=user.id, version=1)
        db.add(grant)
    else:
        grant.version += 1
    grant.enabled, grant.granted_at, grant.expires_at = body.enabled, stamp, expiry
    grant.max_drafts_per_hour = body.max_drafts_per_hour
    invalidate_automatic_drafts(db, conversation.id, "GRANT_CHANGED")
    audit(db, conversation.workspace_id, user.id, "automatic_drafts.granted", conversation.id,
          enabled=grant.enabled, version=grant.version, max_drafts_per_hour=grant.max_drafts_per_hour)
    db.commit()
    return grant_json(db, conversation, grant, request.app.state.settings)


def _check_job(db, job):
    if job.reason_code:
        raise HTTPException(409, "JOB_CANCELLED")
    if job.status == "generating" and job.claim_expires_at and aware(job.claim_expires_at) <= now():
        raise HTTPException(409, "CLAIM_EXPIRED")
    if job.status == "generating" and db.scalar(select(AutomaticDraftSlot.id).where(
            AutomaticDraftSlot.claim_id == job.id, AutomaticDraftSlot.expires_at > now())) is None:
        raise HTTPException(409, "CLAIM_EXPIRED")
    conversation = db.get(Conversation, job.conversation_id)
    grant = db.get(AutoDraftGrant, job.grant_id)
    source = db.get(Message, job.message_id)
    if aware(job.expires_at) <= now():
        raise HTTPException(409, "EXPIRED")
    if (conversation is None or conversation.workspace_id != job.workspace_id
            or source is None or source.workspace_id != job.workspace_id
            or source.conversation_id != conversation.id or source.connector_id != job.connector_id
            or source.direction != "inbound" or source.origin != "live"
            or source.author_kind not in {"contact", "contact_human"} or source.deleted
            or source.revision != job.message_revision or not message_available(db, source)):
        raise HTTPException(409, "SOURCE_CHANGED")
    from .intelligence import suppressed_sources
    if source.id in suppressed_sources(db, conversation):
        raise HTTPException(409, "SOURCE_FORGOTTEN")
    if _authority(db, conversation, grant) != job.authority_snapshot:
        raise HTTPException(409, "CONTEXT_STALE")
    return conversation, grant


def _admit(session_factory, settings):
    with session_factory() as db:
        # The trusted canonical event is the only trigger. Existing permissions
        # never opt in, and changing a grant never replays previous messages.
        source_ids = list(db.scalars(select(Outbox.id).join(
            AutoDraftGrant, AutoDraftGrant.conversation_id == Outbox.payload["conversation_id"].as_string(),
        ).where(Outbox.kind == "message.accepted", Outbox.payload["live_eligible"].as_boolean().is_(True),
                Outbox.workspace_id == AutoDraftGrant.workspace_id,
                Outbox.created_at >= now() - JOB_TTL, Outbox.created_at >= AutoDraftGrant.granted_at,
                AutoDraftGrant.enabled.is_(True), AutoDraftGrant.expires_at > now(),
                ~select(AutomaticDraftJob.id).where(AutomaticDraftJob.source_outbox_id == Outbox.id).exists(),
        ).order_by(Outbox.created_at, Outbox.id).limit(100)))
    results = []
    for source_id in source_ids:
        with session_factory() as db:
            outbox = db.get(Outbox, source_id)
            if outbox is None:
                continue
            with submit_guard(outbox.workspace_id):
                lock_workspace(db, outbox.workspace_id)
                db.expire_all()
                outbox = db.get(Outbox, source_id)
                if outbox is None or db.scalar(select(AutomaticDraftJob.id).where(
                        AutomaticDraftJob.source_outbox_id == source_id)):
                    continue
                source = db.get(Message, outbox.aggregate_id)
                event = db.scalar(select(MessageEvent).where(MessageEvent.event_id == outbox.payload.get("event_id")))
                if (source is None or event is None or event.message_id != source.id
                        or event.event_type != "message.created" or source.origin != "live"
                        or source.direction != "inbound" or source.author_kind not in {"contact", "contact_human"}):
                    continue
                grant = db.scalar(select(AutoDraftGrant).where(AutoDraftGrant.conversation_id == source.conversation_id))
                if (grant is None or not grant.enabled or aware(grant.granted_at) > aware(outbox.created_at)
                        or aware(grant.expires_at) <= now()):
                    continue
                conversation = db.get(Conversation, source.conversation_id)
                job = AutomaticDraftJob(id=uid(), workspace_id=source.workspace_id,
                    conversation_id=source.conversation_id, connector_id=source.connector_id, owner_id=grant.owner_id,
                    grant_id=grant.id, source_outbox_id=source_id, event_id=event.event_id,
                    message_id=source.id, message_revision=event.source_revision,
                    expires_at=min(aware(outbox.created_at) + JOB_TTL, aware(grant.expires_at)), status="queued")
                try:
                    job.authority_snapshot = _authority(db, conversation, grant)
                    if outbox.payload.get("revision") != conversation.revision:
                        raise HTTPException(409, "CONTEXT_STALE")
                    _check_job(db, job)
                    reason = _readiness_reason(db, job.workspace_id, settings)
                    if reason:
                        job.status, job.reason_code = "blocked", reason
                except HTTPException as error:
                    job.status, job.reason_code = "cancelled", str(error.detail)
                db.add(job)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()  # Concurrent canonical event admission is deduplicated.
                    continue
                results.append(job_json(job))
    return results


async def process_automatic_drafts(session_factory, settings):
    """Bounded SQL-only lane. It never calls a model or transport."""
    return await asyncio.to_thread(_admit, session_factory, settings)


def _claim(session_factory, settings):
    with _claim_guard, session_factory() as db:
        # One short global decision lock bounds provider work across replicas.
        # Owner and workspace authority share no locks with the network call.
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(684260921)"))
        stamp = now()
        db.execute(update(AutomaticDraftJob).where(
            AutomaticDraftJob.status == "generating", AutomaticDraftJob.claim_expires_at <= stamp,
        ).values(status="uncertain", reason_code="MODEL_OUTCOME_UNKNOWN"))
        db.execute(update(AutomaticDraftJob).where(
            AutomaticDraftJob.status == "queued", AutomaticDraftJob.expires_at <= stamp,
        ).values(status="expired", reason_code="EXPIRED"))
        slots = []
        for slot_id in range(GLOBAL_MAX_INFLIGHT):
            slot = db.get(AutomaticDraftSlot, slot_id)
            if slot is None:
                slot = AutomaticDraftSlot(id=slot_id)
                db.add(slot)
            if slot.expires_at and aware(slot.expires_at) <= stamp:
                slot.claim_id, slot.owner_hash, slot.expires_at = None, None, None
            slots.append(slot)
        inflight = {slot.owner_hash for slot in slots if slot.claim_id}
        available = next((slot for slot in slots if not slot.claim_id), None)
        if available is None:
            db.commit()
            return None
        jobs = list(db.scalars(select(AutomaticDraftJob).where(AutomaticDraftJob.status == "queued")
                    .order_by(AutomaticDraftJob.last_checked_at.asc().nullsfirst(),
                              AutomaticDraftJob.created_at, AutomaticDraftJob.id).limit(100)))
        for job in jobs:
            checked = aware(job.last_checked_at) + timedelta(microseconds=1) if job.last_checked_at else stamp
            job.last_checked_at = max(stamp, checked)
            owner_hash = hashlib.sha256(f"automatic-draft:{job.owner_id}".encode()).hexdigest()
            if owner_hash in inflight:
                continue
            lock_workspace(db, f"automatic-draft-owner:{job.owner_id}")
            lock_workspace(db, job.workspace_id)
            try:
                _, grant = _check_job(db, job)
                reason = _readiness_reason(db, job.workspace_id, settings)
                if reason:
                    raise HTTPException(409, reason)
                count = db.scalar(select(func.count(AutomaticDraftJob.id)).where(
                    AutomaticDraftJob.grant_id == grant.id, AutomaticDraftJob.started_at >= stamp - timedelta(hours=1)))
                if count >= grant.max_drafts_per_hour:
                    raise HTTPException(429, "DRAFT_HOURLY_LIMIT")
            except HTTPException as error:
                job.status, job.reason_code = "blocked", str(error.detail)
                continue
            job.status, job.started_at = "generating", stamp
            job.claim_expires_at = stamp + timedelta(seconds=min(max(settings.model_timeout_seconds, 1), 120) + 30)
            job_id = job.id
            available.claim_id, available.owner_hash, available.expires_at = job.id, owner_hash, job.claim_expires_at
            db.commit()  # Release every claim/owner/workspace lock before provider work.
            return job_id
        db.commit()
        return None


def _generate_one(session_factory, settings):
    if not _generation_guard.acquire(blocking=False):
        return None
    job_id = None
    try:
        job_id = _claim(session_factory, settings)
        if job_id is None:
            return None
        armed = False
        authority_checks = 0
        with session_factory() as db:
            job = db.get(AutomaticDraftJob, job_id)
            if job is None:
                return {"status": "purged"}
            owner = db.get(User, job.owner_id)
            conversation_id, workspace_id = job.conversation_id, job.workspace_id
            provider_deadline = aware(job.claim_expires_at) - timedelta(seconds=5)

            def authority_check(session):
                nonlocal armed, authority_checks
                session.expire_all()
                current = session.get(AutomaticDraftJob, job_id)
                if current is None or current.status != "generating":
                    raise HTTPException(409, "JOB_CANCELLED")
                _check_job(session, current)
                authority_checks += 1
                armed = authority_checks >= 2

            from .intelligence import content_hash, generate_scoped_result, generation_snapshot
            try:
                result, version, snapshot, expiries = generate_scoped_result(
                    db, owner, conversation_id, settings, "Prepare a reply to the latest incoming message for owner review.",
                    authority_check=authority_check, provider_deadline=provider_deadline)
                with submit_guard(workspace_id):
                    lock_workspace(db, workspace_id)
                    db.expire_all()
                    job = db.get(AutomaticDraftJob, job_id)
                    if job is None:
                        return {"status": "purged"}
                    if job.status != "generating":
                        return job_json(job)
                    conversation, grant = _check_job(db, job)
                    fresh = generation_snapshot(db, conversation, owner, snapshot["evidence_revisions"],
                                                snapshot["memory_versions"], bool(snapshot["profile_version"]))
                    if fresh != snapshot:
                        raise HTTPException(409, "CONTEXT_STALE")
                    row = Draft(workspace_id=workspace_id, conversation_id=conversation.id,
                        recipient_id=conversation.provider_chat_id, text=result.text,
                        evidence_message_ids=result.evidence_message_ids, missing_facts=result.missing_facts,
                        model_version=version, profile_version=snapshot["profile_version"],
                        conversation_revision=snapshot["conversation_revision"], control_epoch=snapshot["control_epoch"],
                        permission_version=snapshot["permission_version"], pause_generation=snapshot["pause_generation"],
                        connector_fence=snapshot["connector_fence"], content_hash=content_hash(result.text),
                        context_expires_at=min([aware(job.expires_at), aware(grant.expires_at), *expiries]),
                        status="needs_approval")
                    db.add(row)
                    db.flush()
                    job.status, job.draft_id = "drafted", row.id
                    audit(db, workspace_id, owner.id, "automatic_draft.prepared", row.id, job_id=job.id)
                    db.commit()
                    return job_json(job)
            except Exception as error:
                db.rollback()
                with submit_guard(workspace_id):
                    lock_workspace(db, workspace_id)
                    job = db.get(AutomaticDraftJob, job_id)
                    if job is None:
                        return {"status": "purged"}
                    if job.status == "generating":
                        if isinstance(error, HTTPException) and error.status_code in {403, 404, 409}:
                            job.status, job.reason_code = "cancelled", "CONTEXT_STALE"
                        else:
                            job.status = "uncertain" if armed else "blocked"
                            job.reason_code = "MODEL_OUTCOME_UNKNOWN" if armed else (
                                "QUOTA_HELD" if isinstance(error, HTTPException) and error.status_code == 429
                                else "MODEL_ADMISSION_BLOCKED")
                        db.commit()
                    return job_json(job)
    finally:
        if job_id:
            with _claim_guard, session_factory() as db:
                if db.get_bind().dialect.name == "postgresql":
                    db.execute(text("SELECT pg_advisory_xact_lock(684260921)"))
                db.execute(update(AutomaticDraftSlot).where(AutomaticDraftSlot.claim_id == job_id)
                           .values(claim_id=None, owner_hash=None, expires_at=None))
                db.commit()
        _generation_guard.release()


async def run_one_generation(session_factory, settings):
    """At most one provider request per worker; controls continue on the event loop."""
    return await asyncio.to_thread(_generate_one, session_factory, settings)


async def generation_loop(session_factory, settings):
    while True:
        await run_one_generation(session_factory, settings)
        await asyncio.sleep(1)


def forget_automatic_draft_sources(db, conversation_id, sources):
    # Context may include any permitted source, so Forget cancels every pending
    # proposal for the chat; the job ledger carries IDs and authority only.
    invalidate_automatic_drafts(db, conversation_id, "SOURCE_FORGOTTEN")


def purge_automatic_draft_data(db, conversation_id):
    db.execute(delete(AutomaticDraftJob).where(AutomaticDraftJob.conversation_id == conversation_id))
    db.execute(delete(AutoDraftGrant).where(AutoDraftGrant.conversation_id == conversation_id))


def purge_workspace_automatic_drafts(db, workspace_id):
    db.execute(delete(AutomaticDraftJob).where(AutomaticDraftJob.workspace_id == workspace_id))
    db.execute(delete(AutoDraftGrant).where(AutoDraftGrant.workspace_id == workspace_id))


def export_automatic_draft_data(db, conversation_id):
    grant = db.scalar(select(AutoDraftGrant).where(AutoDraftGrant.conversation_id == conversation_id))
    return {"grant": {"id": grant.id, "owner_id": grant.owner_id, "enabled": grant.enabled,
                      "version": grant.version, "expires_at": aware(grant.expires_at).isoformat(),
                      "max_drafts_per_hour": grant.max_drafts_per_hour} if grant else None,
            "jobs": [job_json(job) | {"message_id": job.message_id, "message_revision": job.message_revision,
                                      "event_id": job.event_id}
                     for job in db.scalars(select(AutomaticDraftJob).where(
                         AutomaticDraftJob.conversation_id == conversation_id))]}
