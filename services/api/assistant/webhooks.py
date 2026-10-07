"""Signature-verified ingestion for the official WhatsApp Cloud API.

Provider metadata identifies an existing connector. Ownership, conversation IDs,
and permissions are resolved locally; webhook JSON never grants tenant access.
Unsupported and unconsented input is acknowledged without retaining its text.
"""

import hashlib
import hmac
import json
from contextlib import ExitStack
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from .access import audit
from .db import aware, now
from .models import Connector, Conversation, Draft, Permission, SendAttempt
from .provider_authority import whatsapp_workspace_authorized
from .whatsapp_provider import normalize_phone

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
MAX_WEBHOOK_BYTES = 1_000_000


def _invalid() -> HTTPException:
    return HTTPException(400, "Malformed WhatsApp webhook")


def _string(value, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _invalid()
    return value


def _timestamp(value) -> datetime:
    # Meta transmits Unix seconds as strings. Reject floats and booleans rather
    # than guessing dates or silently truncating a malformed provider timestamp.
    if isinstance(value, str):
        if not value.isascii() or not value.isdecimal() or len(value) > 12:
            raise _invalid()
        value = int(value)
    if type(value) is not int or value < 0:
        raise _invalid()
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        raise _invalid() from None


def _list(value) -> list:
    if not isinstance(value, list):
        raise _invalid()
    return value


def _may_retain(db, conversation: Conversation) -> bool:
    permission = db.scalar(select(Permission).where(
        Permission.conversation_id == conversation.id,
        Permission.workspace_id == conversation.workspace_id,
    ))
    return bool(permission and permission.read and permission.retain and (
        permission.expires_at is None or aware(permission.expires_at) > now()
    ))


@router.get("/whatsapp")
def verify_whatsapp(request: Request):
    settings = request.app.state.settings
    if not settings.whatsapp_verify_token:
        raise HTTPException(503, "WhatsApp verification is not configured")
    supplied = request.query_params.get("hub.verify_token", "")
    valid = hmac.compare_digest(supplied.encode(), settings.whatsapp_verify_token.encode())
    if request.query_params.get("hub.mode") != "subscribe" or not valid:
        raise HTTPException(403, "Invalid WhatsApp verification")
    challenge = request.query_params.get("hub.challenge")
    if not challenge or len(challenge) > 1024:
        raise _invalid()
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(challenge)


async def _verified_payload(request: Request) -> dict:
    settings = request.app.state.settings
    if not settings.whatsapp_app_secret:
        raise HTTPException(503, "WhatsApp webhooks are not configured")
    # Stream with a limit even when Content-Length is absent or dishonest.
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "WhatsApp webhook is too large")
        chunks.extend(chunk)
    signature = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(
        settings.whatsapp_app_secret.encode(), bytes(chunks), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise HTTPException(401, "Invalid WhatsApp webhook signature")
    try:
        payload = json.loads(chunks)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise _invalid() from None
    if not isinstance(payload, dict):
        raise _invalid()
    return payload


def _receipt(db, connector: Connector, status: dict) -> bool:
    provider_id = _string(status.get("id"), 180)
    provider_status = _string(status.get("status"), 40)
    state = {"sent": "accepted", "delivered": "delivered", "read": "delivered", "failed": "failed"}.get(
        provider_status
    )
    if state is None:
        return False
    # Statuses must name a provider timestamp and a recipient, but the recorded
    # attempt (never the supplied recipient/tenant) determines the affected row.
    _timestamp(status.get("timestamp"))
    recipient = _string(status.get("recipient_id"), 160)
    attempt = db.scalar(select(SendAttempt).join(
        Conversation, Conversation.id == SendAttempt.conversation_id
    ).where(
        SendAttempt.provider_message_id == provider_id,
        SendAttempt.workspace_id == connector.workspace_id,
        Conversation.workspace_id == connector.workspace_id,
        Conversation.connector_id == connector.id,
        Conversation.provider_chat_id == recipient,
    ).with_for_update())
    if attempt is None:
        return False
    if attempt.status == "delivered" or attempt.status == state:
        return False
    if attempt.status not in {"dispatching", "uncertain", "accepted", "failed"}:
        return False
    # A delayed acceptance cannot undo a final failure. Delivery evidence can
    # resolve an uncertain attempt and dominates previously observed failure.
    if state == "accepted" and attempt.status == "failed":
        return False
    previous = attempt.status
    attempt.status = state
    if state == "failed":
        errors = status.get("errors", [])
        if not isinstance(errors, list):
            raise _invalid()
        code = errors[0].get("code") if errors and isinstance(errors[0], dict) else None
        attempt.error_code = str(code)[:80] if isinstance(code, (str, int)) else "provider_failure"
    else:
        attempt.error_code = None
    draft = db.scalar(select(Draft).where(
        Draft.id == attempt.draft_id,
        Draft.workspace_id == connector.workspace_id,
        Draft.conversation_id == attempt.conversation_id,
    ))
    if draft is not None and draft.status in {"dispatching", "uncertain", "accepted", "failed"}:
        draft.status = state
    audit(db, connector.workspace_id, "whatsapp_webhook", "send.receipt", attempt.id,
          previous_status=previous, status=state)
    return True


def _message(db, connector: Connector, message: dict, *, origin="live", direction="inbound", chat_id=None) -> bool:
    if message.get("type") != "text":
        return False
    provider_id = _string(message.get("id"), 180)
    sender_id = _string(message.get("from"), 160)
    chat_id = chat_id or sender_id
    if ((direction == "inbound" and sender_id != chat_id)
            or (direction == "outbound" and sender_id != connector.owner_sender_id)):
        return False
    timestamp = _timestamp(message.get("timestamp"))
    content = message.get("text")
    if not isinstance(content, dict):
        raise _invalid()
    text = _string(content.get("body"), 10000)
    context = message.get("context", {})
    if not isinstance(context, dict):
        raise _invalid()
    reply_to = context.get("id")
    if reply_to is not None:
        reply_to = _string(reply_to, 180)
    conversation = db.scalar(select(Conversation).where(
        Conversation.connector_id == connector.id,
        Conversation.workspace_id == connector.workspace_id,
        Conversation.provider_chat_id == chat_id,
        Conversation.kind == "contact",
    ))
    if conversation is None or not _may_retain(db, conversation):
        return False
    # Avoid a circular import: messaging owns the shared canonical ingest path.
    from .messaging import CanonicalEvent, ingest_event

    event_id = "whatsapp:message:" + hashlib.sha256(
        f"{connector.id}\0{provider_id}".encode()
    ).hexdigest()
    event = CanonicalEvent(
        schema_version=1,
        event_id=event_id,
        workspace_id=connector.workspace_id,
        connector_id=connector.id,
        conversation_id=conversation.id,
        provider=connector.provider,
        account_id=connector.account_id,
        provider_message_id=provider_id,
        sender_id=sender_id,
        direction=direction,
        origin=origin,
        event_type="message.created",
        # A Business app echo proves an outgoing device message, not which human
        # operator typed it. The authenticated owner reviews authorship separately.
        author_kind="contact_human" if direction == "inbound" else "unknown_owner_outgoing",
        provider_timestamp=timestamp,
        content={"type": "text", "text": text},
        reply_to=reply_to,
        source_revision=1,
    )
    result = ingest_event(db, event)
    return result.get("status") in {"accepted", "duplicate"}


def _coexistence_ready(connector: Connector, value: dict) -> bool:
    if connector.capabilities.get("business_app_coexistence") != "supported":
        return False
    phone = value["metadata"].get("display_phone_number")
    if not isinstance(phone, str):
        return False
    try:
        return normalize_phone(phone) == connector.owner_sender_id
    except ValueError:
        return False


def _history(db, connector: Connector, value: dict) -> tuple[int, int]:
    accepted = ignored = 0
    for chunk in _list(value.get("history")):
        if not isinstance(chunk, dict):
            raise _invalid()
        errors = chunk.get("errors")
        if errors is not None:
            for error in _list(errors):
                if not isinstance(error, dict):
                    raise _invalid()
                if error.get("code") == 2593109:
                    connector.capabilities = {**connector.capabilities, "business_history_sharing": "declined"}
            ignored += 1
            continue
        metadata = chunk.get("metadata")
        if (not isinstance(metadata, dict) or type(metadata.get("phase")) is not int
                or metadata["phase"] not in {0, 1, 2} or type(metadata.get("chunk_order")) is not int
                or metadata["chunk_order"] < 0 or type(metadata.get("progress")) is not int
                or not 0 <= metadata["progress"] <= 100):
            raise _invalid()
        for thread in _list(chunk.get("threads")):
            if not isinstance(thread, dict):
                raise _invalid()
            chat_id = _string(thread.get("id"), 160)
            for message in _list(thread.get("messages")):
                if not isinstance(message, dict):
                    raise _invalid()
                direction = "outbound" if message.get("from") == connector.owner_sender_id else "inbound"
                if _message(db, connector, message, origin="history", direction=direction, chat_id=chat_id):
                    accepted += 1
                else:
                    ignored += 1
        # This is an observed chunk's progress, not a completion guarantee:
        # provider phases can be absent and chunks can arrive out of order.
        connector.capabilities = {**connector.capabilities, "business_history_sharing": "observed",
                                  "business_history_last_chunk": {
                                      "phase": metadata["phase"], "chunk_order": metadata["chunk_order"],
                                      "progress": metadata["progress"], "observed_at": now().isoformat(),
                                      "completeness_verified": False}}
    return accepted, ignored


@router.post("/whatsapp")
async def receive_whatsapp(request: Request):
    payload = await _verified_payload(request)
    import anyio
    # History chunks can contain many messages. SQL/crypto work belongs in the
    # bounded request thread pool, keeping the event loop available for controls.
    return await anyio.to_thread.run_sync(_receive_payload, payload, request)


def _receive_payload(payload: dict, request: Request):
    if payload.get("object") != "whatsapp_business_account":
        return {"status": "acknowledged", "accepted": 0, "receipts": 0, "ignored": 1}
    accepted = receipts = ignored = 0
    values = []
    for entry in _list(payload.get("entry")):
        if not isinstance(entry, dict):
            raise _invalid()
        for change in _list(entry.get("changes")):
            if not isinstance(change, dict):
                raise _invalid()
            field = change.get("field")
            if field not in {"messages", "history", "smb_message_echoes"}:
                ignored += 1
                continue
            value = change.get("value")
            if not isinstance(value, dict) or not isinstance(value.get("metadata"), dict):
                raise _invalid()
            account_id = _string(value["metadata"].get("phone_number_id"), 120)
            values.append((account_id, field, value))

    from .messaging import submit_guard

    with request.app.state.session_factory() as db:
        resolved = []
        for account_id, field, value in values:
            connector = db.scalar(select(Connector).where(
                Connector.provider == "whatsapp_cloud",
                Connector.account_id == account_id,
                Connector.status == "connected",
            ))
            if connector is None or not whatsapp_workspace_authorized(
                    db, request.app.state.settings, connector.workspace_id):
                ignored += 1
            else:
                resolved.append((connector.id, connector.workspace_id, account_id, field, value))
        # Drop the preliminary lookup transaction before waiting for guards.
        # All identifiers below came from SQL, never a supplied tenant field.
        db.rollback()
        with ExitStack() as guards:
            ordered_workspaces = sorted({item[1] for item in resolved})
            for workspace_id in ordered_workspaces:
                guards.enter_context(submit_guard(workspace_id))
            from .storage_authority import lock_workspace
            for workspace_id in ordered_workspaces:
                lock_workspace(db, workspace_id)
            db.expire_all()
            for connector_id, workspace_id, account_id, field, value in resolved:
                # A connector can disconnect while this webhook waits for a
                # local submit. Re-resolve its mapping under the held guards.
                connector = db.scalar(select(Connector).where(
                    Connector.id == connector_id,
                    Connector.workspace_id == workspace_id,
                    Connector.provider == "whatsapp_cloud",
                    Connector.account_id == account_id,
                    Connector.status == "connected",
                ))
                if connector is None or not whatsapp_workspace_authorized(
                        db, request.app.state.settings, connector.workspace_id):
                    ignored += 1
                    continue
                if field in {"history", "smb_message_echoes"}:
                    if not _coexistence_ready(connector, value):
                        ignored += 1
                        continue
                    if field == "history":
                        new_accepted, new_ignored = _history(db, connector, value)
                        accepted += new_accepted
                        ignored += new_ignored
                    else:
                        for message in _list(value.get("message_echoes")):
                            if not isinstance(message, dict):
                                raise _invalid()
                            chat_id = _string(message.get("to"), 160)
                            if _message(db, connector, message, direction="outbound", chat_id=chat_id):
                                accepted += 1
                            else:
                                ignored += 1
                    continue
                for message in _list(value.get("messages", [])):
                    if not isinstance(message, dict):
                        raise _invalid()
                    if _message(db, connector, message):
                        accepted += 1
                    else:
                        ignored += 1
                for status in _list(value.get("statuses", [])):
                    if not isinstance(status, dict):
                        raise _invalid()
                    if _receipt(db, connector, status):
                        receipts += 1
                    else:
                        ignored += 1
            # Mutation and durable commit share the same guarded boundary as
            # observed controls and the pilot's final external-submit decision.
            db.commit()
    return {"status": "acknowledged", "accepted": accepted, "receipts": receipts, "ignored": ignored}
