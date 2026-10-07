from datetime import datetime, timedelta
from hashlib import sha256
from functools import wraps
import inspect
import json
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import LargeBinary, cast, delete, func, or_, select, true, union_all, update
from sqlalchemy.exc import IntegrityError

from .access import audit, conversation_for, permission_for, workspace_for
from .auth import get_current_user
from .db import EncryptedText, aware, get_db, now
from .models import (AuditEvent, Connector, Conversation, Draft, ImportRecord, Memory, Message,
                     MessageEvent, Outbox, Permission, StyleProfile,
                     Suppression, Task, Workspace)

router = APIRouter()
MAX_PRIVATE_OPERATION_CONVERSATIONS = 200
MAX_PRIVATE_OPERATION_ROWS = 20000
MAX_PRIVATE_OPERATION_CIPHERTEXT_BYTES = 32 * 1024 * 1024


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceInput(Payload):
    name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=80)


class ConnectorInput(Payload):
    workspace_id: str
    provider: Literal["export_only", "mock", "whatsapp_cloud"] = "export_only"
    account_id: str = Field(min_length=1, max_length=120)
    owner_sender_id: str = Field(min_length=1, max_length=120)


class ConversationInput(Payload):
    connector_id: str
    provider_chat_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=160)
    kind: Literal["contact", "group"] = "contact"
    recipient_opted_in: bool = False
    group_send_allowed: bool = False


class PermissionInput(Payload):
    read: bool = False
    retain: bool = False
    learn: bool = False
    draft: bool = False
    send: bool = False
    share: bool = False
    expires_at: datetime | None = None


class ImportInput(Payload):
    conversation_id: str
    text: str = Field(min_length=1, max_length=2_000_000)
    owner_sender_label: str = Field(min_length=1, max_length=160)
    date_order: Literal["DMY", "MDY", "YMD"]
    timezone: str


def public(row, *fields):
    return {key: getattr(row, key) for key in ("id", *fields)}


def invalidate(db, conversation, reason):
    # SQL is authoritative; pending references never authorize an action after this revision bump.
    from .messaging import invalidate_conversation
    conversation.revision += 1
    conversation.control_epoch += 1
    invalidate_conversation(db, conversation, reason)


def serialized_control(function):
    """Order SQL controls with claims; resource mapping and permission remain SQL-owned."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        from .messaging import submit_guard
        values = inspect.signature(function).bind(*args, **kwargs).arguments
        db = values["db"]
        workspace_id = values.get("workspace_id")
        body = values.get("body")
        if workspace_id is None and body is not None:
            workspace_id = getattr(body, "workspace_id", None)
        for key, model in (("conversation_id", Conversation), ("connector_id", Connector),
                           ("memory_id", Memory), ("task_id", Task)):
            resource_id = values.get(key) or (getattr(body, key, None) if body else None)
            if resource_id and not workspace_id:
                resource = db.get(model, resource_id)
                workspace_id = resource.workspace_id if resource else None
        if not workspace_id:
            return function(*args, **kwargs)
        with submit_guard(workspace_id):
            from .storage_authority import lock_workspace
            lock_workspace(db, workspace_id)
            db.expire_all()
            return function(*args, **kwargs)
    return wrapped


@router.get("/workspaces")
def list_workspaces(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0, le=10000),
                    user=Depends(get_current_user), db=Depends(get_db)):
    return [public(w, "name", "timezone", "paused", "pause_generation")
            for w in db.scalars(select(Workspace).where(Workspace.owner_id == user.id)
                                .order_by(Workspace.created_at, Workspace.id).offset(offset).limit(limit))]


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceInput, user=Depends(get_current_user), db=Depends(get_db),
                     idempotency_key: str | None = Header(default=None)):
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Unknown timezone")
    key_hash = None
    body_hash = None
    if idempotency_key is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", idempotency_key):
            raise HTTPException(422, "Use an 8–128 character workspace creation key")
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        body_hash = sha256(json.dumps(body.model_dump(), sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

    def previous_creation():
        if key_hash is None:
            return None
        previous = db.scalar(select(Workspace).where(
            Workspace.owner_id == user.id, Workspace.creation_key_hash == key_hash))
        if previous and previous.creation_payload_hash != body_hash:
            raise HTTPException(409, "This creation key belongs to a different workspace request")
        return previous

    row = previous_creation()
    if row is not None:
        return public(row, "name", "timezone", "paused", "pause_generation")
    row = Workspace(owner_id=user.id, creation_key_hash=key_hash,
                    creation_payload_hash=body_hash, **body.model_dump())
    db.add(row)
    try:
        db.flush()
        audit(db, row.id, user.id, "workspace.created", row.id)
        db.commit()
    except IntegrityError:
        # The unique owner/key constraint is the cross-process arbiter. A lost
        # response or simultaneous retry returns the committed workspace once.
        db.rollback()
        row = previous_creation()
        if row is None:
            raise
    return public(row, "name", "timezone", "paused", "pause_generation")


def capabilities(provider):
    names = ["history_sync", "live_receive", "send_text", "group_read", "group_send",
             "owner_self_chat", "pairing_code", "message_edits", "deletions", "qr_pairing",
             "quoted_reply", "emoji_reaction", "native_forward", "delivery_receipts", "read_receipts",
             "phone_continuity", "human_outgoing", "human_reaction", "assistant_echo",
             "participant_identity", "message_expiry", "native_records", "contact_save_local",
             "contact_write_whatsapp", "contact_write_google", "contact_write_os"]
    values = {n: "unsupported" for n in names}
    values["contact_save_local"] = "supported"
    if provider == "mock":
        unavailable = {"pairing_code", "qr_pairing", "owner_self_chat", "phone_continuity",
                       "contact_write_whatsapp", "contact_write_google", "contact_write_os"}
        values.update({n: "supported" for n in names if n not in unavailable})
    elif provider == "whatsapp_personal":
        values.update(qr_pairing="unknown", live_receive="unknown", send_text="unknown",
                      human_outgoing="unknown", assistant_echo="unknown", delivery_receipts="unknown")
    elif provider == "whatsapp_cloud":
        values.update(live_receive="unknown", send_text="unknown", group_read="unknown", group_send="unknown")
    return values


@router.post("/connectors", status_code=201)
@serialized_control
def create_connector(body: ConnectorInput, request: Request,
                     user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, body.workspace_id)
    settings = request.app.state.settings
    if body.provider == "mock" and settings.environment == "production":
        raise HTTPException(403, "Mock connectors are unavailable in production")
    if body.provider == "whatsapp_cloud":
        from .provider_authority import require_whatsapp_owner
        require_whatsapp_owner(settings, user)
        if body.account_id != settings.whatsapp_phone_number_id:
            raise HTTPException(409, "Configure and verify this Business phone-number ID before connecting")
        status = "needs_verification"
    else:
        status = "connected"
    existing = db.scalar(select(Connector).where(Connector.provider == body.provider,
                                                Connector.account_id == body.account_id))
    if existing:
        if existing.workspace_id != body.workspace_id:
            raise HTTPException(409, "This account is already registered; explicit owner transfer is required")
        return public(existing, "workspace_id", "provider", "account_id", "status", "capabilities", "fence")
    row = Connector(**body.model_dump(), status=status, capabilities=capabilities(body.provider),
                    lease_expires_at=now() + timedelta(hours=1) if body.provider == "mock" else None)
    db.add(row)
    db.flush()
    audit(db, row.workspace_id, user.id, "connector.created", row.id, provider=row.provider)
    db.commit()
    return public(row, "workspace_id", "provider", "account_id", "status", "capabilities", "fence")


def connector_for(db, user, connector_id):
    row = db.get(Connector, connector_id)
    if row is None:
        raise HTTPException(404, "Connector not found")
    workspace_for(db, user, row.workspace_id)
    return row


@router.get("/connectors/{connector_id}")
def get_connector(connector_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = connector_for(db, user, connector_id)
    return public(row, "workspace_id", "provider", "account_id", "status", "capabilities", "fence")


@router.get("/connectors/{connector_id}/capabilities")
def connector_capability_evidence(connector_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = connector_for(db, user, connector_id)
    defaults = capabilities(row.provider)
    entries = []
    for name, fallback in defaults.items():
        status = row.capabilities.get(name, fallback)
        if isinstance(status, dict):
            status = status.get("status", "unknown")
        mock = row.provider == "mock"
        local = name == "contact_save_local"
        tested = (mock or local) and status == "supported"
        entries.append({"name": name, "status": status,
                        "evidence_kind": "synthetic" if mock and tested else "local" if local else "unverified",
                        "evidence_date": "2026-10-06" if tested else None,
                        "test_reference": "tests/test_actions.py" if mock and tested else
                                          "tests/test_people.py" if local else None,
                        "reason": "Simulation; no provider or device test" if mock else
                                  "Assistant-local database only" if local else
                                  "Provider/device eligibility and operation evidence pending"})
    return {"connector_id": row.id, "account_id": row.account_id, "provider": row.provider,
            "adapter_version": "mock-actions-v1" if row.provider == "mock" else
                               "meta-graph-configured" if row.provider == "whatsapp_cloud" else "text-export-v1",
            "simulation": row.provider == "mock", "capabilities": entries}


@router.get("/connectors")
def list_connectors(workspace_id: str, limit: int = Query(100, ge=1, le=200),
                    offset: int = Query(0, ge=0, le=10000), user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    return [public(row, "workspace_id", "provider", "account_id", "status", "capabilities", "fence")
            for row in db.scalars(select(Connector).where(Connector.workspace_id == workspace_id)
                                  .order_by(Connector.created_at, Connector.id).offset(offset).limit(limit))]


@router.post("/connectors/{connector_id}/verify")
@serialized_control
def verify_connector(connector_id: str, request: Request,
                     user=Depends(get_current_user), db=Depends(get_db)):
    import httpx
    row = connector_for(db, user, connector_id)
    s = request.app.state.settings
    if row.provider != "whatsapp_cloud" or not s.whatsapp_access_token:
        raise HTTPException(409, "Only a configured WhatsApp Business number can be verified")
    from .provider_authority import require_whatsapp_owner
    require_whatsapp_owner(s, user)
    if not s.whatsapp_phone_number_id or row.account_id != s.whatsapp_phone_number_id:
        raise HTTPException(409, "Configure this Business phone-number ID before verifying")
    try:
        from .whatsapp_provider import graph_endpoint, normalize_phone
        result = httpx.get(graph_endpoint(s.whatsapp_api_version, row.account_id),
                           headers={"Authorization": f"Bearer {s.whatsapp_access_token}"},
                           params={"fields": "id,display_phone_number,verified_name,is_on_biz_app,platform_type"},
                           timeout=15, follow_redirects=False, trust_env=False)
        result.raise_for_status()
        identity = result.json()
        if not isinstance(identity, dict) or identity.get("id") != row.account_id:
            raise ValueError("Identity mismatch")
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Business identity verification failed; inspect credentials and account access")
    # The verified Graph lookup binds the device sender number. A caller-supplied
    # owner label never proves the authorship of Business app history/echoes.
    phone = None
    candidate_phone = identity.get("display_phone_number")
    if isinstance(candidate_phone, str):
        try:
            phone = normalize_phone(candidate_phone)
            row.owner_sender_id = phone
        except ValueError:
            pass
    coexistence = bool(phone and identity.get("is_on_biz_app") is True
                       and identity.get("platform_type") == "CLOUD_API")
    reconnect_review = (row.status == "disconnected" or (row.status == "connected" and
                        (row.lease_expires_at is None or aware(row.lease_expires_at) <= now())))
    if reconnect_review:
        row.fence += 1
        for conversation in db.scalars(select(Conversation).where(
                Conversation.connector_id == row.id, Conversation.workspace_id == row.workspace_id)):
            invalidate(db, conversation, "business_verification_gap")
            conversation.control_state = "RECONNECT_REVIEW"
    row.status = "connected"
    row.capabilities = {**row.capabilities, "live_receive": "supported", "send_text": "supported",
                        "business_app_coexistence": "supported" if coexistence else "unknown",
                        "history_sync": "supported" if coexistence else "unsupported",
                        "human_outgoing": "supported" if coexistence else "unsupported",
                        "identity_verification": {"status": "verified", "checked_at": now().isoformat(),
                                                  "live_delivery_verified": False}}
    row.lease_expires_at = now() + timedelta(hours=1)
    audit(db, row.workspace_id, user.id, "connector.verified", row.id)
    db.commit()
    return {**public(row, "status", "account_id", "capabilities", "fence"),
            "coexistence": coexistence, "live_delivery_verified": False,
            "lease_expires_at": row.lease_expires_at, "reconnect_review_required": reconnect_review}


@router.delete("/connectors/{connector_id}")
@serialized_control
def disconnect_connector(connector_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = connector_for(db, user, connector_id)
    row.status = "disconnected"
    row.fence += 1
    row.lease_expires_at = now()
    for conv in db.scalars(select(Conversation).where(Conversation.connector_id == row.id,
                                                     Conversation.workspace_id == row.workspace_id)):
        invalidate(db, conv, "disconnect")
        conv.control_state = "RECONNECT_REVIEW"
    from .whatsapp_personal import erase_session_credentials
    erase_session_credentials(db, row)
    audit(db, row.workspace_id, user.id, "connector.disconnected", row.id)
    db.commit()
    return {"status": "disconnected", "provider_revocation": "not_requested", "fence": row.fence}


@router.post("/conversations", status_code=201)
@serialized_control
def create_conversation(body: ConversationInput, user=Depends(get_current_user), db=Depends(get_db)):
    connector = connector_for(db, user, body.connector_id)
    if connector.provider == "whatsapp_personal":
        raise HTTPException(403, "Authorize personal WhatsApp contacts through the connected session")
    existing = db.scalar(select(Conversation).where(Conversation.connector_id == connector.id,
                                                   Conversation.provider_chat_id == body.provider_chat_id))
    if existing:
        return public(existing, "workspace_id", "connector_id", "title", "kind", "revision", "control_state")
    row = Conversation(workspace_id=connector.workspace_id, **body.model_dump(),
                       control_state="READ_ONLY" if body.kind == "group" else "DRAFT_MODE")
    db.add(row)
    db.flush()
    db.add(Permission(workspace_id=row.workspace_id, conversation_id=row.id))
    audit(db, row.workspace_id, user.id, "conversation.created", row.id)
    db.commit()
    return public(row, "workspace_id", "connector_id", "title", "kind", "revision", "control_state")


@router.get("/conversations")
def list_conversations(workspace_id: str, limit: int = Query(100, ge=1, le=200),
                       offset: int = Query(0, ge=0, le=10000), user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    return [public(c, "workspace_id", "connector_id", "title", "kind", "revision", "control_state")
            for c in db.scalars(select(Conversation).where(Conversation.workspace_id == workspace_id)
                                .order_by(Conversation.created_at, Conversation.id).offset(offset).limit(limit))]


@router.put("/conversations/{conversation_id}/permissions")
@serialized_control
def set_permissions(conversation_id: str, body: PermissionInput,
                    user=Depends(get_current_user), db=Depends(get_db)):
    row = conversation_for(db, user, conversation_id)
    grant = db.scalar(select(Permission).where(Permission.conversation_id == row.id))
    if body.expires_at and aware(body.expires_at) <= now():
        raise HTTPException(422, "Permission expiry must be in the future")
    for key, value in body.model_dump().items():
        setattr(grant, key, value)
    grant.version += 1
    invalidate(db, row, "permission_changed")
    if row.control_state not in {"HUMAN_TAKEOVER", "RECONNECT_REVIEW"}:
        row.control_state = "DRAFT_MODE" if body.draft else ("READ_ONLY" if body.read else "AI_OFF")
    if not body.learn:
        db.execute(delete(StyleProfile).where(StyleProfile.conversation_id == row.id,
                                             StyleProfile.workspace_id == row.workspace_id))
    if body.learn:
        from .intelligence import refresh_style_from_messages
        refresh_style_from_messages(db, row)
    audit(db, row.workspace_id, user.id, "permission.changed", row.id, version=grant.version)
    db.commit()
    return public(grant, "read", "retain", "learn", "draft", "send", "share", "version", "expires_at")


@router.get("/conversations/{conversation_id}/permissions")
def get_permissions(conversation_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = conversation_for(db, user, conversation_id)
    grant = db.scalar(select(Permission).where(Permission.conversation_id == row.id,
                                               Permission.workspace_id == row.workspace_id))
    return public(grant, "read", "retain", "learn", "draft", "send", "share", "version", "expires_at")


@router.get("/drafts")
def list_drafts(workspace_id: str, conversation_id: str | None = None, limit: int = 50,
                user=Depends(get_current_user), db=Depends(get_db)):
    from .messaging import draft_payload
    workspace_for(db, user, workspace_id)
    if not 1 <= limit <= 200:
        raise HTTPException(422, "Limit must be between 1 and 200")
    readable = select(Permission.conversation_id).where(Permission.workspace_id == workspace_id,
                       Permission.read.is_(True), Permission.expires_at.is_(None) | (Permission.expires_at > now()))
    query = select(Draft).where(Draft.workspace_id == workspace_id, Draft.conversation_id.in_(readable))
    if conversation_id:
        conversation = conversation_for(db, user, conversation_id)
        if conversation.workspace_id != workspace_id:
            raise HTTPException(404, "Conversation not found in workspace")
        permission_for(db, conversation, "read")
        query = query.where(Draft.conversation_id == conversation_id)
    return [draft_payload(row) for row in db.scalars(query.order_by(Draft.created_at.desc(), Draft.id).limit(limit))]


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    from .messaging import draft_payload
    row = db.get(Draft, draft_id)
    if row is None:
        raise HTTPException(404, "Draft not found")
    conversation = conversation_for(db, user, row.conversation_id)
    if row.workspace_id != conversation.workspace_id:
        raise HTTPException(404, "Draft not found")
    permission_for(db, conversation, "read")
    return draft_payload(row)


@router.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: str, limit: int = 50, before: datetime | None = None,
                  before_id: str | None = Query(default=None, min_length=1, max_length=36),
                  derived_evidence: bool = False,
                  user=Depends(get_current_user), db=Depends(get_db)):
    row = conversation_for(db, user, conversation_id)
    permission_for(db, row, "read")
    if not 1 <= limit <= 200:
        raise HTTPException(422, "Limit must be between 1 and 200")
    query = select(Message).where(Message.workspace_id == row.workspace_id,
                                  Message.conversation_id == row.id, Message.deleted.is_(False))
    if derived_evidence:
        # Filter suppressed references in SQL before applying the page limit.
        # Neither a large history nor the full suppression list is materialized
        # for an evidence picker. Raw history retains its separate read contract.
        if db.get_bind().dialect.name == "postgresql":
            refs = func.json_array_elements_text(Suppression.source_message_ids).table_valued("value").render_derived()
        else:
            refs = func.json_each(Suppression.source_message_ids).table_valued("value")
        suppressed = select(Suppression.id).select_from(Suppression).join(refs, true()).where(
            Suppression.workspace_id == row.workspace_id, Suppression.conversation_id == row.id,
            refs.c.value == Message.id).correlate(Message).exists()
        query = query.where(~suppressed)
    if before_id:
        if before is None:
            raise HTTPException(422, "A message cursor requires both timestamp and ID")
        # Look up only the cursor timestamp inside the already authorized chat.
        # An arbitrary foreign ID cannot load or reveal private message content.
        cursor_stamp = db.scalar(select(Message.provider_timestamp).where(
            Message.workspace_id == row.workspace_id, Message.conversation_id == row.id,
            Message.id == before_id, Message.deleted.is_(False)))
        if cursor_stamp is None:
            raise HTTPException(404, "Message cursor is unavailable")
        if aware(cursor_stamp) != aware(before):
            raise HTTPException(422, "Message cursor timestamp does not match")
        query = query.where(or_(Message.provider_timestamp < before,
                                (Message.provider_timestamp == before) & (Message.id < before_id)))
    elif before:
        query = query.where(Message.provider_timestamp < before)
    messages = list(db.scalars(query.order_by(Message.provider_timestamp.desc(), Message.id.desc()).limit(limit)))
    from .native import message_available
    messages = [message for message in messages if message_available(db, message)]
    return [public(m, "sender_id", "direction", "origin", "author_kind", "text", "provider_timestamp",
                   "revision", "reply_to") for m in reversed(messages)]


def parsed_import(body, settings):
    from .importer import parse_export
    if len(body.text.encode("utf-8")) > settings.max_import_bytes:
        raise HTTPException(413, "Export exceeds configured upload limit")
    try:
        return parse_export(body.text, date_order=body.date_order, timezone=body.timezone,
                            owner_sender_label=body.owner_sender_label, max_records=settings.max_import_records)
    except ValueError as exc:
        # Parser errors are controlled, content-free validation messages.
        raise HTTPException(422, str(exc))


@router.post("/imports/preview")
def preview_import(body: ImportInput, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    conv = conversation_for(db, user, body.conversation_id)
    permission_for(db, conv, "read")
    result = parsed_import(body, request.app.state.settings)
    return {"source_hash": result.source_hash, "senders": result.senders, "record_count": len(result.records),
            "warnings": result.warnings, "oldest": result.oldest, "newest": result.newest,
            "history_completeness": "unknown", "owner_sender_label": body.owner_sender_label}


@router.post("/imports", status_code=201)
@serialized_control
def commit_import(body: ImportInput, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    conv = conversation_for(db, user, body.conversation_id)
    permission_for(db, conv, "read")
    permission_for(db, conv, "retain")
    result = parsed_import(body, request.app.state.settings)
    source_key = sha256((result.source_hash + body.timezone + body.date_order + body.owner_sender_label).encode()).hexdigest()
    existing = db.scalar(select(ImportRecord).where(ImportRecord.conversation_id == conv.id,
                                                   ImportRecord.workspace_id == conv.workspace_id,
                                                   ImportRecord.source_hash == source_key))
    if existing:
        return public(existing, "status", "message_count", "coverage") | {"replayed": True}
    prior = db.scalar(select(ImportRecord).where(ImportRecord.conversation_id == conv.id,
                                                ImportRecord.workspace_id == conv.workspace_id,
                                                ImportRecord.coverage["file_hash"].as_string() == result.source_hash))
    if prior:
        raise HTTPException(409, "This file was imported with another mapping; delete chat data before remapping")
    count = 0
    for record in result.records:
        is_owner = record.sender == body.owner_sender_label and not record.is_system
        provider_id = f"export:{result.source_hash}:{record.ordinal}"
        prior_message = db.scalar(select(Message).where(Message.workspace_id == conv.workspace_id,
                                                         Message.conversation_id == conv.id,
                                                         Message.provider_message_id == provider_id))
        if prior_message is not None:
            # Source-key tombstones remain even after deletion; a repeated file cannot resurrect them.
            if prior_message.deleted:
                raise HTTPException(409, "Deleted source records cannot be restored by reimporting the same file")
            continue
        msg = Message(workspace_id=conv.workspace_id, connector_id=conv.connector_id, conversation_id=conv.id,
                      provider_message_id=provider_id, sender_id=record.sender or "system",
                      direction="outbound" if is_owner else "inbound", origin="history",
                      author_kind="system" if record.is_system else ("human_owner" if is_owner else "contact"),
                      text=record.text, provider_timestamp=record.timestamp,
                      excluded_from_learning=record.excluded_from_learning)
        db.add(msg)
        db.flush()
        db.add(MessageEvent(workspace_id=conv.workspace_id, event_id=f"{conv.id}:{provider_id}",
                            conversation_id=conv.id, message_id=msg.id, event_type="message.created", source_revision=1))
        count += 1
    invalidate(db, conv, "history_imported")
    from .intelligence import refresh_style_from_messages
    refresh_style_from_messages(db, conv)
    row = ImportRecord(workspace_id=conv.workspace_id, conversation_id=conv.id, source_hash=source_key,
                       owner_sender_label=body.owner_sender_label, timezone=body.timezone,
                       date_order=body.date_order, message_count=count,
                       coverage={"file_hash": result.source_hash, "source": "owner_export", "oldest": result.oldest,
                                 "newest": result.newest, "history_completeness": "unknown", "warnings": result.warnings,
                                 "received_records": count, "missing_media": sum(r.is_media for r in result.records)})
    db.add(row)
    db.flush()
    db.add(Outbox(workspace_id=conv.workspace_id, kind="history.imported", aggregate_id=row.id,
                  payload={"import_id": row.id, "conversation_id": conv.id}))
    audit(db, conv.workspace_id, user.id, "history.imported", row.id, record_count=count)
    db.commit()
    return public(row, "status", "message_count", "coverage") | {"replayed": False}


@router.get("/imports/{import_id}")
def get_import(import_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.get(ImportRecord, import_id)
    if row is None:
        raise HTTPException(404, "Import not found")
    conv = conversation_for(db, user, row.conversation_id)
    permission_for(db, conv, "read")
    return public(row, "status", "message_count", "coverage")


@router.post("/pause-all")
@serialized_control
def pause_all(workspace_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = workspace_for(db, user, workspace_id)
    row.paused = True
    row.pause_generation += 1
    from .messaging import hold_workspace_schedules
    hold_workspace_schedules(db, row)
    from .actions import hold_workspace_actions
    hold_workspace_actions(db, row)
    # Pause has its own generation fence. It does not erase a durable owner
    # schedule or the conversation context that must be rechecked on resume.
    from .jobs_models import AuthorizedJob
    db.execute(update(AuthorizedJob).where(AuthorizedJob.workspace_id == row.id,
               AuthorizedJob.action_kind != "REMINDER", AuthorizedJob.status.in_(["scheduled", "held"]),
               or_(AuthorizedJob.hold_reason.is_(None), AuthorizedJob.hold_reason != "OWNER_HOLD"))
               .values(status="held", hold_reason="GLOBAL_PAUSE").execution_options(synchronize_session=False))
    audit(db, row.id, user.id, "workspace.paused", row.id)
    db.commit()
    return {"paused": row.paused, "pause_generation": row.pause_generation}


@router.post("/resume-all")
@serialized_control
def resume_all(workspace_id: str, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    row = workspace_for(db, user, workspace_id)
    row.paused = False
    row.pause_generation += 1
    from .messaging import resume_workspace_schedules
    resume_workspace_schedules(db, row, request.app.state.settings)
    from .actions import resume_workspace_actions
    resume_workspace_actions(db, row, request.app.state.settings)
    audit(db, row.id, user.id, "workspace.resumed", row.id)
    db.commit()
    return {"paused": row.paused, "pause_generation": row.pause_generation,
            "note": "Durable schedules resume after current checks; expired or cancelled actions remain terminal; human takeover requires per-chat resume"}


@router.post("/conversations/{conversation_id}/takeover")
@serialized_control
def takeover(conversation_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = conversation_for(db, user, conversation_id)
    invalidate(db, row, "human_takeover")
    row.control_state = "HUMAN_TAKEOVER"
    audit(db, row.workspace_id, user.id, "conversation.takeover", row.id)
    db.commit()
    return public(row, "control_state", "control_epoch", "revision")


@router.post("/conversations/{conversation_id}/resume")
@serialized_control
def resume(conversation_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = conversation_for(db, user, conversation_id)
    permission_for(db, row, "draft")
    connector = db.get(Connector, row.connector_id)
    if connector.status != "connected":
        raise HTTPException(409, "Reconnect and verify account before resuming")
    invalidate(db, row, "explicit_resume")
    row.control_state = "DRAFT_MODE"
    audit(db, row.workspace_id, user.id, "conversation.resumed", row.id)
    db.commit()
    return public(row, "control_state", "control_epoch", "revision")


@router.get("/activity")
def activity(workspace_id: str, limit: int = 100, user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    if not 1 <= limit <= 200:
        raise HTTPException(422, "Limit must be between 1 and 200")
    return [public(e, "actor_id", "action", "resource_id", "details", "created_at") for e in
            db.scalars(select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
                       .order_by(AuditEvent.created_at.desc()).limit(limit))]


def _admit_private_operation(db, workspace_id, *, operation, conversation_id=None):
    """Count bounded ID projections before loading or changing private material.

    The caller checks ownership first. These are synchronous request limits;
    larger exports and erasures need a separate durable batching workflow.
    """
    from .action_models import AutomationGrant, ForwardRoute, OutboundAction, SubmissionAttempt
    from .automatic_drafts_models import AutoDraftGrant, AutomaticDraftJob
    from .jobs_models import AuthorizedJob, JobRun
    from .models import Automation, ScheduledIntent, SendAttempt
    from .native_models import MessageContext, NativeRecord, ReactionExample
    from .people_models import ContactSaveGrant, ContactSource, LocalContact, UsageLedger, WorkspaceBudget

    conversations = select(Conversation.id).where(Conversation.workspace_id == workspace_id)
    if conversation_id is not None:
        conversations = conversations.where(Conversation.id == conversation_id)
    elif operation == "export":
        readable = select(Permission.conversation_id).where(
            Permission.workspace_id == workspace_id, Permission.read.is_(True),
            or_(Permission.expires_at.is_(None), Permission.expires_at > now()))
        conversations = conversations.where(Conversation.id.in_(readable))
    conversation_count = db.scalar(select(func.count()).select_from(
        conversations.limit(MAX_PRIVATE_OPERATION_CONVERSATIONS + 1).subquery()))
    if conversation_count > MAX_PRIVATE_OPERATION_CONVERSATIONS:
        raise HTTPException(413, {"code": "PRIVATE_ROW_LIMIT_EXCEEDED", "operation": operation,
                                  "limit": MAX_PRIVATE_OPERATION_CONVERSATIONS, "resource": "conversations"})

    counts, payload_sizes = [], []

    def count_rows(model, *conditions):
        ids = select(model.id).where(model.workspace_id == workspace_id, *conditions)
        counts.append(select(func.count().label("row_count")).select_from(
            ids.limit(MAX_PRIVATE_OPERATION_ROWS + 1).subquery()))
        encrypted = [column for column in model.__table__.columns if isinstance(column.type, EncryptedText)]
        if encrypted:
            # Database byte lengths avoid decrypting any private row. SQLite
            # length(TEXT) counts Unicode characters, so measure a BLOB there.
            byte_length = (lambda column: func.octet_length(column)) if db.get_bind().dialect.name == "postgresql" else (
                lambda column: func.length(cast(column, LargeBinary)))
            sizes = [func.coalesce(byte_length(column), 0) for column in encrypted]
            projected = select(sum(sizes).label("payload_bytes")).where(model.workspace_id == workspace_id, *conditions)
            payload_sizes.append(select(func.coalesce(func.sum(projected.subquery().c.payload_bytes), 0).label("payload_bytes")))

    for model in (Permission, Message, MessageEvent, ImportRecord, StyleProfile, Automation, Memory, Task,
                  Suppression, Draft, ScheduledIntent, AutomationGrant, MessageContext, NativeRecord,
                  ReactionExample, ContactSource, ContactSaveGrant, AutoDraftGrant, AutomaticDraftJob):
        if model is Task and operation == "purge_workspace":
            count_rows(model)
        else:
            count_rows(model, model.conversation_id.in_(conversations))
    action_scope = OutboundAction.conversation_id.in_(conversations)
    if operation != "export":
        action_scope = or_(action_scope, OutboundAction.destination_conversation_id.in_(conversations))
    count_rows(OutboundAction, action_scope)
    action_ids = select(OutboundAction.id).where(OutboundAction.workspace_id == workspace_id, action_scope)
    count_rows(SubmissionAttempt, SubmissionAttempt.action_id.in_(action_ids))
    draft_ids = select(Draft.id).where(Draft.workspace_id == workspace_id, Draft.conversation_id.in_(conversations))
    count_rows(SendAttempt, SendAttempt.draft_id.in_(draft_ids))
    count_rows(ForwardRoute, or_(ForwardRoute.source_conversation_id.in_(conversations),
                                ForwardRoute.destination_conversation_id.in_(conversations)))
    job_scope = AuthorizedJob.conversation_id.in_(conversations)
    if conversation_id is None:
        job_scope = or_(job_scope, AuthorizedJob.conversation_id.is_(None))
    count_rows(AuthorizedJob, job_scope)
    job_ids = select(AuthorizedJob.id).where(AuthorizedJob.workspace_id == workspace_id, job_scope)
    count_rows(JobRun, JobRun.job_id.in_(job_ids))
    if conversation_id is None and operation == "purge_workspace":
        for model in (Connector, LocalContact, UsageLedger, WorkspaceBudget):
            count_rows(model)
    else:
        contact_ids = select(ContactSource.contact_id).where(
            ContactSource.workspace_id == workspace_id, ContactSource.conversation_id.in_(conversations))
        count_rows(LocalContact, LocalContact.id.in_(contact_ids))
    if operation != "export":
        # Per-chat erasure currently inspects every pending workspace outbox row.
        # Admit the actual scan, including pending records for other conversations.
        count_rows(Outbox, *([Outbox.status == "pending"] if conversation_id is not None else []))
    total = conversation_count + db.scalar(select(func.coalesce(func.sum(union_all(*counts).subquery().c.row_count), 0)))
    if total > MAX_PRIVATE_OPERATION_ROWS:
        raise HTTPException(413, {"code": "PRIVATE_ROW_LIMIT_EXCEEDED", "operation": operation,
                                  "limit": MAX_PRIVATE_OPERATION_ROWS, "resource": "private_rows"})
    if payload_sizes:
        total_bytes = db.scalar(select(func.coalesce(func.sum(union_all(*payload_sizes).subquery().c.payload_bytes), 0)))
        if total_bytes > MAX_PRIVATE_OPERATION_CIPHERTEXT_BYTES:
            raise HTTPException(413, {"code": "PRIVATE_PAYLOAD_LIMIT_EXCEEDED", "operation": operation,
                                      "limit": MAX_PRIVATE_OPERATION_CIPHERTEXT_BYTES, "resource": "ciphertext_bytes"})


def purge_conversation(db, row):
    from .models import Automation
    from .actions import purge_action_data
    from .jobs import purge_job_data
    from .native import purge_native_data
    from .people import purge_people_data
    _admit_private_operation(db, row.workspace_id, operation="purge_conversation", conversation_id=row.id)
    invalidate(db, row, "data_deleted")
    purge_action_data(db, row.id)
    purge_job_data(db, row.id)
    purge_native_data(db, row.id)
    purge_people_data(db, row.id)
    # Content-free tombstones are retained to stop old queued work from resurrecting deleted data.
    ids = list(db.scalars(select(Message.id).where(Message.workspace_id == row.workspace_id,
                                                 Message.conversation_id == row.id)))
    if ids:
        db.add(Suppression(workspace_id=row.workspace_id, conversation_id=row.id,
                           content_hash="chat-data-deleted", source_message_ids=ids))
    for model in [MessageEvent, ImportRecord, StyleProfile, Automation, Memory, Task]:
        db.execute(delete(model).where(model.workspace_id == row.workspace_id, model.conversation_id == row.id))
    for msg in db.scalars(select(Message).where(Message.workspace_id == row.workspace_id,
                                               Message.conversation_id == row.id)):
        msg.text = ""
        msg.deleted = True
        msg.excluded_from_learning = True
    for draft in db.scalars(select(Draft).where(Draft.workspace_id == row.workspace_id,
                                               Draft.conversation_id == row.id)):
        draft.text = ""
        draft.evidence_message_ids = []
        draft.missing_facts = []
        draft.approved_hash = None
    grant = db.scalar(select(Permission).where(Permission.conversation_id == row.id))
    for capability in ("read", "retain", "learn", "draft", "send", "share"):
        setattr(grant, capability, False)
    grant.version += 1
    row.control_state = "AI_OFF"
    for job in db.scalars(select(Outbox).where(Outbox.workspace_id == row.workspace_id,
                                             Outbox.status == "pending")):
        if job.payload.get("conversation_id") == row.id:
            job.status = "cancelled"


@router.delete("/conversations/{conversation_id}/data")
@serialized_control
def delete_conversation_data(conversation_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = conversation_for(db, user, conversation_id)
    purge_conversation(db, row)
    audit(db, row.workspace_id, user.id, "conversation.data_deleted", row.id)
    db.commit()
    return {"status": "completed", "backup_deletion": "No backup integration configured; apply tombstones on restore"}


@router.post("/data-export")
@serialized_control
def export_data(workspace_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    from .actions import export_action_data
    from .automatic_drafts import export_automatic_draft_data
    from .jobs import export_job_data
    from .jobs import export_local_reminders
    from .native import export_native_data, message_available
    from .people import export_people_data
    workspace_for(db, user, workspace_id)
    _admit_private_operation(db, workspace_id, operation="export")
    records = []
    readable = select(Permission.conversation_id).where(Permission.workspace_id == workspace_id,
               Permission.read.is_(True), or_(Permission.expires_at.is_(None), Permission.expires_at > now()))
    for conv in db.scalars(select(Conversation).where(Conversation.workspace_id == workspace_id,
                           Conversation.id.in_(readable)).order_by(Conversation.id).limit(MAX_PRIVATE_OPERATION_CONVERSATIONS)):
        grant = db.scalar(select(Permission).where(Permission.conversation_id == conv.id))
        if not grant or not grant.read or (grant.expires_at and aware(grant.expires_at) <= now()):
            continue
        records.append({"conversation": public(conv, "title", "kind"), "messages": [
            public(m, "sender_id", "text", "origin", "provider_timestamp", "author_kind") for m in
            db.scalars(select(Message).where(Message.workspace_id == workspace_id,
                                             Message.conversation_id == conv.id, Message.deleted.is_(False))
                       .order_by(Message.provider_timestamp).limit(20000)) if message_available(db, m)],
                        "actions": export_action_data(db, conv.id),
                        "jobs": export_job_data(db, conv.id),
                        "automatic_drafts": export_automatic_draft_data(db, conv.id),
                        "people": export_people_data(db, conv.id),
                        "native": export_native_data(db, conv.id),
                        "memories": [public(m, "text", "status", "source_message_ids", "version", "expires_at")
                                     for m in db.scalars(select(Memory).where(Memory.conversation_id == conv.id))]})
    return {"workspace_id": workspace_id, "conversations": records, "format_version": 2,
            "local_reminders": export_local_reminders(db, workspace_id),
            "native_material": "Private provider originals are dispatch-only; lifecycle and evidence metadata included"}


@router.delete("/account-data")
@serialized_control
def delete_account_data(workspace_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    from .people import purge_workspace_people_data
    from .jobs import purge_workspace_jobs
    workspace = workspace_for(db, user, workspace_id)
    # Authentication keys are always erased, including when a later bounded
    # content purge needs a separate batch. Fencing closes the private actor.
    from .whatsapp_personal import erase_session_credentials
    personal = list(db.scalars(select(Connector).where(Connector.workspace_id == workspace_id,
                                                     Connector.provider == "whatsapp_personal")))
    for conn in personal:
        conn.status, conn.lease_expires_at = "disconnected", now()
        conn.fence += 1
        erase_session_credentials(db, conn)
    if personal:
        db.commit()
        from .storage_authority import lock_workspace
        lock_workspace(db, workspace_id)
        db.refresh(workspace)
    _admit_private_operation(db, workspace_id, operation="purge_workspace")
    workspace.paused = True
    workspace.pause_generation += 1
    for conv in db.scalars(select(Conversation).where(Conversation.workspace_id == workspace_id)):
        purge_conversation(db, conv)
    for conn in db.scalars(select(Connector).where(Connector.workspace_id == workspace_id)):
        conn.status = "disconnected"
        conn.fence += 1
    db.execute(delete(Task).where(Task.workspace_id == workspace_id))
    purge_workspace_people_data(db, workspace_id)
    purge_workspace_jobs(db, workspace_id)
    for event in db.scalars(select(Outbox).where(Outbox.workspace_id == workspace_id)):
        event.payload = {}
        event.status = "cancelled"
    audit(db, workspace_id, user.id, "workspace.data_deleted", workspace_id)
    db.commit()
    return {"status": "completed", "scope": "application conversation content",
            "limitations": ["Provider-side access token revocation is a separate provider operation",
                            "Content-free audit, IDs and tombstones remain; backups require lifecycle policy"]}
