"""Owner-scoped QR gateway for actual personal WhatsApp sessions.

Signal authentication and raw QR values belong to the private session service.
The application remains the authority for account ownership, grants and sends.
"""
from datetime import datetime, timedelta
import asyncio
import hashlib
import re
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from .access import audit, conversation_for, permission_for, workspace_for
from .auth import get_current_user
from .core import capabilities, connector_for, invalidate, public
from .db import aware, get_db, now, uid
from .messaging import CanonicalEvent, EventContent, content_hash, ingest_event, require_session_service, submit_guard
from .models import Connector, Conversation, Draft, Message, Permission, SendAttempt, User, Workspace
from .storage_authority import lock_workspace
from .whatsapp_personal_models import PersonalAccountAlias, PersonalAuthKey, PersonalWhatsAppSession

router = APIRouter(tags=["whatsapp-personal"])
PREFIX = "/integrations/whatsapp/personal"
PROVIDER = "whatsapp_personal"
LEASE_SECONDS = 60
MAX_CHATS = 200
JID = re.compile(r"[0-9]{5,24}@(?:s\.whatsapp\.net|lid)\Z")
STATES = {"starting", "qr", "connected", "reconnecting", "disconnected", "logged_out", "failed"}
PHONE = re.compile(r"\+[0-9][0-9 ()-]{6,24}\Z")
PAIRING_CODE = re.compile(r"[A-Z0-9]{4}-?[A-Z0-9]{4}\Z")


def number_hash(digits):
    return hashlib.sha256(digits.encode()).hexdigest()


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartInput(Payload):
    workspace_id: str = Field(min_length=36, max_length=36)
    # Owner's own number in international form. The session requests a pairing
    # code for it and only this number may complete the link.
    phone_number: str | None = Field(default=None, max_length=32)

    @field_validator("phone_number")
    @classmethod
    def international_number(cls, value):
        if value is None:
            return None
        if not PHONE.fullmatch(value.strip()):
            raise ValueError("Enter the full international number starting with + and the country code")
        digits = re.sub(r"\D", "", value)
        if not 8 <= len(digits) <= 15 or digits.startswith("0"):
            raise ValueError("Enter the full international number starting with + and the country code")
        return digits


class ConnectorInput(Payload):
    connector_id: str = Field(min_length=36, max_length=36)


class ContactInput(ConnectorInput):
    provider_chat_id: str = Field(min_length=5, max_length=160)
    title: str = Field(min_length=1, max_length=160)
    read: bool = False
    retain: bool = False
    learn: bool = False
    draft: bool = False
    send: bool = False
    recipient_opted_in: bool = False

    @field_validator("provider_chat_id")
    @classmethod
    def contact_jid(cls, value):
        if not JID.fullmatch(value):
            raise ValueError("A canonical one-to-one WhatsApp contact is required")
        return value

    @model_validator(mode="after")
    def grants(self):
        if (self.learn or self.draft or self.send) and not (self.read and self.retain):
            raise ValueError("Learning, drafting and sending require read and retain")
        if self.send and not self.recipient_opted_in:
            raise ValueError("Recipient opt-in is required for sending")
        return self


class Identity(Payload):
    schema_version: Literal[1] = 1
    workspace_id: str = Field(min_length=36, max_length=36)
    connector_id: str = Field(min_length=36, max_length=36)
    connector_fence: int = Field(ge=1)
    account_id: str | None = Field(default=None, max_length=120)


class SendEnvelope(Identity):
    account_id: str = Field(min_length=5, max_length=120)
    conversation_id: str = Field(min_length=36, max_length=36)
    recipient_id: str = Field(min_length=5, max_length=160)
    draft_id: str = Field(min_length=36, max_length=36)
    attempt_id: str = Field(min_length=36, max_length=36)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1, max_length=4096)


class AuthorityInput(Identity):
    operation: Literal["start", "status", "chats", "disconnect", "ingest", "send"]
    send: SendEnvelope | None = None


class SessionEvent(Identity):
    event_type: Literal["connection", "message", "receipt"]
    data: dict = Field(default_factory=dict)


class ConnectionData(Payload):
    state: Literal["qr", "connected", "reconnecting", "disconnected", "logged_out", "failed"]
    account_id: str | None = Field(default=None, max_length=120)
    account_aliases: list[str] = Field(default_factory=list, max_length=4)


class ReceiptData(Payload):
    attempt_id: str | None = Field(default=None, max_length=36)
    draft_id: str | None = Field(default=None, max_length=36)
    payload_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provider_message_id: str = Field(min_length=1, max_length=180)
    status: Literal["submitting", "accepted", "uncertain", "delivered", "failed"]


class MessageData(Payload):
    conversation_id: str = Field(min_length=36, max_length=36)
    provider_message_id: str = Field(min_length=1, max_length=180)
    provider_chat_id: str = Field(min_length=5, max_length=160)
    sender_id: str = Field(min_length=5, max_length=160)
    direction: Literal["inbound", "outbound"]
    origin: Literal["live", "history", "replay", "unknown"]
    event_type: Literal["message.created", "message.edited", "message.deleted"] = "message.created"
    author_kind: str | None = Field(default=None, max_length=40)
    provider_timestamp: datetime
    content: EventContent
    source_revision: int = Field(default=1, ge=1, le=2147483647)
    expires_at: datetime | None = None

    @field_validator("provider_timestamp", "expires_at")
    @classmethod
    def timestamp_has_zone(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Personal WhatsApp timestamps require an explicit timezone")
        return aware(value) if value is not None else None


def configured(settings):
    return bool(settings.whatsapp_personal_enabled and settings.whatsapp_personal_session_url
                and len(settings.whatsapp_personal_session_token.encode()) >= 32
                and (settings.whatsapp_personal_authority_token or settings.internal_service_token))


def require_owner(settings, user):
    if not configured(settings):
        raise HTTPException(503, "Personal WhatsApp session service is unavailable")
    if settings.environment == "production" and not user.subject.startswith(("google:", "operator:")):
        raise HTTPException(403, "Verified Google or owner sign-in is required for personal WhatsApp")


def personal_for(db, user, connector_id, settings):
    require_owner(settings, user)
    row = connector_for(db, user, connector_id)
    if row.provider != PROVIDER:
        raise HTTPException(404, "Personal WhatsApp connector not found")
    return row


def identity(row):
    return {"schema_version": 1, "workspace_id": row.workspace_id, "connector_id": row.id,
            "connector_fence": row.fence,
            "account_id": row.account_id if JID.fullmatch(row.account_id) else None}


def connector_payload(row):
    return {**public(row, "workspace_id", "provider", "account_id", "status", "capabilities", "fence"),
            "lease_expires_at": aware(row.lease_expires_at).isoformat() if row.lease_expires_at else None}


def status_payload(row, settings):
    healthy = bool(row and row.status == "connected" and row.lease_expires_at
                   and aware(row.lease_expires_at) > now())
    return {"enabled": settings.whatsapp_personal_enabled, "configured": configured(settings),
            "status": "not_connected" if row is None else (
                "unavailable" if row.status == "connected" and not healthy else row.status),
            "connector": connector_payload(row) if row else None, "connected": healthy,
            "simulation": False}


def current_identity(db, body, *, require_session=True, connected=False):
    row = db.get(Connector, body.connector_id)
    if (row is None or row.provider != PROVIDER or row.workspace_id != body.workspace_id
            or row.fence != body.connector_fence):
        raise HTTPException(409, "Personal WhatsApp session authority changed")
    expected = identity(row)["account_id"]
    if expected != body.account_id:
        raise HTTPException(409, "Personal WhatsApp account identity changed")
    session = db.scalar(select(PersonalWhatsAppSession).where(
        PersonalWhatsAppSession.connector_id == row.id,
        PersonalWhatsAppSession.workspace_id == row.workspace_id))
    if require_session and (session is None or session.connector_fence != row.fence):
        raise HTTPException(409, "Personal WhatsApp session is revoked")
    if connected and (row.status != "connected" or not row.lease_expires_at
                      or aware(row.lease_expires_at) <= now() or expected is None):
        raise HTTPException(409, "Personal WhatsApp session is disconnected or lease expired")
    return row, session


def erase_session_credentials(db, connector):
    """Erase opaque Signal keys even when a content purge exceeds its admission limit.

    Account aliases are content-free binding tombstones; keeping them prevents a
    PN/LID alias from allowing a different workspace to claim the retained account.
    The private actor's periodic authority check closes a fenced socket.
    """
    if connector.provider != PROVIDER:
        return
    db.execute(delete(PersonalAuthKey).where(PersonalAuthKey.connector_id == connector.id,
                                             PersonalAuthKey.workspace_id == connector.workspace_id))
    db.execute(delete(PersonalWhatsAppSession).where(PersonalWhatsAppSession.connector_id == connector.id,
                                                    PersonalWhatsAppSession.workspace_id == connector.workspace_id))


def revoke(db, row, reason):
    row.status, row.lease_expires_at = "disconnected", now()
    row.fence += 1
    erase_session_credentials(db, row)
    for conv in db.scalars(select(Conversation).where(Conversation.connector_id == row.id,
                                                     Conversation.workspace_id == row.workspace_id)):
        invalidate(db, conv, reason)
        conv.control_state = "RECONNECT_REVIEW"


def private_request(settings, operation, payload):
    """Fixed private origin, no redirects/proxies, bounded metadata response."""
    try:
        with httpx.Client(timeout=settings.whatsapp_personal_timeout_seconds,
                          follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", settings.whatsapp_personal_session_url.rstrip("/") + "/v1/" + operation,
                               headers={"Authorization": f"Bearer {settings.whatsapp_personal_session_token}"},
                               json=payload) as response:
                if response.status_code != 200:
                    raise ValueError("Private session service rejected request")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 256_000:
                        raise ValueError("Private response too large")
                    chunks.append(chunk)
                import json
                result = json.loads(b"".join(chunks))
        if (not isinstance(result, dict) or result.get("schema_version") != 1
                or result.get("state") not in STATES):
            raise ValueError("Invalid private session metadata")
        account = result.get("account_id")
        if account is not None and (not isinstance(account, str) or not JID.fullmatch(account)):
            raise ValueError("Invalid private account metadata")
        return result
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(503, "Personal WhatsApp session service is unavailable") from None


@router.get(PREFIX + "/config")
def config(request: Request, workspace_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    settings = request.app.state.settings
    return {"enabled": settings.whatsapp_personal_enabled, "configured": configured(settings),
            "status": "configured" if configured(settings) else "disabled" if not settings.whatsapp_personal_enabled
            else "unavailable", "simulation": False, "google_verified_required": settings.environment == "production",
            "capabilities": capabilities(PROVIDER), "live_verified": False, "owner_subject": user.subject}


@router.get(PREFIX + "/status")
def status(workspace_id: str, request: Request, response: Response,
           user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    response.headers["Cache-Control"] = "private, no-store"
    row = db.scalar(select(Connector).where(Connector.workspace_id == workspace_id,
                                           Connector.provider == PROVIDER))
    return {**status_payload(row, request.app.state.settings), "owner_subject": user.subject}


@router.post(PREFIX + "/start")
def start(body: StartInput, request: Request, response: Response,
          user=Depends(get_current_user), db=Depends(get_db)):
    settings = request.app.state.settings
    require_owner(settings, user)
    workspace_for(db, user, body.workspace_id)
    db.rollback()
    with submit_guard(body.workspace_id):
        lock_workspace(db, body.workspace_id)
        workspace_for(db, user, body.workspace_id)
        row = db.scalar(select(Connector).where(Connector.workspace_id == body.workspace_id,
                                               Connector.provider == PROVIDER))
        if row is None:
            row = Connector(id=uid(), workspace_id=body.workspace_id, provider=PROVIDER,
                            account_id="pending:" + uid(), owner_sender_id="unpaired",
                            status="starting", capabilities=capabilities(PROVIDER))
            db.add(row)
            db.flush()
        session = db.scalar(select(PersonalWhatsAppSession).where(PersonalWhatsAppSession.connector_id == row.id))
        expected = number_hash(body.phone_number) if body.phone_number and row.status != "connected" else None
        if row.status != "connected" and body.phone_number is None and settings.environment == "production":
            raise HTTPException(422, "Enter your WhatsApp number to bind this pairing to your own account")
        # A number-bound request always starts a fresh fenced session so the
        # private service requests a new pairing code for exactly that number.
        if row.status in {"disconnected", "failed", "logged_out"} or (expected and session is not None):
            revoke(db, row, "personal_session_restart")
            row.status = "starting"
            session = None
        if session is None:
            session = PersonalWhatsAppSession(workspace_id=row.workspace_id, connector_id=row.id,
                                              connector_fence=row.fence, status="starting")
            db.add(session)
        if expected:
            session.expected_account_hash = expected
        envelope = identity(row)
        pairing_phone = {"pairing_phone": body.phone_number} if expected else {}
        audit(db, row.workspace_id, user.id, "whatsapp.personal.start_requested", row.id)
        db.commit()
    # This transaction is finished before the private service can call back.
    private_request(settings, "sessions/start", {**envelope, **pairing_phone})
    db.expire_all()
    row = db.get(Connector, envelope["connector_id"])
    if row is None or row.workspace_id != envelope["workspace_id"] or row.fence != envelope["connector_fence"]:
        raise HTTPException(409, "Personal WhatsApp session changed while starting")
    current_identity(db, Identity(**identity(row)))
    response.headers["Cache-Control"] = "private, no-store"
    return status_payload(row, settings)


def private_owner_read(db, user, connector_id, settings, operation, extra=None):
    row = personal_for(db, user, connector_id, settings)
    envelope = identity(row)
    db.rollback()
    result = private_request(settings, operation, {**envelope, **(extra or {})})
    db.expire_all()
    row = db.get(Connector, envelope["connector_id"])
    if row is None or row.workspace_id != envelope["workspace_id"] or row.fence != envelope["connector_fence"]:
        raise HTTPException(409, "Personal WhatsApp session changed during private read")
    current_identity(db, Identity(**identity(row)))
    workspace_for(db, user, row.workspace_id)
    if result.get("account_id") != identity(row)["account_id"]:
        raise HTTPException(409, "Personal WhatsApp account changed; refresh connection status")
    return row, result


@router.get(PREFIX + "/pairing")
def pairing(connector_id: str, request: Request, response: Response,
            user=Depends(get_current_user), db=Depends(get_db)):
    row, result = private_owner_read(db, user, connector_id, request.app.state.settings, "sessions/status")
    pairable = result["state"] == "qr" and row.status in {"starting", "pairing"}
    safe = pairing_value(result.get("qr"), "value", 45, lambda value: 1 <= len(value) <= 4096) if pairable else None
    code = pairing_value(result.get("pairing"), "code", 180, PAIRING_CODE.fullmatch) if pairable else None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    # The private service calls this state "qr"; clients and connector status call it "pairing".
    state = "pairing" if result["state"] == "qr" else result["state"]
    return {"connector_id": row.id, "state": state, "qr": safe, "pairing_code": code,
            "poll_after_seconds": 3}


def pairing_value(item, key, max_seconds, valid):
    if not isinstance(item, dict):
        return None
    try:
        value = item[key]
        expires = aware(datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00")))
        if not isinstance(value, str) or not valid(value) or not now() < expires <= now() + timedelta(seconds=max_seconds):
            raise ValueError("Invalid pairing expiry")
        return {key: value, "expires_at": expires.isoformat()}
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def contact_metadata(result):
    contacts = result.get("chats", [])
    if not isinstance(contacts, list) or len(contacts) > MAX_CHATS:
        raise HTTPException(503, "Invalid personal WhatsApp contact metadata")
    safe, seen = [], set()
    for item in contacts:
        if not isinstance(item, dict):
            raise HTTPException(503, "Invalid personal WhatsApp contact metadata")
        jid, title = item.get("provider_chat_id"), item.get("title")
        if (not isinstance(jid, str) or not JID.fullmatch(jid) or item.get("kind") != "contact"
                or jid in seen):
            continue
        if not isinstance(title, str) or not 1 <= len(title) <= 160:
            title = jid
        safe.append({"provider_chat_id": jid, "title": title, "kind": "contact"})
        seen.add(jid)
    return safe


def permission_payload(permission):
    return {**{name: bool(getattr(permission, name)) if permission else False
               for name in ("read", "retain", "learn", "draft", "send", "share")},
            "version": permission.version if permission else 0,
            "expires_at": aware(permission.expires_at).isoformat() if permission and permission.expires_at else None}


def owner_contact(db, connector, jid):
    """Both observed PN and LID addresses identify the same owner account."""
    return jid == connector.account_id or db.scalar(select(PersonalAccountAlias.alias_id).where(
        PersonalAccountAlias.alias_id == jid, PersonalAccountAlias.connector_id == connector.id,
        PersonalAccountAlias.workspace_id == connector.workspace_id)) is not None


@router.get(PREFIX + "/chats")
def chats(connector_id: str, request: Request, response: Response,
          user=Depends(get_current_user), db=Depends(get_db)):
    row, result = private_owner_read(db, user, connector_id, request.app.state.settings, "sessions/chats")
    current_identity(db, Identity(**identity(row)), connected=True)
    rows = []
    for item in contact_metadata(result):
        conv = db.scalar(select(Conversation).where(Conversation.connector_id == row.id,
                                                    Conversation.provider_chat_id == item["provider_chat_id"]))
        perm = db.scalar(select(Permission).where(Permission.conversation_id == conv.id)) if conv else None
        rows.append({**item, "conversation_id": conv.id if conv else None, "permissions": permission_payload(perm)})
    response.headers["Cache-Control"] = "private, no-store"
    return {"connector_id": row.id, "chats": rows, "limit": MAX_CHATS, "has_more": len(rows) >= MAX_CHATS}


@router.post(PREFIX + "/chats/authorize")
def authorize(body: ContactInput, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    settings = request.app.state.settings
    row, result = private_owner_read(db, user, body.connector_id, settings, "sessions/chats",
                                    {"provider_chat_id": body.provider_chat_id})
    if not any(item["provider_chat_id"] == body.provider_chat_id for item in contact_metadata(result)):
        raise HTTPException(404, "Contact is absent from the current WhatsApp session")
    envelope, workspace_id = identity(row), row.workspace_id
    db.rollback()
    with submit_guard(workspace_id):
        lock_workspace(db, workspace_id)
        row, _ = current_identity(db, Identity(**envelope), connected=True)
        workspace_for(db, user, workspace_id)
        if owner_contact(db, row, body.provider_chat_id):
            raise HTTPException(403, "Owner self-chat automation is unavailable")
        conv = db.scalar(select(Conversation).where(Conversation.connector_id == row.id,
                                                    Conversation.provider_chat_id == body.provider_chat_id))
        if conv is None:
            count = db.scalar(select(func.count()).select_from(Conversation).where(Conversation.connector_id == row.id))
            if count >= MAX_CHATS:
                raise HTTPException(413, "Personal WhatsApp contact authorization limit reached")
            conv = Conversation(id=uid(), workspace_id=workspace_id, connector_id=row.id,
                                provider_chat_id=body.provider_chat_id, title=body.title,
                                kind="contact", control_state="DRAFT_MODE")
            db.add(conv)
            db.flush()
            perm = Permission(workspace_id=workspace_id, conversation_id=conv.id)
            db.add(perm)
            db.flush()
        else:
            perm = db.scalar(select(Permission).where(Permission.conversation_id == conv.id,
                                                     Permission.workspace_id == workspace_id))
            if perm is None:
                raise HTTPException(409, "Contact permission record is unavailable")
            invalidate(db, conv, "personal_contact_grants_changed")
            perm.version += 1
        conv.title, conv.recipient_opted_in = body.title, body.recipient_opted_in
        for field in ("read", "retain", "learn", "draft", "send"):
            setattr(perm, field, getattr(body, field))
        # This owner action is a fresh explicit grant, including after expiry.
        perm.share, perm.expires_at = False, None
        audit(db, workspace_id, user.id, "whatsapp.personal.contact_authorized", conv.id,
              read=perm.read, retain=perm.retain, learn=perm.learn, draft=perm.draft, send=perm.send)
        db.commit()
        return {"conversation": public(conv, "workspace_id", "connector_id", "provider_chat_id", "title", "kind",
                                         "revision", "control_state"), "permissions": permission_payload(perm)}


@router.post(PREFIX + "/disconnect")
def disconnect(body: ConnectorInput, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    row = personal_for(db, user, body.connector_id, request.app.state.settings)
    workspace_id = row.workspace_id
    db.rollback()
    with submit_guard(workspace_id):
        lock_workspace(db, workspace_id)
        row = personal_for(db, user, body.connector_id, request.app.state.settings)
        revoke(db, row, "personal_disconnect")
        envelope = identity(row)
        audit(db, workspace_id, user.id, "whatsapp.personal.disconnected", row.id)
        db.commit()
    remote = "unknown"
    try:
        result = private_request(request.app.state.settings, "sessions/disconnect", envelope)
        remote = "confirmed" if result["state"] == "disconnected" else "unknown"
    except HTTPException:
        pass
    return {"status": "disconnected", "connector_id": body.connector_id,
            "fence": envelope["connector_fence"], "remote_session_close": remote,
            "provider_revocation": "not_verified"}


def validate_personal_transport(db, settings, connector, conversation):
    user = db.get(User, db.get(Workspace, connector.workspace_id).owner_id)
    require_owner(settings, user)
    if not settings.enable_external_sends:
        raise HTTPException(403, "External sending is disabled")
    current_identity(db, Identity(**identity(connector)), connected=True)
    if conversation.kind != "contact" or not JID.fullmatch(conversation.provider_chat_id):
        raise HTTPException(403, "Personal WhatsApp supports one-to-one text messages only")
    if owner_contact(db, connector, conversation.provider_chat_id) or not conversation.recipient_opted_in:
        raise HTTPException(403, "A contact's opt-in is required for sending")


def validate_send(db, body, settings):
    from .messaging import _validate_current
    row, _ = current_identity(db, body, connected=True)
    draft, attempt = db.get(Draft, body.draft_id), db.get(SendAttempt, body.attempt_id)
    if (draft is None or attempt is None or draft.workspace_id != row.workspace_id
            or attempt.workspace_id != row.workspace_id or attempt.draft_id != draft.id
            or attempt.conversation_id != body.conversation_id or draft.conversation_id != body.conversation_id
            or attempt.status != "dispatching" or draft.status != "dispatching"
            or attempt.connector_fence != row.fence or attempt.content_hash != body.payload_hash
            or draft.content_hash != body.payload_hash or content_hash(body.text) != body.payload_hash
            or draft.text != body.text or draft.recipient_id != body.recipient_id):
        raise HTTPException(409, "Exact personal WhatsApp send authorization changed")
    _, _, checked = _validate_current(db, draft, settings)
    if checked.id != row.id:
        raise HTTPException(409, "Personal WhatsApp send connector changed")
    return row, draft, attempt


@router.post("/internal/whatsapp-session-authority", dependencies=[Depends(require_session_service)])
def session_authority(body: AuthorityInput, request: Request, db=Depends(get_db)):
    settings = request.app.state.settings
    result = {**body.model_dump(exclude={"operation", "send"}), "allowed": False,
              "reason_code": "authority_unavailable", "authority_expires_at": now().isoformat(), "grants": []}
    if not configured(settings):
        return result
    try:
        row, _ = current_identity(db, body, require_session=body.operation != "disconnect",
                                  connected=body.operation in {"ingest", "send", "chats"})
        if body.operation == "status" and row.status == "connected":
            if row.lease_expires_at is None or aware(row.lease_expires_at) <= now():
                raise HTTPException(409, "Personal session lease expired")
            row.lease_expires_at = now() + timedelta(seconds=LEASE_SECONDS)
            session = db.scalar(select(PersonalWhatsAppSession).where(PersonalWhatsAppSession.connector_id == row.id))
            session.last_health_at = now()
            db.commit()
        owner = db.get(User, db.get(Workspace, row.workspace_id).owner_id)
        require_owner(settings, owner)
        if body.operation != "disconnect" and row.status in {"disconnected", "failed", "logged_out"}:
            raise HTTPException(409, "Personal session was revoked")
        paused = db.get(Workspace, row.workspace_id).paused
        if body.operation == "send" and paused:
            raise HTTPException(409, "Workspace is paused")
        if body.operation == "ingest" and paused:
            # A paused owner grants no content access, while the managed socket
            # remains healthy for a later resume. Denial would stop the actor.
            return {**result, "allowed": True, "reason_code": "workspace_paused_no_content_grants",
                    "authority_expires_at": (now() + timedelta(seconds=5)).isoformat()}
        if body.operation == "send":
            if body.send is None or any(getattr(body.send, name) != getattr(body, name) for name in
                                        ("workspace_id", "connector_id", "connector_fence", "account_id")):
                raise HTTPException(409, "Exact personal send identity is required")
            validate_send(db, body.send, settings)
        permitted = list(db.execute(select(Conversation, Permission).join(
            Permission, Permission.conversation_id == Conversation.id).where(
                Conversation.connector_id == row.id, Conversation.workspace_id == row.workspace_id,
                Permission.workspace_id == row.workspace_id, Conversation.kind == "contact",
                Permission.read.is_(True), Permission.retain.is_(True),
                Permission.expires_at.is_(None) | (Permission.expires_at > now())
            ).order_by(Conversation.id).limit(MAX_CHATS)))
        grants = [{"conversation_id": conv.id, "provider_chat_id": conv.provider_chat_id,
                   "read": True, "retain": True,
                   "send": bool(perm.send and conv.recipient_opted_in and not conv.recipient_opted_out)}
                  for conv, perm in permitted if JID.fullmatch(conv.provider_chat_id)]
        return {**result, "allowed": True, "reason_code": "current_authority",
                "authority_expires_at": (now() + timedelta(seconds=5)).isoformat(), "grants": grants}
    except HTTPException:
        return result


def apply_connection(db, body, data):
    row, session = current_identity(db, body)
    if data.state == "connected":
        if data.account_id is None or not JID.fullmatch(data.account_id):
            raise HTTPException(422, "Actual personal WhatsApp account identity is required")
        if body.account_id is not None and data.account_id != body.account_id:
            raise HTTPException(409, "Personal WhatsApp account identity changed")
        aliases = set(data.account_aliases + [data.account_id])
        if any(not JID.fullmatch(alias) for alias in aliases):
            raise HTTPException(422, "Invalid personal WhatsApp account alias")
        if session.expected_account_hash and not any(
                alias.endswith("@s.whatsapp.net") and number_hash(alias.split("@", 1)[0]) == session.expected_account_hash
                for alias in aliases):
            # Only the number the owner entered may complete the link; a relayed
            # QR or pairing code scanned by another account is refused.
            revoke(db, row, "personal_account_number_mismatch")
            db.commit()
            raise HTTPException(409, "The linked WhatsApp number does not match the number entered for pairing")
        for alias in aliases:
            occupied = db.get(PersonalAccountAlias, alias)
            other = db.scalar(select(Connector).where(Connector.provider == PROVIDER,
                                                       Connector.account_id == alias, Connector.id != row.id))
            if (occupied and occupied.connector_id != row.id) or other:
                revoke(db, row, "personal_account_binding_rejected")
                db.commit()
                raise HTTPException(409, "This WhatsApp account is already bound to another workspace")
        if row.status == "connected" and (not row.lease_expires_at or aware(row.lease_expires_at) <= now()):
            for conv in db.scalars(select(Conversation).where(Conversation.connector_id == row.id,
                                                             Conversation.workspace_id == row.workspace_id)):
                invalidate(db, conv, "personal_session_lease_gap")
                conv.control_state = "RECONNECT_REVIEW"
        for alias in aliases:
            if db.get(PersonalAccountAlias, alias) is None:
                db.add(PersonalAccountAlias(alias_id=alias, workspace_id=row.workspace_id, connector_id=row.id))
        row.account_id, row.owner_sender_id = data.account_id, data.account_id
        row.status = session.status = "connected"
        row.lease_expires_at = now() + timedelta(seconds=LEASE_SECONDS)
        row.capabilities = {**capabilities(PROVIDER), "live_receive": "supported", "send_text": "supported",
                            "qr_pairing": "supported", "delivery_receipts": "supported",
                            "human_outgoing": "supported", "assistant_echo": "supported"}
        session.last_connected_at = session.last_health_at = now()
        session.error_code = None
    else:
        target = "pairing" if data.state == "qr" else data.state
        if row.status == "connected":
            for conv in db.scalars(select(Conversation).where(Conversation.connector_id == row.id,
                                                             Conversation.workspace_id == row.workspace_id)):
                invalidate(db, conv, "personal_session_connection_gap")
                conv.control_state = "RECONNECT_REVIEW"
        row.status = session.status = target
        row.lease_expires_at = None
        session.last_health_at = now()
        if data.state == "logged_out":
            erase_session_credentials(db, row)
            row.fence += 1
    return {"status": "accepted", "connector_id": row.id, "connector_fence": row.fence}


def apply_message(db, body, data):
    row, _ = current_identity(db, body, connected=True)
    if db.get(Workspace, row.workspace_id).paused:
        return {"status": "ignored", "reason": "workspace_paused"}
    if not JID.fullmatch(data.provider_chat_id):
        return {"status": "ignored", "reason": "unsupported_chat"}
    conv = db.scalar(select(Conversation).where(Conversation.connector_id == row.id,
                                               Conversation.workspace_id == row.workspace_id,
                                               Conversation.provider_chat_id == data.provider_chat_id,
                                               Conversation.kind == "contact"))
    if conv is None:
        return {"status": "ignored", "reason": "chat_not_authorized"}
    if data.conversation_id != conv.id:
        raise HTTPException(409, "Personal WhatsApp conversation grant changed")
    if data.direction == "inbound" and data.sender_id != conv.provider_chat_id:
        raise HTTPException(409, "Personal WhatsApp sender does not match this contact")
    if data.direction == "outbound" and data.sender_id != row.owner_sender_id:
        raise HTTPException(409, "Personal WhatsApp outgoing sender does not match the owner")
    # fromMe proves only which account sent it, not whether a human or automation
    # authored it. Only our send ledger or explicit owner review resolves this.
    author = "unknown_owner_outgoing" if data.direction == "outbound" else "contact_human"
    event = CanonicalEvent(schema_version=1,
        event_id=content_hash(f"{data.provider_message_id}:{data.event_type}:{data.source_revision}"),
        workspace_id=row.workspace_id, connector_id=row.id, conversation_id=conv.id,
        provider=PROVIDER, account_id=row.account_id, provider_message_id=data.provider_message_id,
        sender_id=data.sender_id, direction=data.direction, origin=data.origin, event_type=data.event_type,
        author_kind=author, provider_timestamp=data.provider_timestamp, content=data.content,
        source_revision=data.source_revision, expires_at=data.expires_at)
    return ingest_event(db, event)


def apply_receipt(db, body, data, settings):
    row, _ = current_identity(db, body, connected=False)
    attempt = db.get(SendAttempt, data.attempt_id) if data.attempt_id else db.scalar(select(SendAttempt).join(
        Conversation, Conversation.id == SendAttempt.conversation_id).where(
            Conversation.connector_id == row.id, SendAttempt.workspace_id == row.workspace_id,
            SendAttempt.provider_message_id == data.provider_message_id))
    draft = db.get(Draft, attempt.draft_id) if attempt else None
    conv = db.get(Conversation, attempt.conversation_id) if attempt else None
    if (attempt is None or draft is None or conv is None or conv.connector_id != row.id
            or conv.workspace_id != row.workspace_id
            or attempt.workspace_id != row.workspace_id or draft.workspace_id != row.workspace_id
            or attempt.connector_fence != row.fence
            or data.draft_id is not None and data.draft_id != draft.id
            or data.payload_hash is not None and data.payload_hash != attempt.content_hash):
        raise HTTPException(409, "Personal receipt has no correlated send authorization")
    if data.status in {"submitting", "accepted", "uncertain"} and (
            data.attempt_id is None or data.draft_id is None or data.payload_hash is None):
        raise HTTPException(422, "Submission receipts require exact send authorization")
    if attempt.provider_message_id and attempt.provider_message_id != data.provider_message_id:
        raise HTTPException(409, "Personal receipt provider reference changed")
    collision = db.scalar(select(SendAttempt).where(SendAttempt.provider_message_id == data.provider_message_id,
                                                    SendAttempt.id != attempt.id,
                                                    SendAttempt.workspace_id == row.workspace_id))
    if collision:
        raise HTTPException(409, "Personal provider reference is already correlated")
    if data.status == "submitting":
        validate_send(db, SendEnvelope(**identity(row), conversation_id=conv.id,
                     recipient_id=draft.recipient_id, draft_id=draft.id, attempt_id=attempt.id,
                     payload_hash=data.payload_hash, text=draft.text), settings)
        attempt.provider_message_id = data.provider_message_id
    else:
        if not attempt.provider_message_id:
            raise HTTPException(409, "Personal receipt is missing the durable submission record")
        ranks = {"dispatching": 0, "uncertain": 0, "accepted": 1, "failed": 2, "delivered": 3}
        if ranks.get(attempt.status, -1) < ranks[data.status] or (
                data.status == "uncertain" and attempt.status == "dispatching"):
            attempt.status = draft.status = data.status
            from .companion import settle_action_budget
            settlement = "uncertain" if data.status == "uncertain" else "consumed" if data.status in {
                "accepted", "delivered"} else "released"
            settle_action_budget(db, row.workspace_id, f"draft:{draft.id}", settlement)
    return {"status": "accepted", "attempt_id": attempt.id}


@router.post("/internal/whatsapp-session-events", dependencies=[Depends(require_session_service)])
def session_event(body: SessionEvent, request: Request, db=Depends(get_db)):
    if not configured(request.app.state.settings):
        raise HTTPException(503, "Personal WhatsApp is disabled")
    parsers = {"connection": ConnectionData, "message": MessageData, "receipt": ReceiptData}
    try:
        data = parsers[body.event_type].model_validate(body.data)
    except ValidationError:
        raise HTTPException(422, "Invalid personal WhatsApp event") from None
    db.rollback()
    with submit_guard(body.workspace_id):
        lock_workspace(db, body.workspace_id)
        if body.event_type == "connection":
            result = apply_connection(db, body, data)
        elif body.event_type == "message":
            result = apply_message(db, body, data)
        else:
            result = apply_receipt(db, body, data, request.app.state.settings)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if body.event_type == "connection":
                lock_workspace(db, body.workspace_id)
                row = db.get(Connector, body.connector_id)
                if row and row.workspace_id == body.workspace_id and row.fence == body.connector_fence:
                    revoke(db, row, "personal_account_binding_race_rejected")
                    db.commit()
            raise HTTPException(409, "Personal WhatsApp account or event already claimed") from None
        return result


async def send_text(settings, final_check):
    if final_check is None:
        return "failed", None, "personal_authority_missing"
    try:
        async with httpx.AsyncClient(timeout=settings.whatsapp_personal_timeout_seconds,
                                     follow_redirects=False, trust_env=False) as client:
            envelope = final_check()
            response = await client.post(settings.whatsapp_personal_session_url.rstrip("/") + "/v1/messages/send",
                headers={"Authorization": f"Bearer {settings.whatsapp_personal_session_token}"}, json=envelope)
        if 400 <= response.status_code < 500:
            return "failed", None, "personal_service_rejected"
        if response.status_code != 200 or len(response.content) > 16_000:
            return "uncertain", None, "personal_transport_outcome_unknown"
        result = response.json()
        if not isinstance(result, dict):
            return "uncertain", None, "personal_provider_reference_missing"
        outcome, reference = result.get("status"), result.get("provider_message_id")
        if outcome not in {"accepted", "uncertain", "failed"} or not isinstance(reference, str) or not 1 <= len(reference) <= 180:
            return "uncertain", None, "personal_provider_reference_missing"
        return outcome, reference, None if outcome == "accepted" else "personal_transport_outcome_unknown"
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError):
        return "uncertain", None, "personal_transport_outcome_unknown"


class ConfirmAuthorship(Payload):
    conversation_id: str = Field(min_length=36, max_length=36)
    message_ids: list[str] = Field(min_length=1, max_length=100)
    confirm_authored_by_owner: Literal[True]


@router.post(PREFIX + "/authorship/confirm")
def confirm_authorship(body: ConfirmAuthorship, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    conv = conversation_for(db, user, body.conversation_id)
    row = personal_for(db, user, conv.connector_id, request.app.state.settings)
    workspace_id = row.workspace_id
    db.rollback()
    with submit_guard(workspace_id):
        lock_workspace(db, workspace_id)
        conv = conversation_for(db, user, body.conversation_id)
        row = personal_for(db, user, conv.connector_id, request.app.state.settings)
        permission_for(db, conv, "read")
        permission_for(db, conv, "retain")
        ids = set(body.message_ids)
        messages = list(db.scalars(select(Message).where(Message.id.in_(ids),
            Message.workspace_id == workspace_id, Message.connector_id == row.id, Message.conversation_id == conv.id)))
        from .native import message_available
        if len(messages) != len(ids) or any(message.direction != "outbound" or message.sender_id != row.owner_sender_id
                or message.author_kind not in {"unknown_owner_outgoing", "human_owner"}
                or message.deleted or not message_available(db, message) for message in messages):
            raise HTTPException(409, "Only available outgoing messages from this account can be confirmed")
        changed = 0
        for message in messages:
            if message.author_kind != "human_owner":
                message.author_kind, message.excluded_from_learning = "human_owner", False
                message.revision += 1
                changed += 1
        if changed:
            invalidate(db, conv, "personal_owner_authorship_confirmed")
            from .intelligence import refresh_style_from_messages
            refresh_style_from_messages(db, conv)
        audit(db, workspace_id, user.id, "whatsapp.personal.owner_authorship_confirmed", conv.id, count=changed)
        db.commit()
        return {"status": "confirmed", "conversation_id": conv.id, "confirmed_count": changed}
