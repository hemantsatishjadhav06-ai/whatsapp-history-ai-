"""Typed owner commands, scoped catch-up, and hard metadata-only daily budgets.

Commands are authenticated structured requests. Chat content and model output
never become executable commands, recipients, integration scopes, or grants.
"""

from datetime import timedelta
from typing import Annotated, Literal, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from .access import audit, conversation_for, permission_for, workspace_for
from .action_models import OutboundAction
from .auth import get_current_user
from .core import serialized_control
from .db import aware, get_db, now
from .models import AuditEvent, Conversation, Draft, Message, Permission, SendAttempt, Task
from .native_models import MessageContext
from .people_models import UsageLedger, WorkspaceBudget
from .tasks import ACTIVE_TASKS, blocked_source_ids, task_json

router = APIRouter(tags=["companion", "budgets"])


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BudgetInput(Payload):
    expected_version: int = Field(ge=0)
    max_actions_per_day: int | None = Field(default=None, ge=0, le=1_000_000)
    max_tokens_per_day: int | None = Field(default=None, ge=0, le=2_000_000_000)
    max_cost_microusd_per_day: int | None = Field(default=None, ge=0, le=2_000_000_000)


def current_day():
    return now().date().isoformat()


def budget_usage(db, workspace_id, day=None):
    total = db.execute(select(func.coalesce(func.sum(UsageLedger.action_units), 0),
                              func.coalesce(func.sum(UsageLedger.token_units), 0),
                              func.coalesce(func.sum(UsageLedger.cost_microusd), 0)).where(
        UsageLedger.workspace_id == workspace_id, UsageLedger.window_day == (day or current_day()),
        UsageLedger.status.in_(["reserved", "consumed", "uncertain"]),
    )).one()
    return {"action_units": total[0], "token_units": total[1], "cost_microusd": total[2]}


def _budget(db, workspace_id):
    # The pilot's submit guard surrounds all external action callers. This short
    # row lock also serializes existing configured budgets on PostgreSQL.
    return db.scalar(select(WorkspaceBudget).where(WorkspaceBudget.workspace_id == workspace_id)
                     .with_for_update().execution_options(populate_existing=True))


def _quota_check(row, usage):
    if row is None:
        return
    for limit_name, unit in (("max_actions_per_day", "action_units"),
                             ("max_tokens_per_day", "token_units"),
                             ("max_cost_microusd_per_day", "cost_microusd")):
        limit = getattr(row, limit_name)
        if limit is not None and usage[unit] > limit:
            raise HTTPException(429, {"code": "QUOTA_HELD", "budget": limit_name,
                                      "message": "The UTC daily workspace budget would be exceeded"})


def check_action_budget(db, workspace_id, action_id=None):
    row = _budget(db, workspace_id)
    usage = budget_usage(db, workspace_id)
    existing = db.scalar(select(UsageLedger).where(UsageLedger.workspace_id == workspace_id,
                                                   UsageLedger.operation_key == f"action:{action_id}")) if action_id else None
    if (existing is None or existing.window_day != current_day()
            or existing.status not in {"reserved", "consumed", "uncertain"}):
        usage["action_units"] += 1
    _quota_check(row, usage)


def reserve_usage(db, workspace_id, operation_key, kind, *, action_units=0, token_units=0,
                  cost_microusd=0):
    """Trusted execution reservation; includes uncertain work and is replay-safe.

    Token/cost units must be supplied by the selected provider's trusted usage
    accounting. A zero cost here makes no claim that a provider call is free.
    Callers commit before provider calls and never hold SQL locks over them.
    """
    if (not operation_key or len(operation_key) > 160 or not kind or len(kind) > 40
            or any(not isinstance(value, int) or value < 0 for value in
                   (action_units, token_units, cost_microusd))):
        raise ValueError("Usage reservations require bounded metadata and nonnegative integer units")
    from .storage_authority import lock_workspace
    lock_workspace(db, workspace_id)
    budget = _budget(db, workspace_id)
    existing = db.scalar(select(UsageLedger).where(UsageLedger.workspace_id == workspace_id,
                                                   UsageLedger.operation_key == operation_key))
    usage = budget_usage(db, workspace_id)
    values = {"action_units": action_units, "token_units": token_units, "cost_microusd": cost_microusd}
    if existing and existing.status in {"consumed", "uncertain"}:
        # Accepted/uncertain operations are never budget permission to resubmit.
        if existing.window_day == current_day():
            _quota_check(budget, usage)
        return existing
    if existing and existing.window_day == current_day() and existing.status == "reserved":
        if any(getattr(existing, key) != value for key, value in values.items()) or existing.kind != kind:
            raise HTTPException(409, "Usage operation already has a different reservation")
        _quota_check(budget, usage)
        return existing
    candidate = {key: usage[key] + value for key, value in values.items()}
    _quota_check(budget, candidate)
    if existing:
        existing.window_day = current_day()
        existing.kind = kind
        existing.status = "reserved"
        for key, value in values.items():
            setattr(existing, key, value)
    else:
        existing = UsageLedger(workspace_id=workspace_id, operation_key=operation_key,
                               kind=kind, window_day=current_day(), status="reserved", **values)
        db.add(existing)
    db.flush()
    return existing


def reserve_action_budget(db, workspace_id, action_id, kind):
    return reserve_usage(db, workspace_id, f"action:{action_id}", kind, action_units=1)


def settle_action_budget(db, workspace_id, action_id, status):
    if status not in {"consumed", "uncertain", "released"}:
        raise ValueError("Unsupported usage settlement")
    row = db.scalar(select(UsageLedger).where(UsageLedger.workspace_id == workspace_id,
                                             UsageLedger.operation_key == f"action:{action_id}"))
    if row and (row.status == "reserved" or (row.status == "uncertain" and status == "consumed")):
        row.status = status


def budget_json(db, workspace_id, row):
    return {"workspace_id": workspace_id, "version": row.version if row else 0,
            "window_timezone": "UTC", "window_day": current_day(),
            "max_actions_per_day": row.max_actions_per_day if row else None,
            "max_tokens_per_day": row.max_tokens_per_day if row else None,
            "max_cost_microusd_per_day": row.max_cost_microusd_per_day if row else None,
            "usage": budget_usage(db, workspace_id),
            "accounting": "Reservations include model admission bounds and uncertain calls; validated provider usage reconciles tokens and configured verified pricing"}


@router.get("/workspaces/{workspace_id}/budget")
def get_budget(workspace_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    row = db.scalar(select(WorkspaceBudget).where(WorkspaceBudget.workspace_id == workspace_id))
    return budget_json(db, workspace_id, row)


@router.put("/workspaces/{workspace_id}/budget")
@serialized_control
def set_budget(workspace_id: str, body: BudgetInput, user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    row = _budget(db, workspace_id)
    version = row.version if row else 0
    if body.expected_version != version:
        raise HTTPException(409, "Workspace budget version changed")
    if row is None:
        row = WorkspaceBudget(workspace_id=workspace_id, version=1)
        db.add(row)
    else:
        row.version += 1
    for key in ("max_actions_per_day", "max_tokens_per_day", "max_cost_microusd_per_day"):
        setattr(row, key, getattr(body, key))
    audit(db, workspace_id, user.id, "budget.changed", workspace_id, version=row.version)
    db.commit()
    return budget_json(db, workspace_id, row)


@router.get("/workspaces/{workspace_id}/usage")
def get_usage(workspace_id: str, limit: int = Query(50, ge=1, le=200),
              user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    rows = db.scalars(select(UsageLedger).where(UsageLedger.workspace_id == workspace_id)
                      .order_by(UsageLedger.created_at.desc(), UsageLedger.id).limit(limit))
    return {"workspace_id": workspace_id, "entries": [
        {"id": row.id, "operation_key": row.operation_key, "kind": row.kind,
         "window_day": row.window_day, "status": row.status, "action_units": row.action_units,
         "token_units": row.token_units, "cost_microusd": row.cost_microusd} for row in rows]}


def scoped_digest(db, user, workspace_id, *, conversation_id=None, hours=24, limit=20):
    workspace = workspace_for(db, user, workspace_id)
    cutoff = now() - timedelta(hours=hours)
    readable = select(Permission.conversation_id).where(
        Permission.workspace_id == workspace_id, Permission.read.is_(True),
        (Permission.expires_at.is_(None) | (Permission.expires_at > now())),
    )
    if conversation_id:
        conversation = conversation_for(db, user, conversation_id)
        if conversation.workspace_id != workspace_id:
            raise HTTPException(404, "Conversation not found in the selected workspace")
        permission_for(db, conversation, "read")
        # The selected chat is the whole requested audience. In particular, a
        # receipt for forwarding into another chat is outside this view.
        readable = readable.where(Permission.conversation_id == conversation_id)
    conversations = list(db.scalars(select(Conversation).where(Conversation.workspace_id == workspace_id,
                                                               Conversation.id.in_(readable))
                                    .order_by(Conversation.created_at.desc(), Conversation.id).limit(limit)))
    items = []
    for conversation in conversations:
        permission_for(db, conversation, "read")
        blocked = blocked_source_ids(db, conversation)
        query = select(Message).where(Message.workspace_id == workspace_id,
                                       Message.conversation_id == conversation.id,
                                       Message.deleted.is_(False), Message.provider_timestamp >= cutoff)
        if blocked:
            query = query.where(Message.id.not_in(blocked))
        query = query.where(~select(MessageContext.id).where(
            MessageContext.workspace_id == workspace_id, MessageContext.message_id == Message.id,
            MessageContext.expires_at <= now(),
        ).exists())
        latest = db.scalar(query.order_by(Message.provider_timestamp.desc(), Message.id).limit(1))
        drafts = db.scalar(select(func.count()).select_from(Draft).where(
            Draft.workspace_id == workspace_id, Draft.conversation_id == conversation.id,
            Draft.status.in_(["needs_approval", "approved", "ready"])))
        tasks = db.scalar(select(func.count()).select_from(Task).where(
            Task.workspace_id == workspace_id, Task.conversation_id == conversation.id,
            Task.status.in_(ACTIVE_TASKS)))
        attempts = list(db.scalars(select(SendAttempt).where(
            SendAttempt.workspace_id == workspace_id, SendAttempt.conversation_id == conversation.id,
            SendAttempt.created_at >= cutoff).order_by(SendAttempt.created_at.desc()).limit(5)))
        new_actions = list(db.scalars(select(OutboundAction).where(
            OutboundAction.workspace_id == workspace_id, OutboundAction.conversation_id == conversation.id,
            OutboundAction.destination_conversation_id.in_(readable), OutboundAction.created_at >= cutoff,
        ).order_by(OutboundAction.created_at.desc()).limit(5)))
        items.append({"conversation_id": conversation.id, "title": conversation.title,
                      "kind": conversation.kind, "control_state": conversation.control_state,
                      "latest": {"source_ref": latest.id, "preview": latest.text[:200],
                                 "timestamp": aware(latest.provider_timestamp).isoformat()} if latest else None,
                      "pending_drafts": drafts, "pending_tasks": tasks,
                      "recent_receipts": [{"action_ref": row.draft_id, "status": row.status,
                                           "reason_code": row.error_code} for row in attempts]
                      + [{"action_ref": row.id, "kind": row.kind, "status": row.status,
                          "reason_code": row.reason_code} for row in new_actions]})
    scoped_ids = [conversation.id for conversation in conversations]
    personal = [] if conversation_id else list(db.scalars(select(Task).where(Task.workspace_id == workspace_id,
                                                 Task.conversation_id.is_(None), Task.status.in_(ACTIVE_TASKS))
                               .order_by(Task.due_at.asc().nulls_last(), Task.id).limit(limit)))
    # Only content-free audit records for currently readable resources are shown.
    activities = list(db.scalars(select(AuditEvent).where(AuditEvent.workspace_id == workspace_id,
                                                         AuditEvent.resource_id.in_(scoped_ids if conversation_id
                                                                                    else [workspace_id, *scoped_ids]),
                                                         AuditEvent.created_at >= cutoff)
                                .order_by(AuditEvent.created_at.desc(), AuditEvent.id).limit(limit)))
    return {"workspace_id": workspace_id, "conversation_id": conversation_id,
            "scope": "conversation" if conversation_id else "workspace", "paused": workspace.paused, "hours": hours,
            "conversations": items, "personal_tasks": [task_json(row) for row in personal],
            "activity": [{"id": row.id, "action": row.action, "resource_ref": row.resource_id,
                          "timestamp": aware(row.created_at).isoformat()} for row in activities],
            "summary_kind": "scoped_source_preview", "generated_by_model": False}


@router.get("/assistant/digest")
def digest(workspace_id: str, conversation_id: str | None = Query(None, min_length=1, max_length=36),
           hours: int = Query(24, ge=1, le=168), limit: int = Query(20, ge=1, le=100),
           user=Depends(get_current_user), db=Depends(get_db)):
    return scoped_digest(db, user, workspace_id, conversation_id=conversation_id, hours=hours, limit=limit)


class Command(Payload):
    workspace_id: str


class CatchMeUp(Command):
    command: Literal["catch_me_up"]
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    hours: int = Field(default=24, ge=1, le=168)
    limit: int = Field(default=20, ge=1, le=100)


class WriteWithMe(Command):
    command: Literal["write_with_me"]
    conversation_id: str
    instruction: str = Field(default="", max_length=2000)


class TeachMe(Command):
    command: Literal["teach_me"]
    conversation_id: str
    text: str = Field(min_length=1, max_length=4000)
    source_message_ids: list[str] = Field(min_length=1, max_length=40)
    status: Literal["candidate", "confirmed"] = "candidate"


class Pause(Command):
    command: Literal["pause"]
    conversation_id: str | None = None


class Resume(Command):
    command: Literal["resume"]
    conversation_id: str | None = None


OwnerCommand = Annotated[Union[CatchMeUp, WriteWithMe, TeachMe, Pause, Resume], Field(discriminator="command")]


@router.post("/assistant/commands")
def run_command(body: OwnerCommand, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, body.workspace_id)
    if hasattr(body, "conversation_id") and body.conversation_id:
        conversation = conversation_for(db, user, body.conversation_id)
        if conversation.workspace_id != body.workspace_id:
            raise HTTPException(404, "Conversation not found in the selected workspace")
    if isinstance(body, CatchMeUp):
        result = scoped_digest(db, user, body.workspace_id, conversation_id=body.conversation_id,
                               hours=body.hours, limit=body.limit)
    elif isinstance(body, WriteWithMe):
        from .intelligence import DraftRequest, create_draft
        result = create_draft(body.conversation_id, request, DraftRequest(instruction=body.instruction), db=db, user=user)
    elif isinstance(body, TeachMe):
        from .intelligence import MemoryCreate, create_memory
        result = create_memory(body.conversation_id, MemoryCreate(text=body.text,
                               source_message_ids=body.source_message_ids, status=body.status), db=db, user=user)
    elif isinstance(body, Pause):
        from .core import pause_all, takeover
        result = (takeover(body.conversation_id, user=user, db=db) if body.conversation_id
                  else pause_all(body.workspace_id, user=user, db=db))
    else:
        from .core import resume, resume_all
        result = (resume(body.conversation_id, user=user, db=db) if body.conversation_id
                  else resume_all(body.workspace_id, request=request, user=user, db=db))
    return {"command": body.command, "result": result}
