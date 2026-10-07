"""Owner-scoped onboarding for the deployment's eligible Business Cloud number.

This flow does not pair a consumer WhatsApp account. Business-app history needs
Meta's approved Coexistence onboarding, provider opt-in, and local contact grants.
"""

from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select

from .access import audit, conversation_for, permission_for, workspace_for
from .auth import get_current_user
from .core import (ConnectorInput, connector_for, create_connector, invalidate,
                   public, serialized_control)
from .db import aware, get_db, now, uid
from .models import Connector, Conversation, Message, Permission, SendAttempt, StyleProfile
from .provider_authority import require_whatsapp_owner, whatsapp_owner_authorized
from .whatsapp_provider import graph_endpoint, normalize_phone

router = APIRouter(prefix="/integrations/whatsapp", tags=["whatsapp"])
COEXISTENCE_REFERENCE = (
    "https://developers.facebook.com/docs/whatsapp/embedded-signup/custom-flows/onboarding-business-app-users/"
)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectInput(Payload):
    workspace_id: str


class ContactInput(Payload):
    connector_id: str
    phone_number: str = Field(min_length=6, max_length=40)
    title: str = Field(min_length=1, max_length=160)
    read: bool = False
    retain: bool = False
    learn: bool = False
    draft: bool = False
    send: bool = False
    recipient_opted_in: bool = False

    @model_validator(mode="after")
    def validate_contact_grants(self):
        self.phone_number = normalize_phone(self.phone_number)
        if (self.learn or self.draft or self.send) and not (self.read and self.retain):
            raise ValueError("Learning, drafting and sending require read and retain permission")
        if self.send and not self.recipient_opted_in:
            raise ValueError("Sending requires the recipient's opt-in")
        return self


class SyncInput(Payload):
    connector_id: str


class ConfirmAuthorship(Payload):
    conversation_id: str
    message_ids: list[str] = Field(min_length=1, max_length=100)
    confirm_authored_by_owner: Literal[True]


@router.get("/operator-identity")
def own_operator_identity(workspace_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    """Let a signed-in owner identify themselves for manual deployment binding.

    This reveals only the current owner's own stable Google subject. It does
    not grant provider access, disclose the configured operator, or change the
    deployment binding. Google tokens and application session secrets remain
    private to their existing exchanges.
    """
    workspace_for(db, user, workspace_id)
    provider, separator, subject = user.subject.partition(":")
    if provider != "google" or not separator or not subject:
        raise HTTPException(403, "Verified Google sign-in is required for operator identity")
    return {"workspace_id": workspace_id, "provider": "google", "google_subject": subject,
            "identity_verified": True}


@router.get("/status")
def connection_status(workspace_id: str, request: Request,
                      user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    settings = request.app.state.settings
    authorized = whatsapp_owner_authorized(settings, user)
    configured = bool(authorized and settings.whatsapp_access_token and settings.whatsapp_phone_number_id)
    connectors = list(db.scalars(select(Connector).where(
        Connector.workspace_id == workspace_id, Connector.provider == "whatsapp_cloud"
    ).order_by(Connector.created_at, Connector.id)))
    evidence = []
    for row in connectors:
        readable = db.scalar(select(func.count()).select_from(Conversation).join(
            Permission, Permission.conversation_id == Conversation.id
        ).where(Conversation.connector_id == row.id, Permission.workspace_id == workspace_id,
                Permission.read.is_(True), Permission.retain.is_(True),
                Permission.expires_at.is_(None) | (Permission.expires_at > now())))
        received = db.scalar(select(func.count()).select_from(Message).where(
            Message.connector_id == row.id, Message.workspace_id == workspace_id,
            Message.origin == "live", Message.direction == "inbound"))
        delivered = db.scalar(select(func.count()).select_from(SendAttempt).join(
            Conversation, Conversation.id == SendAttempt.conversation_id
        ).where(Conversation.connector_id == row.id, SendAttempt.workspace_id == workspace_id,
                SendAttempt.status == "delivered"))
        evidence.append({**public(row, "account_id", "status", "capabilities", "fence"),
                         "lease_valid": bool(row.lease_expires_at and aware(row.lease_expires_at) > now()),
                         "lease_expires_at": aware(row.lease_expires_at).isoformat() if row.lease_expires_at else None,
                         "authorized_contacts": readable, "received_messages": received,
                         "delivered_messages": delivered,
                         "live_delivery_verified": bool(delivered)})
    missing = []
    for requirement, present in (("authorized_google_owner", authorized),
                                 ("business_phone_number", bool(settings.whatsapp_phone_number_id)),
                                 ("business_access_token", bool(settings.whatsapp_access_token)),
                                 ("webhook_app_secret", bool(settings.whatsapp_app_secret)),
                                 ("webhook_verify_token", bool(settings.whatsapp_verify_token))):
        if not present:
            missing.append(requirement)
    return {"provider": "whatsapp_cloud", "mode": "business_cloud", "configured": configured,
            "owner_authorized": authorized, "external_sends_enabled": settings.enable_external_sends,
            "webhook_configured": bool(settings.whatsapp_app_secret and settings.whatsapp_verify_token),
            "webhook_path": "/api/webhooks/whatsapp", "missing_requirements": missing,
            "connectors": evidence,
            "personal_account": {"supported": False, "history_import": "authorized_text_export"},
            "business_app_history": {"adapter_implemented": True, "live_verified": False,
                                     "eligible_setup_required": True, "maximum_days": 180,
                                     "groups_supported": False, "reference": COEXISTENCE_REFERENCE,
                                     "requirements": ["approved_meta_provider", "embedded_signup_coexistence",
                                                      "provider_history_sharing", "per_contact_read_retain_consent",
                                                      "sync_within_24_hours_of_onboarding"]},
            "automatic_read_all_chats": False, "groups_live_supported": False}


@router.post("/connect", status_code=201)
def connect_business(body: ConnectInput, request: Request,
                     user=Depends(get_current_user), db=Depends(get_db)):
    settings = request.app.state.settings
    require_whatsapp_owner(settings, user)
    if not settings.whatsapp_phone_number_id or not settings.whatsapp_access_token:
        raise HTTPException(409, "An eligible Business Cloud number and server-held access token are required")
    # The browser cannot choose a server-held account or impersonate its owner.
    return create_connector(body=ConnectorInput(workspace_id=body.workspace_id, provider="whatsapp_cloud",
                                               account_id=settings.whatsapp_phone_number_id,
                                               owner_sender_id=settings.whatsapp_phone_number_id),
                            request=request, user=user, db=db)


@router.post("/contacts", status_code=201)
@serialized_control
def authorize_contact(body: ContactInput, request: Request,
                      user=Depends(get_current_user), db=Depends(get_db)):
    require_whatsapp_owner(request.app.state.settings, user)
    connector = connector_for(db, user, body.connector_id)
    if connector.provider != "whatsapp_cloud":
        raise HTTPException(409, "Choose the owner's WhatsApp Business connector")
    row = db.scalar(select(Conversation).where(
        Conversation.connector_id == connector.id, Conversation.workspace_id == connector.workspace_id,
        Conversation.provider_chat_id == body.phone_number))
    if row is None:
        row = Conversation(workspace_id=connector.workspace_id, connector_id=connector.id,
                           provider_chat_id=body.phone_number, title=body.title, kind="contact")
        db.add(row)
        db.flush()
        grant = Permission(workspace_id=row.workspace_id, conversation_id=row.id)
        db.add(grant)
        db.flush()
    else:
        if row.kind != "contact":
            raise HTTPException(409, "Business Cloud contact routing cannot replace a group")
        grant = db.scalar(select(Permission).where(
            Permission.conversation_id == row.id, Permission.workspace_id == row.workspace_id))
        if grant is None:
            raise HTTPException(409, "Contact permission record needs repair")
    row.title = body.title
    row.recipient_opted_in = body.recipient_opted_in
    for field in ("read", "retain", "learn", "draft", "send"):
        setattr(grant, field, getattr(body, field))
    grant.share = False
    grant.expires_at = None
    grant.version += 1
    invalidate(db, row, "whatsapp_contact_consent_changed")
    if row.control_state not in {"HUMAN_TAKEOVER", "RECONNECT_REVIEW"}:
        row.control_state = "DRAFT_MODE" if body.draft else "READ_ONLY" if body.read else "AI_OFF"
    if not body.learn:
        for profile in db.scalars(select(StyleProfile).where(
                StyleProfile.conversation_id == row.id, StyleProfile.workspace_id == row.workspace_id)):
            db.delete(profile)
    audit(db, row.workspace_id, user.id, "whatsapp.contact_authorized", row.id,
          permission_version=grant.version)
    db.commit()
    return {"conversation": public(row, "workspace_id", "connector_id", "title", "kind", "control_state"),
            "permissions": public(grant, "read", "retain", "learn", "draft", "send", "share", "version"),
            "recipient_opted_in": row.recipient_opted_in, "history_recovered": False}


@router.post("/owner-authorship")
@serialized_control
def confirm_owner_authorship(body: ConfirmAuthorship, request: Request,
                             user=Depends(get_current_user), db=Depends(get_db)):
    require_whatsapp_owner(request.app.state.settings, user)
    conversation = conversation_for(db, user, body.conversation_id)
    connector = db.get(Connector, conversation.connector_id)
    if connector.provider != "whatsapp_cloud":
        raise HTTPException(409, "Choose Business app messages for this review")
    permission_for(db, conversation, "read")
    permission_for(db, conversation, "retain")
    rows = list(db.scalars(select(Message).where(
        Message.id.in_(set(body.message_ids)), Message.workspace_id == conversation.workspace_id,
        Message.conversation_id == conversation.id, Message.connector_id == connector.id)))
    if len(rows) != len(set(body.message_ids)):
        raise HTTPException(404, "Message not found in this contact")
    if any(row.direction != "outbound" or row.sender_id != connector.owner_sender_id
           or row.author_kind not in {"unknown_owner_outgoing", "human_owner"}
           or row.deleted for row in rows):
        raise HTTPException(409, "Only available Business app outgoing messages can be confirmed")
    from .native import message_available
    if any(not message_available(db, row) for row in rows):
        raise HTTPException(409, "Message evidence is no longer available")
    changed = 0
    for row in rows:
        if row.author_kind != "human_owner":
            row.author_kind = "human_owner"
            row.excluded_from_learning = False
            changed += 1
    if changed:
        invalidate(db, conversation, "owner_confirmed_message_authorship")
        db.flush()
        from .intelligence import refresh_style_from_messages
        refresh_style_from_messages(db, conversation)
        audit(db, conversation.workspace_id, user.id, "whatsapp.owner_authorship_confirmed", conversation.id,
              confirmed_count=changed)
    db.commit()
    return {"confirmed": changed, "conversation_id": conversation.id,
            "learning_requires_consent": True, "assistant_messages_confirmable": False}


@serialized_control
def _claim_business_history(body: SyncInput, request: Request, user, db):
    settings = request.app.state.settings
    require_whatsapp_owner(settings, user)
    row = connector_for(db, user, body.connector_id)
    if (row.provider != "whatsapp_cloud" or row.status != "connected"
            or row.account_id != settings.whatsapp_phone_number_id
            or not row.lease_expires_at or aware(row.lease_expires_at) <= now()
            or row.capabilities.get("business_app_coexistence") != "supported"):
        raise HTTPException(409, "Verify an eligible WhatsApp Business app Coexistence number first")
    if not settings.whatsapp_access_token or not settings.whatsapp_app_secret or not settings.whatsapp_verify_token:
        raise HTTPException(409, "Business credentials and the signed public webhook must be configured")
    eligible = db.scalar(select(Conversation.id).join(Permission).where(
        Conversation.connector_id == row.id, Conversation.workspace_id == row.workspace_id,
        Permission.read.is_(True), Permission.retain.is_(True),
        Permission.expires_at.is_(None) | (Permission.expires_at > now())).limit(1))
    if eligible is None:
        raise HTTPException(409, "Authorize read and retain for the intended contacts before history sync")
    try:
        endpoint = graph_endpoint(settings.whatsapp_api_version, row.account_id, "smb_app_data")
    except ValueError:
        raise HTTPException(409, "Configured Business API resource is invalid") from None
    saved = row.capabilities.get("business_history_request")
    if isinstance(saved, dict):
        return row.id, row.workspace_id, row.fence, endpoint, saved, True
    attempt = {"status": "submitting", "attempt_id": uid(), "requested_at": now().isoformat(),
               "provider_request_id": None}
    row.capabilities = {**row.capabilities, "business_history_request": attempt}
    audit(db, row.workspace_id, user.id, "whatsapp.history_sync_claimed", row.id)
    # Persist before provider submission. A crash or timeout is an unknown outcome,
    # never permission to repeat Meta's once-per-onboarding synchronization request.
    db.commit()
    return row.id, row.workspace_id, row.fence, endpoint, attempt, False


@router.post("/history-sync")
def sync_business_history(body: SyncInput, request: Request,
                          user=Depends(get_current_user), db=Depends(get_db)):
    settings = request.app.state.settings
    connector_id, workspace_id, fence, endpoint, attempt, existing = _claim_business_history(
        body=body, request=request, user=user, db=db)
    if existing:
        db.rollback()
        return {**attempt, "resubmitted": False, "history_sharing_verified": False}

    # Claim and final authority transactions are short. In particular the
    # process-local owner-control guard must end before a slow provider call.
    # Pausing, revoking chat access or disconnecting stays responsive during sync.
    from .storage_authority import lock_workspace
    from .messaging import submit_guard
    with submit_guard(workspace_id):
        lock_workspace(db, workspace_id)
        db.expire_all()
        row = db.get(Connector, connector_id)
        eligible = db.scalar(select(Conversation.id).join(Permission).where(
            Conversation.connector_id == connector_id, Conversation.workspace_id == workspace_id,
            Permission.read.is_(True), Permission.retain.is_(True),
            Permission.expires_at.is_(None) | (Permission.expires_at > now())).limit(1))
        current = (row is not None and row.status == "connected" and row.fence == fence
                   and row.account_id == settings.whatsapp_phone_number_id
                   and row.lease_expires_at and aware(row.lease_expires_at) > now()
                   and eligible is not None and whatsapp_owner_authorized(settings, user))
        if not current:
            attempt = {**attempt, "status": "cancelled", "error_code": "authority_changed"}
            if row is not None:
                row.capabilities = {**row.capabilities, "business_history_request": attempt}
            db.commit()
            return {**attempt, "resubmitted": False, "history_sharing_verified": False}
        db.commit()
    try:
        result = httpx.post(endpoint,
                            headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
                            json={"messaging_product": "whatsapp", "sync_type": "history"},
                            timeout=15, follow_redirects=False, trust_env=False)
        if 400 <= result.status_code < 500:
            attempt = {**attempt, "status": "failed", "error_code": f"provider_http_{result.status_code}"}
        elif result.status_code == 200:
            data = result.json()
            reference = data.get("request_id") if isinstance(data, dict) else None
            if not isinstance(reference, str) or not 1 <= len(reference) <= 180:
                raise ValueError("Missing provider request reference")
            attempt = {**attempt, "status": "accepted", "provider_request_id": reference}
        else:
            raise ValueError("Unknown provider acceptance")
    except (httpx.HTTPError, ValueError, TypeError):
        attempt = {**attempt, "status": "uncertain", "error_code": "provider_outcome_unknown"}
    lock_workspace(db, workspace_id)
    db.expire_all()
    row = db.get(Connector, connector_id)
    if row is None:
        db.rollback()
        return {**attempt, "resubmitted": False, "history_sharing_verified": False}
    row.capabilities = {**row.capabilities, "business_history_request": attempt}
    audit(db, row.workspace_id, user.id, "whatsapp.history_sync_result", row.id, status=attempt["status"])
    db.commit()
    return {**attempt, "resubmitted": False, "history_sharing_verified": False,
            "note": "Acceptance is not proof that history was shared or synchronized"}
