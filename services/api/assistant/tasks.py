"""Owner-authored follow-up tasks and a scoped, multi-platform inbox foundation.

These records do not schedule notifications, extract promises, or send messages.
Dates are explicit instants supplied by the owner, never inferred from chat text.
"""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update

from .access import audit, conversation_for, permission_for, workspace_for
from .auth import get_current_user
from .core import serialized_control
from .db import aware, get_db, now
from .models import Connector, Conversation, Draft, Message, Permission, Suppression, Task

router = APIRouter(tags=["tasks", "inbox"])
TaskStatus = Literal["pending", "needs_review", "completed", "cancelled"]
ACTIVE_TASKS = ("pending", "needs_review")


def explicit_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("t", "T").replace("z", "Z"))
        except ValueError:
            pass
    raise ValueError("due_at must be an ISO datetime with an explicit offset")


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreate(Payload):
    workspace_id: str
    conversation_id: str | None = None
    title: str = Field(min_length=1, max_length=1000)
    due_at: datetime | None = None
    timezone: str | None = None
    source_message_ids: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("due_at", mode="before")
    @classmethod
    def due_at_is_explicit(cls, value):
        return explicit_datetime(value)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Task title must not be blank")
        return value.strip()

    @field_validator("due_at")
    @classmethod
    def due_at_has_offset(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("due_at requires an explicit timezone offset")
        return aware(value) if value else value

    @field_validator("source_message_ids")
    @classmethod
    def unique_sources(cls, values):
        if len(values) != len(set(values)) or any(not value or len(value) > 36 for value in values):
            raise ValueError("Source IDs must be unique database message IDs")
        return values


class TaskEdit(Payload):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    due_at: datetime | None = None
    status: TaskStatus | None = None

    @field_validator("due_at", mode="before")
    @classmethod
    def due_at_is_explicit(cls, value):
        return explicit_datetime(value)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Task title must not be blank")
        return value.strip() if value else value

    @field_validator("due_at")
    @classmethod
    def due_at_has_offset(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("due_at requires an explicit timezone offset")
        return aware(value) if value else value


def blocked_source_ids(db, conversation):
    return {source_id for suppression in db.scalars(select(Suppression).where(
        Suppression.workspace_id == conversation.workspace_id,
        Suppression.conversation_id == conversation.id,
    )) for source_id in suppression.source_message_ids}


def checked_sources(db, conversation, source_ids):
    from .native import message_available
    rows = list(db.scalars(select(Message).where(
        Message.workspace_id == conversation.workspace_id, Message.conversation_id == conversation.id,
        Message.id.in_(source_ids), Message.deleted.is_(False),
    )))
    rows = [row for row in rows if message_available(db, row)]
    if {row.id for row in rows} != set(source_ids):
        raise HTTPException(422, "Task sources must be available in the selected conversation")
    if set(source_ids) & blocked_source_ids(db, conversation):
        raise HTTPException(409, "Task sources were suppressed from derived records")
    return rows


def task_json(row):
    return {"id": row.id, "workspace_id": row.workspace_id, "conversation_id": row.conversation_id,
            "title": row.title, "due_at": aware(row.due_at).isoformat() if row.due_at else None,
            "timezone": row.timezone, "status": row.status, "source_message_ids": row.source_message_ids,
            "source_revision": row.source_revision, "created_by": row.created_by, "version": row.version,
            "notifications_enabled": False}


def task_for(db, user, task_id):
    row = db.get(Task, task_id)
    if row is None:
        raise HTTPException(404, "Task not found")
    workspace_for(db, user, row.workspace_id)
    return row


def source_scope(db, user, task, *, retain=False):
    if not task.source_message_ids:
        return None
    if not task.conversation_id:
        raise HTTPException(409, "Task evidence has no conversation scope")
    conversation = conversation_for(db, user, task.conversation_id)
    if conversation.workspace_id != task.workspace_id:
        raise HTTPException(404, "Task not found")
    permission_for(db, conversation, "read")
    if retain:
        permission_for(db, conversation, "retain")
    return conversation


@router.post("/tasks", status_code=201)
@serialized_control
def create_task(body: TaskCreate, user=Depends(get_current_user), db=Depends(get_db)):
    workspace = workspace_for(db, user, body.workspace_id)
    timezone = body.timezone if body.timezone is not None else workspace.timezone
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "A valid IANA timezone is required") from None
    sources = []
    if body.conversation_id:
        conversation = conversation_for(db, user, body.conversation_id)
        if conversation.workspace_id != workspace.id:
            raise HTTPException(422, "Task conversation must belong to the selected workspace")
        if body.source_message_ids:
            permission_for(db, conversation, "read")
            permission_for(db, conversation, "retain")
            sources = checked_sources(db, conversation, body.source_message_ids)
    elif body.source_message_ids:
        raise HTTPException(422, "Message evidence requires an explicit conversation scope")
    row = Task(workspace_id=workspace.id, conversation_id=body.conversation_id, title=body.title,
               due_at=body.due_at, timezone=timezone, status="pending",
               source_message_ids=body.source_message_ids,
               source_revision={source.id: source.revision for source in sources}, created_by="owner", version=1)
    db.add(row)
    db.flush()
    audit(db, workspace.id, user.id, "task.created", row.id, source_count=len(sources), due_at_set=bool(row.due_at))
    db.commit()
    return task_json(row)


@router.get("/tasks")
def list_tasks(workspace_id: str, status: TaskStatus | None = None,
               limit: int = Query(50, ge=1, le=200), user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    query = select(Task).where(Task.workspace_id == workspace_id)
    if status is not None:
        query = query.where(Task.status == status)
    # Filter permission scopes in SQL before limiting so inaccessible tasks do not starve the page.
    readable = select(Permission.conversation_id).where(
        Permission.workspace_id == workspace_id, Permission.read.is_(True),
        (Permission.expires_at.is_(None) | (Permission.expires_at > now())),
    )
    query = query.where(Task.conversation_id.is_(None) | (func.json_array_length(Task.source_message_ids) == 0)
                        | Task.conversation_id.in_(readable))
    result = []
    for row in db.scalars(query.order_by(Task.created_at.desc(), Task.id).limit(limit)):
        try:
            source_scope(db, user, row)
        except HTTPException as error:
            if error.status_code in {403, 404, 409}:
                continue
            raise
        result.append(task_json(row))
    return result


@router.patch("/tasks/{task_id}")
@serialized_control
def edit_task(task_id: str, body: TaskEdit, user=Depends(get_current_user), db=Depends(get_db)):
    row = task_for(db, user, task_id)
    changed = body.model_fields_set - {"expected_version"}
    if not changed or any(getattr(body, field) is None for field in changed - {"due_at"}):
        raise HTTPException(422, "Supply a task change; only due_at may be cleared")
    if row.version != body.expected_version:
        raise HTTPException(409, "Task version changed; reload before editing")
    conversation = source_scope(db, user, row, retain=True)
    values = {field: getattr(body, field) for field in changed}
    if conversation and body.status == "pending":
        # Explicit owner review reaffirms the current sources after an edit; never do this automatically.
        sources = checked_sources(db, conversation, row.source_message_ids)
        values["source_revision"] = {source.id: source.revision for source in sources}
    values["version"] = body.expected_version + 1
    result = db.execute(update(Task).where(Task.id == row.id, Task.workspace_id == row.workspace_id,
                                          Task.version == body.expected_version)
                        .values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Task version changed; reload before editing")
    audit(db, row.workspace_id, user.id, "task.updated", row.id, version=body.expected_version + 1,
          changed_fields=sorted(changed))
    db.commit()
    db.refresh(row)
    return task_json(row)


@router.delete("/tasks/{task_id}", status_code=204)
@serialized_control
def cancel_task(task_id: str, expected_version: int | None = Query(None, ge=1),
                user=Depends(get_current_user), db=Depends(get_db)):
    row = task_for(db, user, task_id)
    # Cancellation remains possible after the source conversation's permission is revoked.
    version = expected_version if expected_version is not None else row.version
    if version != row.version:
        raise HTTPException(409, "Task version changed; reload before cancelling")
    if row.status == "cancelled":
        return Response(status_code=204)
    result = db.execute(update(Task).where(Task.id == row.id, Task.workspace_id == row.workspace_id,
                                          Task.version == version)
                        .values(status="cancelled", version=version + 1)
                        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Task version changed; reload before cancelling")
    audit(db, row.workspace_id, user.id, "task.cancelled", row.id, version=version + 1)
    db.commit()
    return Response(status_code=204)


def invalidate_task_sources(db, conversation, message_id, *, forgotten=False):
    """Invalidate a derivation after source edits; erase forgotten derived task content."""
    for row in db.scalars(select(Task).where(
        Task.workspace_id == conversation.workspace_id, Task.conversation_id == conversation.id,
    ).with_for_update().execution_options(populate_existing=True)):
        if message_id not in row.source_message_ids:
            continue
        if forgotten:
            row.title = "Forgotten task"
            row.source_message_ids = []
            row.source_revision = {}
            row.status = "cancelled"
            row.version += 1
        elif row.status in ACTIVE_TASKS:
            row.status = "needs_review"
            row.version += 1


@router.get("/inbox")
def unified_inbox(workspace_id: str, limit: int = Query(100, ge=1, le=200),
                  user=Depends(get_current_user), db=Depends(get_db)):
    from .native_models import MessageContext
    current_message = ~select(MessageContext.id).where(
        MessageContext.message_id == Message.id, MessageContext.expires_at <= now(),
    ).correlate(Message).exists()
    workspace = workspace_for(db, user, workspace_id)
    readable = select(Permission.conversation_id).where(
        Permission.workspace_id == workspace.id, Permission.read.is_(True),
        (Permission.expires_at.is_(None) | (Permission.expires_at > now())),
    )
    latest_activity = select(func.max(Message.provider_timestamp)).where(
        Message.workspace_id == workspace.id, Message.conversation_id == Conversation.id,
        Message.deleted.is_(False),
        current_message,
    ).correlate(Conversation).scalar_subquery()
    conversations = list(db.scalars(select(Conversation).where(
        Conversation.workspace_id == workspace.id, Conversation.id.in_(readable),
    ).order_by(latest_activity.desc().nulls_last(), Conversation.created_at.desc(), Conversation.id).limit(limit)))
    items = []
    for conversation in conversations:
        permission_for(db, conversation, "read")
        connector = db.get(Connector, conversation.connector_id)
        if connector is None or connector.workspace_id != workspace.id:
            continue
        blocked = blocked_source_ids(db, conversation)
        latest_query = select(Message).where(
            Message.workspace_id == workspace.id, Message.conversation_id == conversation.id,
            Message.deleted.is_(False),
            current_message,
        )
        if blocked:
            latest_query = latest_query.where(Message.id.not_in(blocked))
        latest = db.scalar(latest_query.order_by(Message.provider_timestamp.desc(), Message.id.desc()).limit(1))
        drafts = db.scalar(select(func.count()).select_from(Draft).where(
            Draft.workspace_id == workspace.id, Draft.conversation_id == conversation.id,
            Draft.status.in_(["needs_approval", "approved", "ready"]),
        ))
        tasks = db.scalar(select(func.count()).select_from(Task).where(
            Task.workspace_id == workspace.id, Task.conversation_id == conversation.id,
            Task.status.in_(ACTIVE_TASKS),
        ))
        items.append({"conversation_id": conversation.id, "title": conversation.title,
                      "kind": conversation.kind, "platform": connector.provider,
                      "connector_id": connector.id, "connector_health": {
                          "status": connector.status, "fence": connector.fence,
                          "lease_expires_at": aware(connector.lease_expires_at).isoformat() if connector.lease_expires_at else None,
                          "capabilities": connector.capabilities,
                      }, "control_state": conversation.control_state,
                      "latest_message": {"id": latest.id, "text_preview": latest.text[:280],
                                         "direction": latest.direction,
                                         "timestamp": aware(latest.provider_timestamp).isoformat()} if latest else None,
                      "pending_draft_count": drafts, "owner_task_count": tasks})
    # Latest activity is the useful order for an inbox, with empty conversations at the end.
    items.sort(key=lambda item: item["latest_message"]["timestamp"] if item["latest_message"] else "", reverse=True)
    personal_tasks = db.scalar(select(func.count()).select_from(Task).where(
        Task.workspace_id == workspace.id, Task.conversation_id.is_(None), Task.status.in_(ACTIVE_TASKS),
    ))
    return {"workspace_id": workspace.id, "workspace_paused": workspace.paused,
            "conversations": items, "personal_owner_task_count": personal_tasks,
            "notifications_enabled": False, "platform_scope": "Registered, explicitly permitted conversations"}
