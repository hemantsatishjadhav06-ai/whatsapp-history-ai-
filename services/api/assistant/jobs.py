"""Trusted proactive triggers. Chat content can never create an authorized job.

An authenticated owner grants the exact stored operation once. Every occurrence
uses a durable run key, current authority and the ordinary outbound dispatcher.
Local reminders are private records and do not call a messaging provider.
"""

import argparse
import asyncio
from datetime import UTC, datetime, time, timedelta
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, select

from .access import audit, conversation_for, permission_for, workspace_for
from .auth import get_current_user
from .db import aware, get_db, now, uid
from .jobs_models import AuthorizedJob, JobRun
from .messaging import require_internal, submit_guard
from .models import Connector, Conversation, Memory, Message, Suppression, Workspace

router = APIRouter(tags=["jobs"])
ACTIVE = ("scheduled", "held", "running")
ActionKind = Literal["SEND_TEXT", "QUOTE", "REACTION", "FORWARD", "REMINDER"]


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    conversation_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=120)
    purpose: str = Field(min_length=1, max_length=500)
    action_kind: ActionKind
    content: str = Field(default="", max_length=4000)
    emoji: str | None = Field(default=None, max_length=32)
    target_message_id: str | None = None
    route_id: str | None = None
    evidence_message_ids: list[str] = Field(default_factory=list, max_length=30)
    memory_ids: list[str] = Field(default_factory=list, max_length=20)
    due_at: datetime
    expires_at: datetime
    timezone: str = "Asia/Kolkata"
    recurrence: Literal["none", "daily", "weekly"] = "none"
    max_runs: int = Field(default=1, ge=1, le=100)
    max_runs_per_hour: int = Field(default=3, ge=1, le=10)
    max_lateness_seconds: int = Field(default=3600, ge=30, le=86400)
    ambiguity_policy: Literal["reject", "earlier", "later"] = "reject"
    gap_policy: Literal["reject", "skip"] = "reject"
    quiet_start: str | None = None
    quiet_end: str | None = None

    @field_validator("due_at", "expires_at", mode="before")
    @classmethod
    def explicit_datetime(cls, value):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("z", "Z"))
            except ValueError:
                raise ValueError("Job times must be explicit ISO datetimes") from None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Job times require an explicit timezone offset")
        return aware(value)

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("A valid IANA timezone is required") from None
        return value

    @field_validator("evidence_message_ids", "memory_ids")
    @classmethod
    def unique_refs(cls, value):
        if len(set(value)) != len(value) or any(not item or len(item) > 36 for item in value):
            raise ValueError("References must be unique database IDs")
        return value

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def quiet_clock(cls, value):
        if value is not None:
            try:
                parsed = time.fromisoformat(value)
            except ValueError:
                raise ValueError("Quiet hours require HH:MM") from None
            if len(value) != 5 or parsed.second or parsed.tzinfo:
                raise ValueError("Quiet hours require HH:MM")
        return value

    @model_validator(mode="after")
    def coherent_action(self):
        self.content = self.content.strip()
        if not self.purpose.strip():
            raise ValueError("Purpose must not be blank")
        if self.action_kind == "REMINDER":
            if not self.content.strip() or self.conversation_id or self.target_message_id or self.route_id:
                raise ValueError("Local reminders require text and no external audience")
            if self.emoji or self.evidence_message_ids or self.memory_ids:
                raise ValueError("Local reminders cannot carry messaging source references")
        elif not self.conversation_id:
            raise ValueError("An external action requires an exact conversation")
        if self.action_kind in {"SEND_TEXT", "QUOTE"} and not self.content.strip():
            raise ValueError("Sending requires exact owner-authorized text")
        if self.action_kind in {"QUOTE", "REACTION", "FORWARD"} and not self.target_message_id:
            raise ValueError("A native action requires an exact source message")
        if self.action_kind == "REACTION" and (not self.emoji or self.content):
            raise ValueError("A reaction requires an emoji and no message text")
        if self.action_kind == "FORWARD" and (not self.route_id or self.content or self.emoji):
            raise ValueError("A forward requires an exact route and authentic source")
        if self.action_kind != "FORWARD" and self.route_id:
            raise ValueError("Only a forward may carry a route")
        if self.action_kind != "REACTION" and self.emoji:
            raise ValueError("Only a reaction may carry an emoji")
        if self.action_kind == "SEND_TEXT" and self.target_message_id:
            raise ValueError("Use QUOTE for an explicit native reply target")
        if self.recurrence == "none" and self.max_runs != 1:
            raise ValueError("A one-off job has exactly one occurrence")
        if bool(self.quiet_start) != bool(self.quiet_end) or (self.quiet_start and self.quiet_start == self.quiet_end):
            raise ValueError("Quiet hours require two distinct clock times")
        return self


class JobControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    operation: Literal["hold", "resume", "cancel"]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _reason(error):
    return error.detail.get("reason_code", error.detail.get("code", "SCOPE_DENIED")) if isinstance(error.detail, dict) else str(error.detail)


def operation_payload(job):
    return {"kind": job.action_kind, "conversation_id": job.conversation_id, "connector_id": job.connector_id,
            "text": job.content, "emoji": job.emoji, "target_message_id": job.target_message_id,
            "route_id": job.route_id, "evidence_message_ids": job.evidence_message_ids,
            "memory_ids": job.memory_ids}


def job_json(row, *, private=True):
    result = {"id": row.id, "workspace_id": row.workspace_id, "conversation_id": row.conversation_id,
              "connector_id": row.connector_id, "action_kind": row.action_kind, "status": row.status,
              "hold_reason": row.hold_reason, "version": row.version, "runs_done": row.runs_done,
              "max_runs": row.max_runs, "due_at": aware(row.due_at).isoformat(),
              "expires_at": aware(row.expires_at).isoformat(), "timezone": row.timezone,
              "recurrence": row.recurrence, "ambiguity_policy": row.ambiguity_policy, "gap_policy": row.gap_policy,
              "max_lateness_seconds": row.max_lateness_seconds, "content_hash": row.content_hash,
              "trigger_type": "AUTHORIZED_JOB", "external_action": row.action_kind != "REMINDER"}
    if private:
        result.update(purpose=row.purpose, content=row.content, emoji=row.emoji, target_message_id=row.target_message_id,
                      route_id=row.route_id, evidence_message_ids=row.evidence_message_ids, memory_ids=row.memory_ids)
    return result


def _job_for(db, user, job_id):
    job = db.get(AuthorizedJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    workspace_for(db, user, job.workspace_id)
    if job.conversation_id:
        conversation = conversation_for(db, user, job.conversation_id)
        permission_for(db, conversation, "read")
    return job


def _sources(db, job, *, snapshot=False):
    from .native import message_available
    if not job.conversation_id:
        return
    source_ids = set(job.evidence_message_ids)
    if job.target_message_id:
        source_ids.add(job.target_message_id)
    suppressed = {item for record in db.scalars(select(Suppression).where(
        Suppression.workspace_id == job.workspace_id, Suppression.conversation_id == job.conversation_id))
        for item in record.source_message_ids}
    revisions = {}
    for source_id in source_ids:
        source = db.get(Message, source_id)
        if (source is None or source.workspace_id != job.workspace_id or source.conversation_id != job.conversation_id
                or source.deleted or source_id in suppressed
                or not message_available(db, source)):
            raise HTTPException(409, "SOURCE_MISSING")
        revisions[source_id] = source.revision
    versions = {}
    for memory_id in job.memory_ids:
        memory = db.get(Memory, memory_id)
        if (memory is None or memory.workspace_id != job.workspace_id or memory.conversation_id != job.conversation_id
                or memory.status != "confirmed" or (memory.expires_at and aware(memory.expires_at) <= now())):
            raise HTTPException(409, "CONTEXT_STALE")
        for source_id in memory.source_message_ids:
            source = db.get(Message, source_id)
            if (source is None or source.deleted or source.workspace_id != job.workspace_id
                    or source.conversation_id != job.conversation_id or source_id in suppressed
                    or source.revision != memory.source_revision.get(source_id)
                    or not message_available(db, source)):
                raise HTTPException(409, "CONTEXT_STALE")
            revisions[source_id] = source.revision
        versions[memory_id] = memory.version
    if snapshot:
        job.source_revisions, job.memory_versions = revisions, versions
    elif revisions != job.source_revisions or versions != job.memory_versions:
        raise HTTPException(409, "CONTEXT_STALE")


def _scope(db, job, *, snapshot=False):
    workspace = db.get(Workspace, job.workspace_id)
    if workspace is None or workspace.owner_id != job.created_by:
        raise HTTPException(403, "SCOPE_DENIED")
    if job.action_kind == "REMINDER":
        return workspace
    conversation = db.get(Conversation, job.conversation_id)
    connector = db.get(Connector, job.connector_id)
    if (conversation is None or connector is None or conversation.workspace_id != workspace.id
            or connector.workspace_id != workspace.id or conversation.connector_id != connector.id):
        raise HTTPException(403, "SCOPE_DENIED")
    permission = permission_for(db, conversation, "send")
    for key in ("read", "retain", "draft"):
        permission_for(db, conversation, key)
    if conversation.control_state in {"AI_OFF", "READ_ONLY", "HUMAN_TAKEOVER", "RECONNECT_REVIEW"}:
        raise HTTPException(409, "HUMAN_TAKEOVER")
    if connector.status != "connected" or connector.lease_expires_at is None or aware(connector.lease_expires_at) <= now():
        raise HTTPException(409, "CAPABILITY_UNAVAILABLE")
    if snapshot:
        job.permission_version, job.control_epoch, job.connector_fence = permission.version, conversation.control_epoch, connector.fence
    elif (job.permission_version != permission.version or job.control_epoch != conversation.control_epoch
          or job.connector_fence != connector.fence):
        raise HTTPException(409, "CONTEXT_STALE")
    _sources(db, job, snapshot=snapshot)
    if job.route_id:
        from .action_models import ForwardRoute
        route = db.get(ForwardRoute, job.route_id)
        if (route is None or route.workspace_id != workspace.id or route.owner_id != job.created_by
                or route.connector_id != connector.id or route.source_conversation_id != conversation.id
                or not route.enabled or aware(route.expires_at) <= now()):
            raise HTTPException(403, "ROUTE_DENIED")
        destination = db.get(Conversation, route.destination_conversation_id)
        if destination is None or destination.workspace_id != workspace.id or destination.connector_id != connector.id:
            raise HTTPException(403, "ROUTE_DENIED")
        permission_for(db, conversation, "share")
        destination_permission = permission_for(db, destination, "send")
        for key in ("read", "retain", "share"):
            permission_for(db, destination, key)
        if destination.control_state in {"AI_OFF", "READ_ONLY", "HUMAN_TAKEOVER", "RECONNECT_REVIEW"}:
            raise HTTPException(409, "HUMAN_TAKEOVER")
        current_destination = {"id": destination.id, "recipient_id": destination.provider_chat_id,
                               "permission_version": destination_permission.version,
                               "control_epoch": destination.control_epoch}
        if snapshot:
            job.route_version, job.destination_snapshot = route.version, current_destination
        elif job.route_version != route.version or job.destination_snapshot != current_destination:
            raise HTTPException(409, "ROUTE_DENIED")
    return workspace


def in_quiet_hours(job, instant):
    if not job.quiet_start:
        return False
    local = aware(instant).astimezone(ZoneInfo(job.timezone)).strftime("%H:%M")
    if job.quiet_start < job.quiet_end:
        return job.quiet_start <= local < job.quiet_end
    return local >= job.quiet_start or local < job.quiet_end


def _check_occurrence(db, job, *, include_hold=True):
    instant = now()
    if (aware(job.expires_at) <= instant or aware(job.due_at) + timedelta(seconds=job.max_lateness_seconds) <= instant
            or job.runs_done >= job.max_runs):
        raise HTTPException(409, "EXPIRED")
    if aware(job.due_at) > instant:
        raise HTTPException(409, "JOB_NOT_DUE")
    if job.status not in ACTIVE or job.hold_reason == "OWNER_HOLD":
        raise HTTPException(409, "JOB_NOT_ACTIVE")
    workspace = _scope(db, job)
    if include_hold and job.action_kind != "REMINDER":
        if workspace.paused:
            raise HTTPException(409, "GLOBAL_PAUSE")
        if in_quiet_hours(job, instant):
            raise HTTPException(409, "QUIET_HOURS")
        count = db.scalar(select(func.count()).select_from(JobRun).where(
            JobRun.job_id == job.id, JobRun.status.in_(["accepted", "delivered", "read", "uncertain"]),
            JobRun.created_at >= instant - timedelta(hours=1)))
        if count >= job.max_runs_per_hour:
            raise HTTPException(409, "QUOTA_HELD")


def authorize_job_action(db, job, action):
    """Called at the last action submission boundary, inside its workspace guard."""
    current = db.get(AuthorizedJob, job.id)
    if (current is None or current.workspace_id != action.workspace_id
            or action.authorized_job_id != current.id or action.connector_id != current.connector_id):
        raise HTTPException(403, {"reason_code": "SCOPE_DENIED"})
    if action.authorized_job_version != current.version:
        raise HTTPException(409, {"reason_code": "CONTEXT_STALE"})
    if action.authorized_job_run_key != current.run_key:
        raise HTTPException(409, {"reason_code": "CONTEXT_STALE"})
    if _digest(operation_payload(current)) != current.content_hash:
        raise HTTPException(409, {"reason_code": "CONTEXT_STALE"})
    if (action.conversation_id != current.conversation_id or action.kind != current.action_kind
            or action.payload.get("text", "") != current.content
            or action.payload.get("emoji") != current.emoji
            or getattr(action, "target_message_id", None) != current.target_message_id
            or getattr(action, "route_id", None) != current.route_id
            or action.evidence_message_ids != current.evidence_message_ids):
        raise HTTPException(403, {"reason_code": "SCOPE_DENIED"})
    try:
        _check_occurrence(db, current)
    except HTTPException as error:
        raise HTTPException(error.status_code, {"reason_code": _reason(error)}) from None


@router.post("/jobs", status_code=201)
def create_job(body: JobCreate, user=Depends(get_current_user), db=Depends(get_db)):
    workspace = workspace_for(db, user, body.workspace_id)
    with submit_guard(workspace.id):
        from .storage_authority import lock_workspace
        lock_workspace(db, workspace.id)
        db.refresh(workspace)
        request_hash = _digest(body.model_dump(mode="json"))
        existing = db.scalar(select(AuthorizedJob).where(AuthorizedJob.workspace_id == workspace.id,
                                                       AuthorizedJob.idempotency_key == body.idempotency_key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, "Idempotency key already used for another job")
            return job_json(existing)
        if body.due_at <= now() or body.expires_at <= body.due_at or body.expires_at > now() + timedelta(days=90):
            raise HTTPException(422, "Job requires a future due time and bounded expiry within 90 days")
        connector_id = None
        if body.conversation_id:
            conversation = conversation_for(db, user, body.conversation_id)
            if conversation.workspace_id != workspace.id:
                raise HTTPException(422, "Job conversation must belong to the selected workspace")
            connector_id = conversation.connector_id
        local = body.due_at.astimezone(ZoneInfo(body.timezone))
        job = AuthorizedJob(id=uid(), created_by=user.id, connector_id=connector_id, request_hash=request_hash,
                            **body.model_dump(), local_time=local.time().replace(tzinfo=None).isoformat(),
                            weekday=local.weekday(), runs_done=0, version=1, content_hash="",
                            status="scheduled", hold_reason=None)
        _scope(db, job, snapshot=True)
        job.content_hash = _digest(operation_payload(job))
        if workspace.paused and job.action_kind != "REMINDER":
            job.status, job.hold_reason = "held", "GLOBAL_PAUSE"
        db.add(job)
        db.flush()
        audit(db, workspace.id, user.id, "job.created", job.id, kind=job.action_kind, recurrence=job.recurrence)
        db.commit()
        return job_json(job)


@router.get("/jobs")
def list_jobs(workspace_id: str, limit: int = Query(50, ge=1, le=200), user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    result = []
    for job in db.scalars(select(AuthorizedJob).where(AuthorizedJob.workspace_id == workspace_id)
                         .order_by(AuthorizedJob.created_at.desc()).limit(limit)):
        try:
            _job_for(db, user, job.id)
        except HTTPException:
            continue
        result.append(job_json(job))
    return result


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    return job_json(_job_for(db, user, job_id))


@router.get("/jobs/{job_id}/runs")
def job_runs(job_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    _job_for(db, user, job_id)
    return [{"id": row.id, "occurrence": row.occurrence, "run_key": row.run_key, "action_id": row.action_id,
             "due_at": aware(row.due_at).isoformat(), "status": row.status, "error_code": row.error_code}
            for row in db.scalars(select(JobRun).where(JobRun.job_id == job_id).order_by(JobRun.occurrence))]


@router.patch("/jobs/{job_id}")
def control_job(job_id: str, body: JobControl, user=Depends(get_current_user), db=Depends(get_db)):
    job = _job_for(db, user, job_id)
    with submit_guard(job.workspace_id):
        from .storage_authority import lock_workspace
        lock_workspace(db, job.workspace_id)
        db.refresh(job)
        if job.version != body.expected_version:
            raise HTTPException(409, "Job version changed")
        if job.status not in ("scheduled", "held", "needs_review"):
            raise HTTPException(409, "Submitted or terminal jobs require reconciliation; they cannot be replayed")
        if body.operation == "cancel":
            job.status, job.hold_reason = "cancelled", None
        elif body.operation == "hold":
            job.status, job.hold_reason = "held", "OWNER_HOLD"
        else:
            if aware(job.expires_at) <= now() or aware(job.due_at) + timedelta(seconds=job.max_lateness_seconds) <= now():
                raise HTTPException(409, "EXPIRED")
            _scope(db, job, snapshot=True)
            job.status, job.hold_reason = "scheduled", None
        job.version += 1
        audit(db, job.workspace_id, user.id, "job.controlled", job.id, operation=body.operation, version=job.version)
        db.commit()
        return job_json(job)


def resolve_local(local, timezone, ambiguity_policy="reject", gap_policy="reject"):
    """Resolve a wall clock by round-trip; no implicit DST fold/gap choice."""
    zone = ZoneInfo(timezone)
    candidates = {local.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)
                  if local.replace(tzinfo=zone, fold=fold).astimezone(UTC).astimezone(zone).replace(tzinfo=None) == local}
    ordered = sorted(candidates)
    if not ordered:
        if gap_policy == "skip":
            return None
        raise ValueError("DST_GAP")
    if len(ordered) > 1:
        if ambiguity_policy == "reject":
            raise ValueError("DST_AMBIGUOUS")
        return ordered[0] if ambiguity_policy == "earlier" else ordered[-1]
    return ordered[0]


def next_occurrence(job):
    if job.recurrence == "none":
        return None
    local_date = aware(job.due_at).astimezone(ZoneInfo(job.timezone)).date()
    step = 1 if job.recurrence == "daily" else 7
    for offset in range(step, 15, step):
        local = datetime.combine(local_date + timedelta(days=offset), time.fromisoformat(job.local_time))
        result = resolve_local(local, job.timezone, job.ambiguity_policy, job.gap_policy)
        if result is not None:
            return result
    raise ValueError("DST_GAP")


def _advance(job):
    job.runs_done += 1
    if job.runs_done >= job.max_runs:
        job.status, job.hold_reason = "completed", None
        return
    try:
        upcoming = next_occurrence(job)
    except ValueError as error:
        job.status, job.hold_reason = "needs_review", str(error)
        return
    if upcoming is None or upcoming >= aware(job.expires_at):
        job.status, job.hold_reason = "completed", None
    else:
        job.due_at, job.status, job.hold_reason = upcoming, "scheduled", None


def _proposal(job):
    from .actions import ActionProposal
    return ActionProposal(kind=job.action_kind, intent="forwarding" if job.action_kind == "FORWARD" else "factual_answer",
                          text=job.content, emoji=job.emoji,
                          target_message_id=job.target_message_id, route_id=job.route_id,
                          evidence_message_ids=job.evidence_message_ids)


async def process_jobs(session_factory, settings, job_id=None):
    """Bounded worker tick. Expired recurrences are recorded, never burst-sent."""
    from .storage_authority import lock_workspace
    with session_factory() as db:
        query = select(AuthorizedJob.id).where(AuthorizedJob.status.in_(ACTIVE), AuthorizedJob.due_at <= now())
        if job_id:
            query = query.where(AuthorizedJob.id == job_id)
        ids = list(db.scalars(query.order_by(AuthorizedJob.due_at).limit(100)))
    results = []
    for selected in ids:
        with session_factory() as db:
            workspace_id = db.scalar(select(AuthorizedJob.workspace_id).where(AuthorizedJob.id == selected))
        if workspace_id is None:
            continue
        with submit_guard(workspace_id), session_factory() as db:
            lock_workspace(db, workspace_id)
            job = db.get(AuthorizedJob, selected)
            if job is None or job.status not in ACTIVE or aware(job.due_at) > now():
                continue
            run = db.scalar(select(JobRun).where(JobRun.job_id == job.id, JobRun.occurrence == job.runs_done))
            try:
                _check_occurrence(db, job)
            except HTTPException as error:
                reason = _reason(error)
                if reason in {"GLOBAL_PAUSE", "QUIET_HOURS", "QUOTA_HELD", "JOB_NOT_ACTIVE"}:
                    job.status, job.hold_reason = "held", reason if reason != "JOB_NOT_ACTIVE" else job.hold_reason
                elif reason == "EXPIRED":
                    if run is None:
                        db.add(JobRun(workspace_id=workspace_id, job_id=job.id, occurrence=job.runs_done,
                                      job_version=job.version, run_key=job.run_key, due_at=job.due_at,
                                      status="expired", error_code=reason))
                    elif run.action_id:
                        # A claimed external operation has a durable ledger. Never
                        # turn its uncertain outcome into permission to rerun.
                        job.status, job.hold_reason = "uncertain", "RECONCILE_REQUIRED"
                        db.commit()
                        results.append({"job_id": selected, "status": "uncertain", "reason_code": "RECONCILE_REQUIRED"})
                        continue
                    if aware(job.expires_at) <= now() or job.recurrence == "none":
                        job.status, job.hold_reason = "expired", None
                    else:
                        _advance(job)
                else:
                    job.status, job.hold_reason = "needs_review", reason
                db.commit()
                results.append({"job_id": selected, "status": job.status, "reason_code": reason})
                continue
            if run is None:
                run = JobRun(id=uid(), workspace_id=workspace_id, job_id=job.id, occurrence=job.runs_done,
                             job_version=job.version, run_key=job.run_key, due_at=job.due_at, status="ready")
                db.add(run)
            if job.action_kind == "REMINDER":
                run.status = "reminded"
                audit(db, workspace_id, "job-worker", "reminder.due", run.id, job_id=job.id)
                _advance(job)
                db.commit()
                results.append({"job_id": selected, "run_id": run.id, "status": "reminded", "external_action": False})
                continue
            if not run.action_id:
                from .actions import prepare_action
                job.status, job.hold_reason = "running", None
                try:
                    action = prepare_action(db, job.created_by, job.conversation_id, _proposal(job), authorized_job=job)
                except HTTPException as error:
                    run.status, run.error_code = "blocked", _reason(error)[:80]
                    job.status, job.hold_reason = ("held" if run.error_code in {"QUOTA_HELD", "QUIET_HOURS", "GLOBAL_PAUSE"}
                                                  else "needs_review"), run.error_code
                    db.commit()
                    results.append({"job_id": selected, "status": job.status, "reason_code": run.error_code})
                    continue
                run.action_id = action.id
                audit(db, workspace_id, "job-worker", "job.action_prepared", run.id, job_id=job.id, action_id=action.id)
            action_id, run_id = run.action_id, run.id
            db.commit()
        from .actions import dispatch_action
        try:
            result = await dispatch_action(session_factory, settings, action_id)
        except HTTPException as error:
            result = {"status": "blocked", "reason_code": _reason(error)}
        with submit_guard(workspace_id), session_factory() as db:
            lock_workspace(db, workspace_id)
            job, run = db.get(AuthorizedJob, selected), db.get(JobRun, run_id)
            if job is None or run is None:
                continue
            status = result.get("status", "uncertain")
            # Multiple workers may observe the same immutable action outcome.
            # Only the current occurrence can consume the recurrence once.
            if job.runs_done != run.occurrence or job.version != run.job_version:
                results.append({**result, "job_id": selected, "run_id": run_id})
                continue
            if run.status in {"accepted", "delivered", "read"}:
                results.append({**result, "job_id": selected, "run_id": run_id})
                continue
            run.status, run.error_code = status, result.get("reason_code")
            if status in {"accepted", "delivered", "read"}:
                _advance(job)
            elif status in {"held", "ready"}:
                job.status, job.hold_reason = "held", result.get("reason_code", "QUOTA_HELD")
            elif status in {"submitting", "dispatching"}:
                job.status, job.hold_reason = "running", None
            elif status == "uncertain":
                job.status, job.hold_reason = "uncertain", "RECONCILE_REQUIRED"
            else:
                job.status, job.hold_reason = "needs_review", result.get("reason_code", "ACTION_BLOCKED")
            db.commit()
        results.append({**result, "job_id": selected, "run_id": run_id})
    return results


@router.post("/internal/jobs/run-due", dependencies=[Depends(require_internal)])
async def run_due(request: Request):
    return await process_jobs(request.app.state.session_factory, request.app.state.settings)


def forget_job_sources(db, conversation_id, source_ids):
    sources = set(source_ids)
    for job in db.scalars(select(AuthorizedJob).where(AuthorizedJob.conversation_id == conversation_id)):
        dependent = set(job.source_revisions) & sources
        for memory_id in job.memory_ids:
            memory = db.get(Memory, memory_id)
            if memory and set(memory.source_message_ids) & sources:
                dependent.add(memory_id)
        if dependent:
            job.status, job.hold_reason = "cancelled", "SOURCE_FORGOTTEN"
            job.content, job.purpose, job.emoji = "", "", None
            job.evidence_message_ids, job.source_revisions = [], {}
            job.memory_ids, job.memory_versions = [], {}
            job.target_message_id, job.route_id = None, None
            job.version += 1


def purge_job_data(db, conversation_id):
    ids = select(AuthorizedJob.id).where(AuthorizedJob.conversation_id == conversation_id)
    db.execute(delete(JobRun).where(JobRun.job_id.in_(ids)))
    db.execute(delete(AuthorizedJob).where(AuthorizedJob.conversation_id == conversation_id))


def purge_workspace_jobs(db, workspace_id):
    """Includes owner-local reminders, which deliberately have no chat scope."""
    ids = select(AuthorizedJob.id).where(AuthorizedJob.workspace_id == workspace_id)
    db.execute(delete(JobRun).where(JobRun.job_id.in_(ids)))
    db.execute(delete(AuthorizedJob).where(AuthorizedJob.workspace_id == workspace_id))


def export_job_data(db, conversation_id):
    return [job_json(job) for job in db.scalars(select(AuthorizedJob).where(AuthorizedJob.conversation_id == conversation_id))]


def export_local_reminders(db, workspace_id):
    return [job_json(job) for job in db.scalars(select(AuthorizedJob).where(
        AuthorizedJob.workspace_id == workspace_id, AuthorizedJob.action_kind == "REMINDER"))]


def main():
    parser = argparse.ArgumentParser(description="Run trusted authorized jobs; all outbound authority is rechecked")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from .config import Settings
    from .db import make_database
    settings = Settings().prepare()
    engine, factory = make_database(settings)
    async def run():
        while True:
            await process_jobs(factory, settings)
            from .messaging import process_due
            await process_due(factory, settings)
            if args.once:
                return
            await asyncio.sleep(5)
    try:
        asyncio.run(run())
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
