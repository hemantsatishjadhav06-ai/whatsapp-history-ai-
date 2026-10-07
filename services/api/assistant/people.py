"""Exact-identity assistant-local contacts; no implicit external address-book writes."""

import re
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select

from .access import audit, conversation_for, permission_for, workspace_for
from .auth import get_current_user
from .core import serialized_control
from .db import aware, get_db, now
from .models import Connector, Conversation, Message, Permission, Suppression
from .people_models import ContactSaveGrant, ContactSource, LocalContact

router = APIRouter(tags=["people"])
DESTINATIONS = {"assistant_local": "supported", "whatsapp": "unavailable",
                "google_contacts": "unavailable", "phone_os": "unavailable"}
# Identity strings are retained exactly, within the connector's namespace. Names
# from a text export cannot pass this check and cannot merge people accidentally.
IDENTITY = re.compile(r"(?:\+?[0-9]{7,15}|[0-9]{5,30}@(s\.whatsapp\.net|c\.us|lid))\Z")


class ContactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    source_message_id: str
    destination: Literal["assistant_local", "whatsapp", "google_contacts", "phone_os"] = "assistant_local"
    display_name: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("display_name")
    @classmethod
    def nonblank_name(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Display name must not be blank")
        return value.strip() if value is not None else None


class ContactGrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Expiry requires an explicit timezone offset")
        return aware(value)


def source_valid(db, conversation, source):
    from .native import message_available, message_context_for, native_record_for
    connector = db.get(Connector, conversation.connector_id)
    context = message_context_for(db, source)
    origin_verified = (source.origin in {"live", "backfill"}
                       or (source.origin == "history" and native_record_for(db, source) is not None))
    if (source.workspace_id != conversation.workspace_id or source.conversation_id != conversation.id
            or source.connector_id != conversation.connector_id or source.deleted
            or not origin_verified
            or source.author_kind not in {"contact_human", "contact"}
            or source.direction != "inbound" or not message_available(db, source)
            or (context and context.sender_identity and context.sender_identity.get("id") != source.sender_id)
            or not connector or connector.workspace_id != conversation.workspace_id
            or connector.provider == "export_only" or source.sender_id == connector.owner_sender_id
            or not IDENTITY.fullmatch(source.sender_id)):
        return False
    blocked = {key for row in db.scalars(select(Suppression).where(
        Suppression.workspace_id == conversation.workspace_id,
        Suppression.conversation_id == conversation.id,
    )) for key in row.source_message_ids}
    return source.id not in blocked


def upsert_local_contact(db, conversation, source, *, display_name=None):
    """Caller must hold current read/retain scope and the local-save authority."""
    if not source_valid(db, conversation, source):
        raise HTTPException(409, {"code": "SOURCE_MISSING", "message": "A verified sender identity from an available live/backfill record is required"})
    row = db.scalar(select(LocalContact).where(
        LocalContact.workspace_id == conversation.workspace_id,
        LocalContact.connector_id == conversation.connector_id,
        LocalContact.provider_identity == source.sender_id,
    ))
    created = row is None
    if row is None:
        # A chat title is not a verified contact name. Only the owner supplies a
        # display name; otherwise expose the exact provider identity.
        name = display_name or source.sender_id
        row = LocalContact(workspace_id=conversation.workspace_id, connector_id=conversation.connector_id,
                           provider_identity=source.sender_id, display_name=name, version=1)
        db.add(row)
        db.flush()
    elif display_name is not None and display_name != row.display_name:
        row.display_name = display_name
        row.version += 1
    existing = db.scalar(select(ContactSource).where(ContactSource.contact_id == row.id,
                                                    ContactSource.conversation_id == conversation.id)
                         .order_by(ContactSource.created_at.desc()).limit(1))
    if existing is None:
        db.add(ContactSource(workspace_id=conversation.workspace_id, contact_id=row.id,
                             conversation_id=conversation.id, message_id=source.id,
                             source_revision=source.revision))
    else:
        previous = db.get(Message, existing.message_id)
        if previous and aware(previous.received_at) > aware(source.received_at) and source_valid(db, conversation, previous):
            return row, created
        existing.message_id = source.id
        existing.source_revision = source.revision
    return row, created


def contact_json(row):
    return {"id": row.id, "workspace_id": row.workspace_id, "connector_id": row.connector_id,
            "provider_identity": row.provider_identity, "display_name": row.display_name,
            "version": row.version, "saved_destination": "assistant_local", "external_write": False}


@router.get("/contacts/destinations")
def contact_destinations(user=Depends(get_current_user)):
    return {"destinations": DESTINATIONS, "external_contact_authorization": "not_configured"}


@router.post("/contacts", status_code=201)
@serialized_control
def save_contact(body: ContactInput, user=Depends(get_current_user), db=Depends(get_db)):
    conversation = conversation_for(db, user, body.conversation_id)
    for capability in ("read", "retain"):
        permission_for(db, conversation, capability)
    if body.destination != "assistant_local":
        raise HTTPException(409, {"code": "CAPABILITY_UNAVAILABLE", "destination": body.destination,
                                  "message": "No external address-book destination has been authorized and verified"})
    source = db.get(Message, body.source_message_id)
    if source is None:
        raise HTTPException(404, "Source message not found")
    row, created = upsert_local_contact(db, conversation, source, display_name=body.display_name)
    audit(db, conversation.workspace_id, user.id, "contact.local_saved", row.id,
          conversation_id=conversation.id, destination="assistant_local", created=created)
    db.commit()
    return contact_json(row)


def readable_sources(db, workspace_id):
    readable = select(Permission.conversation_id).where(
        Permission.workspace_id == workspace_id, Permission.read.is_(True), Permission.retain.is_(True),
        (Permission.expires_at.is_(None) | (Permission.expires_at > now())),
    )
    return select(ContactSource).where(ContactSource.workspace_id == workspace_id,
                                        ContactSource.conversation_id.in_(readable))


@router.get("/people")
@router.get("/contacts")
def list_contacts(workspace_id: str, limit: int = Query(100, ge=1, le=200),
                  user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    ids = set()
    for ref in db.scalars(readable_sources(db, workspace_id)):
        conversation = db.get(Conversation, ref.conversation_id)
        source = db.get(Message, ref.message_id)
        if (conversation and source and source.revision == ref.source_revision
                and source_valid(db, conversation, source)):
            ids.add(ref.contact_id)
    return [contact_json(row) for row in db.scalars(select(LocalContact).where(
        LocalContact.workspace_id == workspace_id, LocalContact.id.in_(ids),
    ).order_by(LocalContact.created_at.desc(), LocalContact.id).limit(limit))]


@router.put("/conversations/{conversation_id}/contact-save-grant")
@serialized_control
def set_contact_grant(conversation_id: str, body: ContactGrantInput,
                      user=Depends(get_current_user), db=Depends(get_db)):
    conversation = conversation_for(db, user, conversation_id)
    if body.enabled:
        for capability in ("read", "retain"):
            permission_for(db, conversation, capability)
        if not now() < body.expires_at <= now() + timedelta(days=30):
            raise HTTPException(422, "Local contact-save grant expiry must be within 30 days")
        connector = db.get(Connector, conversation.connector_id)
        if connector.provider == "export_only":
            raise HTTPException(409, "Text exports cannot verify live contact identities")
    row = db.scalar(select(ContactSaveGrant).where(ContactSaveGrant.conversation_id == conversation.id))
    if row is None:
        row = ContactSaveGrant(workspace_id=conversation.workspace_id, conversation_id=conversation.id,
                               enabled=body.enabled, expires_at=body.expires_at, granted_at=now(), version=1)
        db.add(row)
    else:
        row.enabled = body.enabled
        row.expires_at = body.expires_at
        row.granted_at = now()
        row.version += 1
    audit(db, conversation.workspace_id, user.id, "contact.grant_changed", conversation.id,
          enabled=body.enabled, destination="assistant_local")
    db.commit()
    return {"conversation_id": conversation.id, "enabled": row.enabled, "expires_at": row.expires_at,
            "version": row.version, "destination": "assistant_local"}


def auto_save_contact(db, conversation, source):
    """Trusted ingestion hook: local saves continue while external actions pause."""
    row = db.scalar(select(ContactSaveGrant).where(ContactSaveGrant.workspace_id == conversation.workspace_id,
                                                  ContactSaveGrant.conversation_id == conversation.id))
    if not row or not row.enabled or aware(row.expires_at) <= now() or aware(source.received_at) < aware(row.granted_at):
        return None
    try:
        for capability in ("read", "retain"):
            permission_for(db, conversation, capability)
        contact, created = upsert_local_contact(db, conversation, source)
    except HTTPException:
        return None
    if created:
        audit(db, conversation.workspace_id, "contact-save-grant", "contact.local_saved", contact.id,
              conversation_id=conversation.id, destination="assistant_local", created=True)
    return contact


@router.post("/conversations/{conversation_id}/contacts/sync")
@serialized_control
def sync_local_contacts(conversation_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    conversation = conversation_for(db, user, conversation_id)
    for capability in ("read", "retain"):
        permission_for(db, conversation, capability)
    grant = db.scalar(select(ContactSaveGrant).where(ContactSaveGrant.conversation_id == conversation.id))
    if not grant or not grant.enabled or aware(grant.expires_at) <= now():
        raise HTTPException(403, "A current assistant-local contact-save grant is required")
    contacts = set()
    rows = db.scalars(select(Message).where(Message.workspace_id == conversation.workspace_id,
                                            Message.conversation_id == conversation.id,
                                            Message.received_at >= grant.granted_at)
                      .order_by(Message.received_at.desc(), Message.id).limit(200))
    for source in rows:
        contact = auto_save_contact(db, conversation, source)
        if contact:
            contacts.add(contact.id)
    db.commit()
    return {"destination": "assistant_local", "saved_count": len(contacts), "external_write": False,
            "scan_limit": 200}


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.get(LocalContact, contact_id)
    if row is None:
        raise HTTPException(404, "Contact not found")
    workspace_for(db, user, row.workspace_id)
    # Disable local automation in all contributing chats so deletion is sticky.
    conversation_ids = list(db.scalars(select(ContactSource.conversation_id).where(ContactSource.contact_id == row.id)))
    for grant in db.scalars(select(ContactSaveGrant).where(ContactSaveGrant.workspace_id == row.workspace_id,
                                                         ContactSaveGrant.conversation_id.in_(conversation_ids))):
        grant.enabled = False
        grant.version += 1
    db.execute(delete(ContactSource).where(ContactSource.contact_id == row.id))
    db.delete(row)
    audit(db, row.workspace_id, user.id, "contact.local_deleted", row.id)
    db.commit()
    return Response(status_code=204)


def purge_people_data(db, conversation_id):
    refs = list(db.scalars(select(ContactSource).where(ContactSource.conversation_id == conversation_id)))
    contact_ids = {ref.contact_id for ref in refs}
    db.execute(delete(ContactSource).where(ContactSource.conversation_id == conversation_id))
    db.execute(delete(ContactSaveGrant).where(ContactSaveGrant.conversation_id == conversation_id))
    db.flush()
    for contact_id in contact_ids:
        if not db.scalar(select(ContactSource.id).where(ContactSource.contact_id == contact_id).limit(1)):
            db.execute(delete(LocalContact).where(LocalContact.id == contact_id))


def forget_people_sources(db, conversation_id, source_ids):
    """Remove contact derivations before retention/forget makes sources unavailable."""
    refs = list(db.scalars(select(ContactSource).where(ContactSource.conversation_id == conversation_id,
                                                       ContactSource.message_id.in_(source_ids))))
    contact_ids = {ref.contact_id for ref in refs}
    db.execute(delete(ContactSource).where(ContactSource.conversation_id == conversation_id,
                                           ContactSource.message_id.in_(source_ids)))
    db.flush()
    for contact_id in contact_ids:
        if not db.scalar(select(ContactSource.id).where(ContactSource.contact_id == contact_id).limit(1)):
            db.execute(delete(LocalContact).where(LocalContact.id == contact_id))


def purge_workspace_people_data(db, workspace_id):
    from .people_models import UsageLedger, WorkspaceBudget
    for model in (ContactSource, ContactSaveGrant, LocalContact, UsageLedger, WorkspaceBudget):
        db.execute(delete(model).where(model.workspace_id == workspace_id))


def export_people_data(db, conversation_id):
    conversation = db.get(Conversation, conversation_id)
    ids = set()
    if conversation:
        for ref in db.scalars(select(ContactSource).where(ContactSource.conversation_id == conversation_id)):
            source = db.get(Message, ref.message_id)
            if source and source.revision == ref.source_revision and source_valid(db, conversation, source):
                ids.add(ref.contact_id)
    return {"contacts": [contact_json(row) for row in db.scalars(select(LocalContact).where(LocalContact.id.in_(ids)))],
            "contact_save_grants": [{"enabled": row.enabled, "expires_at": row.expires_at,
                                     "version": row.version, "destination": "assistant_local"}
                                    for row in db.scalars(select(ContactSaveGrant).where(
                                        ContactSaveGrant.conversation_id == conversation_id))]}
