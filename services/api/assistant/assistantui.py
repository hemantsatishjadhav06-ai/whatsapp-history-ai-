"""Bounded authenticated UI snapshots and honest public integration metadata."""

import base64
from datetime import datetime
import hashlib
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import load_only

from .access import audit, conversation_for, permission_for, workspace_for
from .action_models import AutomationGrant, ForwardRoute, OutboundAction
from .actions import action_public, grant_public, route_public
from .auth import digest, get_current_user, require_origin, user_payload
from .companion import get_budget
from .core import public, serialized_control
from .db import aware, get_db, now
from .intelligence import memory_json, profile_json
from .jobs import job_json, resolve_local
from .jobs_models import AuthorizedJob
from .lifecycle import policy_view
from .lifecycle_models import RetentionPolicy
from .messaging import content_hash, draft_payload
from .models import (AuditEvent, Connector, Conversation, Draft, Memory, Message, Permission,
                     StyleProfile, Task, Workspace)
from .people import PrefetchedContactScope, contact_json, source_valid
from .people_models import ContactSource, LocalContact
from .native_models import MessageContext
from .models import Suppression
from .tasks import task_json

router = APIRouter(tags=["UI bootstrap", "client configuration"])


def auth_config(settings):
    return {"backend_configured": True, "google_configured": bool(settings.google_client_id),
            "client_id": settings.google_client_id or None,
            "google": {"client_id": settings.google_client_id or None,
                       "android_client_id": settings.google_android_client_id or None,
                       "ios_client_id": settings.google_ios_client_id or None,
                       "configured": bool(settings.google_client_id),
                       "native_configured": bool(settings.google_android_client_id or settings.google_ios_client_id)},
            "browser_authentication": "http_only_cookie_with_csrf",
            "native_authentication": "revocable_bearer_with_google_nonce_and_S256_proof",
            "native_refresh_supported": True,
            "providers": {"whatsapp_personal": {"status": "unavailable", "pairing_supported": False},
                          "whatsapp_business": {"status": "configured" if settings.whatsapp_phone_number_id
                                                 and settings.whatsapp_access_token else "not_configured",
                                                "live_verification_required": True},
                          "google_contacts": {"status": "planned"}, "calendar": {"status": "planned"},
                          "gmail": {"status": "planned"}, "other_social": {"status": "planned"}},
            "external_sends_enabled": settings.enable_external_sends,
            "model": {"status": "disabled" if settings.model_provider == "disabled" else
                      "simulation" if settings.model_provider == "mock" else "configured",
                      "quality_verified": False},
            "voice": {"transcription": "unavailable", "authentication": False},
            "identity_grants_service_access": False}


@router.get("/auth/config")
def public_auth_config(request: Request):
    return auth_config(request.app.state.settings)


@router.get("/auth/csrf")
def recover_browser_csrf(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    if getattr(request.state, "session_kind", None) != "browser":
        raise HTTPException(400, "Native bearer sessions do not use browser CSRF tokens")
    require_origin(request)
    if request.headers.get("origin") is None and request.headers.get("sec-fetch-site") != "same-origin":
        raise HTTPException(403, "CSRF recovery requires an application-origin request")
    if request.headers.get("sec-fetch-site") in {"cross-site", "none"}:
        raise HTTPException(403, "CSRF recovery requires an application-origin request")
    # Called once when restoring a browser cookie, not during snapshot polling.
    # Only a digest persists; other tabs must recover if their old token expires.
    token = secrets.token_urlsafe(32)
    request.state.session_record.csrf_hash = digest(token)
    db.commit()
    return {"csrf_token": token, "rotated": True}


def _cursor(row):
    value = json.dumps([aware(row.created_at).isoformat(), row.id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _cursor_value(value):
    try:
        stamp, identifier = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        parsed = datetime.fromisoformat(stamp)
        if parsed.tzinfo is None or not isinstance(identifier, str) or len(identifier) != 36:
            raise ValueError
        return aware(parsed), identifier
    except (ValueError, TypeError, UnicodeDecodeError):
        raise HTTPException(422, "Invalid conversation cursor") from None


@router.get("/ui/bootstrap")
def bootstrap(request: Request, workspace_id: str | None = None,
              conversation_limit: int = Query(30, ge=1, le=100),
              conversation_cursor: str | None = Query(None, max_length=256),
              user=Depends(get_current_user), db=Depends(get_db)):
    workspaces = list(db.scalars(select(Workspace).where(Workspace.owner_id == user.id)
                                .order_by(Workspace.created_at, Workspace.id).limit(100)))
    workspace = workspace_for(db, user, workspace_id) if workspace_id else (workspaces[0] if workspaces else None)
    result = {"schema_version": 1, "generated_at": now().isoformat(), "user": user_payload(user),
              "workspaces": [public(row, "name", "timezone", "paused", "pause_generation") for row in workspaces],
              "selected_workspace_id": workspace.id if workspace else None,
              "workspace": public(workspace, "name", "timezone", "paused", "pause_generation") if workspace else None,
              "configuration": auth_config(request.app.state.settings),
              "connections": [], "conversations": [], "messages": [], "drafts": [], "actions": [],
              "tasks": [], "memories": [], "styles": [], "grants": [], "routes": [], "jobs": [],
              "contacts": [], "activity": [], "budget": None, "retention": None,
              "pagination": {"conversation_next_cursor": None, "has_more_conversations": False,
                             "resource_limit": 100, "scope": "selected_readable_conversation_page"}}
    result["simulation"] = False
    if workspace is None:
        result["snapshot_version"] = "empty"
        return result
    connectors = list(db.scalars(select(Connector).where(Connector.workspace_id == workspace.id)
                                .order_by(Connector.created_at, Connector.id).limit(100)))
    result["simulation"] = bool(connectors) and all(row.provider == "mock" for row in connectors)
    for connector in connectors:
        value = public(connector, "workspace_id", "provider", "account_id", "owner_sender_id", "status", "capabilities", "fence")
        value.update(simulation=connector.provider == "mock", lease_expires_at=aware(connector.lease_expires_at).isoformat()
                     if connector.lease_expires_at else None,
                     health="healthy" if connector.status == "connected" and connector.lease_expires_at
                     and aware(connector.lease_expires_at) > now() else "needs_attention")
        result["connections"].append(value)
    readable = select(Permission.conversation_id).where(
        Permission.workspace_id == workspace.id, Permission.read.is_(True),
        or_(Permission.expires_at.is_(None), Permission.expires_at > now()))
    query = select(Conversation).where(Conversation.workspace_id == workspace.id, Conversation.id.in_(readable))
    if conversation_cursor:
        stamp, identifier = _cursor_value(conversation_cursor)
        query = query.where(or_(Conversation.created_at > stamp,
                                (Conversation.created_at == stamp) & (Conversation.id > identifier)))
    conversations = list(db.scalars(query.order_by(Conversation.created_at, Conversation.id).limit(conversation_limit + 1)))
    more = len(conversations) > conversation_limit
    conversations = conversations[:conversation_limit]
    ids = [row.id for row in conversations]
    permissions = {row.conversation_id: row for row in db.scalars(select(Permission).where(
        Permission.workspace_id == workspace.id, Permission.conversation_id.in_(ids)))}
    for row in conversations:
        value = public(row, "workspace_id", "connector_id", "provider_chat_id", "title", "kind", "revision",
                       "control_epoch", "control_state", "recipient_opted_in", "recipient_opted_out", "group_send_allowed")
        value["permissions"] = public(permissions[row.id], "read", "retain", "learn", "draft", "send", "share", "version", "expires_at")
        result["conversations"].append(value)
    result["pagination"].update(conversation_next_cursor=_cursor(conversations[-1]) if more else None,
                                 has_more_conversations=more)
    for model, key, render in ((Draft, "drafts", draft_payload), (Memory, "memories", memory_json),
                               (StyleProfile, "styles", profile_json), (AutomationGrant, "grants", grant_public),
                               (OutboundAction, "actions", action_public)):
        resource_query = select(model).where(model.workspace_id == workspace.id, model.conversation_id.in_(ids))
        if model is OutboundAction:
            resource_query = resource_query.where(OutboundAction.destination_conversation_id.in_(readable))
        rows = db.scalars(resource_query.order_by(model.created_at.desc(), model.id).limit(100))
        result[key] = [render(row) for row in rows]
    result["routes"] = [route_public(row) for row in db.scalars(select(ForwardRoute).where(
        ForwardRoute.workspace_id == workspace.id, ForwardRoute.source_conversation_id.in_(ids),
        ForwardRoute.destination_conversation_id.in_(readable)).order_by(ForwardRoute.created_at.desc()).limit(100))]
    for model, key, render in ((Task, "tasks", task_json), (AuthorizedJob, "jobs", job_json)):
        result[key] = [render(row) for row in db.scalars(select(model).where(
            model.workspace_id == workspace.id, or_(model.conversation_id.is_(None), model.conversation_id.in_(ids)))
            .order_by(model.created_at.desc(), model.id).limit(100))]
    # Bound provenance candidates before decrypting or scanning private records.
    # The full address-book endpoint remains separate from this UI preview.
    candidates = list(db.execute(select(LocalContact, ContactSource, Message, Conversation)
        .options(load_only(Message.id, Message.workspace_id, Message.conversation_id, Message.connector_id,
                           Message.deleted, Message.origin, Message.author_kind, Message.direction,
                           Message.sender_id, Message.revision),
                 load_only(Conversation.id, Conversation.workspace_id, Conversation.connector_id))
        .join(ContactSource, ContactSource.contact_id == LocalContact.id)
        .join(Message, Message.id == ContactSource.message_id)
        .join(Conversation, Conversation.id == ContactSource.conversation_id)
        .join(Permission, Permission.conversation_id == Conversation.id)
        .where(LocalContact.workspace_id == workspace.id, ContactSource.workspace_id == workspace.id,
               Message.workspace_id == workspace.id, Conversation.workspace_id == workspace.id,
               Message.conversation_id == Conversation.id, Message.connector_id == Conversation.connector_id,
               LocalContact.connector_id == Conversation.connector_id,
               Permission.workspace_id == workspace.id, Permission.read.is_(True), Permission.retain.is_(True),
               or_(Permission.expires_at.is_(None), Permission.expires_at > now()), Message.deleted.is_(False),
               Message.revision == ContactSource.source_revision)
        .order_by(LocalContact.created_at.desc(), LocalContact.id).limit(200)))
    # Live/backfill identity validation requires metadata and sender identity,
    # never the private message body or unrelated participant attributes. History
    # records still load fields demanded by their canonical native validation.
    # One bounded batch per provenance dimension replaces up to three queries
    # per candidate. The canonical checker still enforces exact identities,
    # expiry, sender lineage and Forget; history-native validation is unchanged.
    candidate_chats = {conversation.id for _, _, _, conversation in candidates}
    candidate_messages = {source.id for _, _, source, _ in candidates}
    candidate_connectors = {conversation.connector_id for _, _, _, conversation in candidates}
    source_contexts = {row.message_id: row for row in db.scalars(select(MessageContext).options(
        load_only(MessageContext.id, MessageContext.workspace_id, MessageContext.message_id,
                  MessageContext.connector_id, MessageContext.conversation_id, MessageContext.sender_identity,
                  MessageContext.expires_at)).where(
        MessageContext.workspace_id == workspace.id, MessageContext.message_id.in_(candidate_messages)))} if candidates else {}
    source_connectors = {row.id: row for row in db.scalars(select(Connector).where(
        Connector.workspace_id == workspace.id, Connector.id.in_(candidate_connectors)))} if candidates else {}
    suppressed = {identifier: set() for identifier in candidate_chats}
    if candidates:
        # Stream metadata and retain only the <=200 candidate source IDs. No
        # record limit is allowed to silently omit an older Forget tombstone.
        for conversation_id, source_ids in db.execute(select(Suppression.conversation_id, Suppression.source_message_ids)
                .where(Suppression.workspace_id == workspace.id, Suppression.conversation_id.in_(candidate_chats))
                .execution_options(yield_per=100)):
            suppressed[conversation_id].update(candidate_messages.intersection(source_ids))
    included = set()
    for contact, ref, source, conversation in candidates:
        scope = PrefetchedContactScope(workspace.id, conversation.id, source_connectors.get(conversation.connector_id),
                                      source_contexts, frozenset(suppressed[conversation.id]))
        if contact.id not in included and source_valid(db, conversation, source, prefetched=scope):
            result["contacts"].append(contact_json(contact))
            included.add(contact.id)
            if len(included) == 100:
                break
    result["activity"] = [public(row, "action", "resource_id", "details", "created_at") for row in db.scalars(
        select(AuditEvent).where(AuditEvent.workspace_id == workspace.id).order_by(AuditEvent.created_at.desc()).limit(50))]
    result["budget"] = get_budget(workspace.id, user=user, db=db)
    policy = db.scalar(select(RetentionPolicy).where(RetentionPolicy.workspace_id == workspace.id))
    result["retention"] = policy_view(policy) if policy else {"workspace_id": workspace.id, "raw_days": 30,
        "derived_days": 90, "audit_days": 90, "version": 0, "backup_status": "not_verified"}
    stable = {key: value for key, value in result.items() if key not in {"generated_at", "configuration"}}
    result["snapshot_version"] = hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()
    return result


@router.get("/ui/resolve")
def resolve_ui_reference(kind: Literal["conversation", "action", "draft", "memory", "connection", "job", "task"],
                         id: str = Query(min_length=1, max_length=36),
                         user=Depends(get_current_user), db=Depends(get_db)):
    """Resolve an authenticated deep link without accepting send authority."""
    if kind == "conversation":
        conversation = conversation_for(db, user, id)
        permission = permission_for(db, conversation, "read")
        value = public(conversation, "workspace_id", "connector_id", "provider_chat_id", "title", "kind", "revision",
                       "control_epoch", "control_state", "recipient_opted_in", "recipient_opted_out", "group_send_allowed")
        value["permissions"] = public(permission, "read", "retain", "learn", "draft", "send", "share", "version", "expires_at")
        return {"kind": kind, "object": value, "conversation": value}
    if kind == "action":
        from .actions import _read_action
        row = _read_action(db, user, id)
        return {"kind": kind, "object": action_public(row), "conversation_id": row.conversation_id}
    if kind == "draft":
        from .core import get_draft
        value = get_draft(id, user=user, db=db)
        return {"kind": kind, "object": value, "conversation_id": value["conversation_id"]}
    if kind == "connection":
        from .core import connector_for
        row = connector_for(db, user, id)
        return {"kind": kind, "object": public(row, "workspace_id", "provider", "account_id", "owner_sender_id", "status", "capabilities", "fence")}
    model, render = {"memory": (Memory, memory_json), "job": (AuthorizedJob, job_json), "task": (Task, task_json)}[kind]
    row = db.get(model, id)
    if row is None:
        raise HTTPException(404, "Reference not found")
    workspace_for(db, user, row.workspace_id)
    if row.conversation_id:
        conversation = conversation_for(db, user, row.conversation_id)
        if conversation.workspace_id != row.workspace_id:
            raise HTTPException(404, "Reference not found")
        permission_for(db, conversation, "read")
    return {"kind": kind, "object": render(row), "conversation_id": row.conversation_id}


class ResolveTimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_datetime: datetime
    timezone: str = Field(min_length=1, max_length=80)
    ambiguity_policy: Literal["reject", "earlier", "later"] = "reject"
    gap_policy: Literal["reject"] = "reject"

    @field_validator("local_datetime", mode="before")
    @classmethod
    def wall_clock(cls, value):
        if not isinstance(value, str):
            raise ValueError("A local ISO wall clock is required")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("A local ISO wall clock is required") from None
        if parsed.tzinfo is not None:
            raise ValueError("Use a local wall clock without an offset plus an IANA timezone")
        return parsed


@router.post("/schedules/resolve-time")
def resolve_schedule_time(body: ResolveTimeInput, user=Depends(get_current_user)):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        instant = resolve_local(body.local_datetime, body.timezone, body.ambiguity_policy, body.gap_policy)
        local = instant.astimezone(ZoneInfo(body.timezone))
    except ZoneInfoNotFoundError:
        raise HTTPException(422, {"reason_code": "INVALID_TIMEZONE"}) from None
    except ValueError as error:
        raise HTTPException(422, {"reason_code": str(error)}) from None
    return {"due_at": instant.isoformat(), "local_datetime": body.local_datetime.isoformat(),
            "timezone": body.timezone, "utc_offset": local.strftime("%z"),
            "ambiguity_policy": body.ambiguity_policy, "gap_policy": body.gap_policy}


class OwnerDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4096)
    expected_revision: int | None = Field(default=None, ge=0)
    expected_control_epoch: int | None = Field(default=None, ge=0)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Draft text must not be blank")
        return value.strip()


@router.post("/conversations/{conversation_id}/owner-drafts", status_code=201)
@serialized_control
def owner_draft(conversation_id: str, body: OwnerDraftInput,
                idempotency_key: str | None = Header(None, min_length=8, max_length=120),
                user=Depends(get_current_user), db=Depends(get_db)):
    from uuid import NAMESPACE_URL, uuid5
    conversation = conversation_for(db, user, conversation_id)
    permission = permission_for(db, conversation, "draft")
    for capability in ("read", "retain"):
        permission_for(db, conversation, capability)
    workspace = workspace_for(db, user, conversation.workspace_id)
    connector = db.get(Connector, conversation.connector_id)
    if connector is None or connector.workspace_id != workspace.id:
        raise HTTPException(409, "Conversation account mapping changed")
    if (body.expected_revision is not None and body.expected_revision != conversation.revision
            or body.expected_control_epoch is not None and body.expected_control_epoch != conversation.control_epoch):
        raise HTTPException(409, "Conversation context changed")
    row_id = str(uuid5(NAMESPACE_URL, f"{workspace.id}:owner-draft:{idempotency_key}")) if idempotency_key else None
    existing = db.get(Draft, row_id) if row_id else None
    if existing is not None:
        if existing.conversation_id != conversation.id or existing.content_hash != content_hash(body.text):
            raise HTTPException(409, "Idempotency key already used for another draft")
        return draft_payload(existing)
    row = Draft(workspace_id=workspace.id, conversation_id=conversation.id, recipient_id=conversation.provider_chat_id,
                text=body.text, evidence_message_ids=[], missing_facts=[], model_version="owner-authored-v1",
                profile_version=0, conversation_revision=conversation.revision, control_epoch=conversation.control_epoch,
                permission_version=permission.version, pause_generation=workspace.pause_generation,
                connector_fence=connector.fence, content_hash=content_hash(body.text), status="needs_approval")
    if row_id:
        row.id = row_id
    db.add(row)
    db.flush()
    audit(db, workspace.id, user.id, "draft.owner_authored", row.id)
    db.commit()
    return draft_payload(row)
