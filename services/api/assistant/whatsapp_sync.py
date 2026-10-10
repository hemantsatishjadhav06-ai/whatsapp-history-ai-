"""Full personal WhatsApp sync: every one-to-one chat, live and history.

The private session service streams batches of chat metadata and messages. Python
stays the authority: every batch rechecks the connector fence, account and lease,
applies the owner's import preference, and never grants sending. History is stored
in bulk without per-message automation or audit rows; live messages keep the
canonical ingest path (phone takeover, drafts, outbox).
"""
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from .access import audit, workspace_for
from .auth import get_current_user
from .db import aware, get_db, now, uid
from .messaging import (PENDING_DRAFTS, CanonicalEvent, content_hash, ingest_event, invalidate_conversation,
                        require_session_service, submit_guard)
from .models import Connector, Conversation, Draft, Message, MessageEvent, Permission, SendAttempt, User, Workspace
from .storage_authority import lock_workspace
from .whatsapp_personal import JID, PREFIX, PROVIDER, Identity, configured, current_identity, require_owner
from .whatsapp_personal_models import (PersonalAccountAlias, PersonalChatAlias, PersonalChatSync,
                                       PersonalSyncPreference, PersonalWhatsAppSession)

router = APIRouter(tags=["whatsapp-personal-sync"])
MAX_SYNC_CHATS = 5000
MAX_BATCH_MESSAGES = 500
MAX_BATCH_CHATS = 1000
MAX_TEXT = 20000
MAX_BACKFILL_PAGES = 200
FULL_HISTORY_RAW_DAYS = 3650
TITLE_RANK = {"number": 0, "push": 1, "chat": 2, "verified": 3, "contact": 4}
STYLE_REFRESH_PER_BATCH = 2


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _jid(value):
    if not JID.fullmatch(value):
        raise ValueError("A canonical one-to-one WhatsApp JID is required")
    return value


def _zoned(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps require an explicit timezone")
    return aware(value)


Jid = Annotated[str, Field(min_length=5, max_length=160), AfterValidator(_jid)]
Zoned = Annotated[datetime, AfterValidator(_zoned)]


class SyncChat(Payload):
    jid: Jid
    alt_jid: Jid | None = None
    title: str | None = Field(default=None, max_length=160)
    title_source: Literal["contact", "verified", "chat", "push"] | None = None
    unread_count: int | None = Field(default=None, ge=0, le=1_000_000)
    archived: bool | None = None
    # Address-book entries and live chat events describe existing chats; only real chats create conversations.
    contact_only: bool = False
    last_activity_at: Zoned | None = None


class SyncMessage(Payload):
    id: str = Field(min_length=1, max_length=180)
    chat_jid: Jid
    chat_alt_jid: Jid | None = None
    from_me: bool
    timestamp: Zoned
    event: Literal["created", "edited", "deleted"] = "created"
    kind: Literal["text", "media", "location", "contact", "poll", "event", "call", "other"] = "text"
    text: str = Field(default="", max_length=MAX_TEXT)
    push_name: str | None = Field(default=None, max_length=160)
    reply_to: str | None = Field(default=None, max_length=180)
    revision: int = Field(default=1, ge=1, le=2_147_483_647)


class SyncProgress(Payload):
    phase: Literal["initial", "recent", "full", "on_demand", "push_name", "complete", "live"]
    percent: int | None = Field(default=None, ge=0, le=100)


class SyncBatch(Identity):
    origin: Literal["live", "history", "replay", "backfill"]
    chats: list[SyncChat] = Field(default_factory=list, max_length=MAX_BATCH_CHATS)
    messages: list[SyncMessage] = Field(default_factory=list, max_length=MAX_BATCH_MESSAGES)
    progress: SyncProgress | None = None


class BackfillReport(Payload):
    jid: Jid
    outcome: Literal["requested", "exhausted", "failed"]


class BackfillInput(Identity):
    reports: list[BackfillReport] = Field(default_factory=list, max_length=50)
    limit: int = Field(default=5, ge=0, le=50)


class PreferenceInput(Payload):
    workspace_id: str = Field(min_length=36, max_length=36)
    import_mode: Literal["all", "selected"]


def import_mode(db, workspace_id):
    row = db.get(PersonalSyncPreference, workspace_id)
    return row.import_mode if row else "all"


def keep_full_history(db, connector):
    """A full WhatsApp mirror would be wiped by the 30-day raw default; keep it unless the owner chose."""
    from .lifecycle import policy_for
    if import_mode(db, connector.workspace_id) != "all":
        return
    policy = policy_for(db, connector.workspace_id)
    if policy.version <= 1 and policy.raw_days == 30:
        policy.raw_days = FULL_HISTORY_RAW_DAYS
        policy.version += 1
        audit(db, connector.workspace_id, "system", "privacy.retention_changed", policy.id,
              version=policy.version, raw_days=policy.raw_days, reason="whatsapp_full_history")


def readable(db, conversation):
    permission = db.scalar(select(Permission).where(Permission.conversation_id == conversation.id,
                                                    Permission.workspace_id == conversation.workspace_id))
    return bool(permission and permission.read and permission.retain
                and (permission.expires_at is None or aware(permission.expires_at) > now()))


def number_title(jid):
    user = jid.split("@", 1)[0]
    return f"+{user}" if jid.endswith("@s.whatsapp.net") else "WhatsApp contact"


class ChatResolver:
    """Resolve phone-number and LID addresses to one conversation, creating it when importing all."""

    def __init__(self, db, connector, mode):
        self.db, self.connector, self.mode = db, connector, mode
        self.cache = {}
        self.readable = {}
        self.states = {}
        self.created = 0
        self.owner = {connector.account_id, *db.scalars(select(PersonalAccountAlias.alias_id).where(
            PersonalAccountAlias.connector_id == connector.id))}
        self.count = None

    def _find(self, jids):
        db, row = self.db, self.connector
        for jid in jids:
            if jid in self.cache:
                return self.cache[jid]
        alias = db.scalar(select(PersonalChatAlias).where(PersonalChatAlias.connector_id == row.id,
                                                         PersonalChatAlias.alias_jid.in_(jids)).limit(1))
        if alias:
            return db.get(Conversation, alias.conversation_id)
        return db.scalar(select(Conversation).where(Conversation.connector_id == row.id,
                                                   Conversation.workspace_id == row.workspace_id,
                                                   Conversation.provider_chat_id.in_(jids)).limit(1))

    def resolve(self, jid, alt=None, title=None, source=None, create=True):
        jids = [value for value in dict.fromkeys([jid, alt]) if value]
        if any(value in self.owner for value in jids):
            return None
        conv = self._find(jids)
        if conv is None:
            if self.mode != "all" or not create:
                return None
            if self.count is None:
                self.count = self.db.scalar(select(func.count()).select_from(Conversation).where(
                    Conversation.connector_id == self.connector.id))
            if self.count >= MAX_SYNC_CHATS:
                return None
            conv = self._create(jids, title, source)
            self.count += 1
        elif conv.workspace_id != self.connector.workspace_id or conv.kind != "contact":
            return None
        self._link(conv, jids)
        if title:
            self.retitle(conv, title, source or "push")
        number = next((value for value in jids if value.endswith("@s.whatsapp.net")), None)
        if number and conv.title == number_title(conv.provider_chat_id) != number_title(number):
            # A chat first seen only by its private WhatsApp ID shows the number once WhatsApp shares it.
            self.retitle(conv, number_title(number), "number")
        return conv

    def _create(self, jids, title, source):
        db, row = self.db, self.connector
        primary = next((value for value in jids if value.endswith("@s.whatsapp.net")), jids[0])
        conv = Conversation(id=uid(), workspace_id=row.workspace_id, connector_id=row.id, provider_chat_id=primary,
                            title=(title or number_title(primary))[:160], kind="contact", control_state="DRAFT_MODE")
        db.add(conv)
        db.flush()
        # Owner-chosen "all chats" import: read, keep and learn every one-to-one chat.
        # Sending is never granted here; it stays a per-chat owner decision.
        db.add(Permission(workspace_id=row.workspace_id, conversation_id=conv.id, read=True, retain=True,
                          learn=True, draft=True, send=False))
        sync = PersonalChatSync(workspace_id=row.workspace_id, connector_id=row.id, conversation_id=conv.id,
                                title_source=source if title else "number", unread_count=0, archived=False,
                                backfill_state="pending", backfill_requests=0, style_dirty=False, updated_at=now())
        db.add(sync)
        db.flush()
        self.states[conv.id] = sync
        self.created += 1
        return conv

    def _link(self, conv, jids):
        db, row = self.db, self.connector
        for jid in jids:
            if self.cache.get(jid) is conv:
                continue
            existing = db.get(PersonalChatAlias, (row.id, jid))
            if existing is None:
                db.add(PersonalChatAlias(connector_id=row.id, alias_jid=jid, workspace_id=row.workspace_id,
                                         conversation_id=conv.id))
                db.flush()
            elif existing.conversation_id != conv.id:
                continue  # An address already belongs to another chat; never move history between chats.
            self.cache[jid] = conv

    def can_store(self, conv):
        if conv.id not in self.readable:
            self.readable[conv.id] = readable(self.db, conv)
        return self.readable[conv.id]

    def state(self, conv):
        if conv.id in self.states:
            return self.states[conv.id]
        db, row = self.db, self.connector
        sync = db.scalar(select(PersonalChatSync).where(PersonalChatSync.conversation_id == conv.id))
        if sync is None:
            sync = PersonalChatSync(workspace_id=row.workspace_id, connector_id=row.id, conversation_id=conv.id,
                                    title_source="number", unread_count=0, archived=False,
                                    backfill_state="pending", backfill_requests=0, style_dirty=False,
                                    updated_at=now())
            db.add(sync)
            db.flush()
        self.states[conv.id] = sync
        return sync

    def retitle(self, conv, title, source):
        title = " ".join(title.split())[:160]
        if not title:
            return
        sync = self.state(conv)
        if TITLE_RANK.get(source, 0) >= TITLE_RANK.get(sync.title_source, 0) and conv.title != title:
            conv.title = title
            sync.title_source = source
            sync.updated_at = now()
        elif TITLE_RANK.get(source, 0) > TITLE_RANK.get(sync.title_source, 0):
            sync.title_source = source


def _author(mode, item, assistant_ids):
    if not item.from_me:
        return "contact_human"
    if item.id in assistant_ids:
        return "assistant"
    # In "all chats" mode the owner chose to mirror their own account: messages sent
    # from their phone are theirs. Selected mode keeps the stricter review flow.
    return "human_owner" if mode == "all" else "unknown_owner_outgoing"


def _record_anchor(sync, item):
    stamp = aware(item.timestamp)
    if sync.oldest_message_at is None or stamp < aware(sync.oldest_message_at):
        sync.oldest_message_at = stamp
        sync.oldest_provider_message_id = item.id
        sync.oldest_from_me = item.from_me
        return True
    return False


def _touch(conv, item):
    if conv.last_message_at is None or aware(item.timestamp) > aware(conv.last_message_at):
        conv.last_message_at = item.timestamp


def bulk_ingest(db, connector, resolver, messages, origin, stats):
    """History, replay and backfill: one transaction, no automation, no per-message audit."""
    stored_origin = "replay" if origin == "replay" else "history"
    groups = {}
    for item in messages:
        conv = resolver.resolve(item.chat_jid, item.chat_alt_jid,
                                None if item.from_me else item.push_name, "push")
        if conv is None or not resolver.can_store(conv):
            stats["ignored"] += 1
            continue
        groups.setdefault(conv.id, (conv, []))[1].append(item)
    outbound = [item.id for _, items in groups.values() for item in items if item.from_me]
    assistant_ids = set(db.scalars(select(SendAttempt.provider_message_id).where(
        SendAttempt.workspace_id == connector.workspace_id, SendAttempt.provider_message_id.in_(outbound)))) if outbound else set()
    received = now()
    for conv, items in groups.values():
        ids = list({item.id for item in items})
        existing = {row.provider_message_id: row for row in db.scalars(select(Message).where(
            Message.workspace_id == conv.workspace_id, Message.connector_id == connector.id,
            Message.conversation_id == conv.id, Message.provider_message_id.in_(ids)))}
        sync = resolver.state(conv)
        changed = False
        for item in sorted(items, key=lambda value: (value.timestamp, value.event != "created")):
            target = existing.get(item.id)
            if item.event == "created":
                text = item.text.strip()
                if target is not None or not text:
                    stats["duplicates" if target is not None else "ignored"] += 1
                    continue
                author = _author(resolver.mode, item, assistant_ids)
                message = Message(id=uid(), workspace_id=conv.workspace_id, connector_id=connector.id,
                                  conversation_id=conv.id, provider_message_id=item.id,
                                  sender_id=connector.owner_sender_id if item.from_me else conv.provider_chat_id,
                                  direction="outbound" if item.from_me else "inbound", origin=stored_origin,
                                  author_kind=author, text=item.text, provider_timestamp=item.timestamp,
                                  received_at=received, reply_to=item.reply_to, revision=1,
                                  excluded_from_learning=author != "human_owner" or item.kind != "text")
                db.add(message)
                db.add(MessageEvent(workspace_id=conv.workspace_id, conversation_id=conv.id, message_id=message.id,
                                    event_id=content_hash(f"{connector.id}\0{conv.id}\0"
                                                          + content_hash(f"{item.id}:message.created:1")),
                                    event_type="message.created", source_revision=1))
                existing[item.id] = message
                stats["accepted"] += 1
                changed = True
                _touch(conv, item)
                _record_anchor(sync, item)
                if author == "human_owner" and item.kind == "text":
                    sync.style_dirty = True
            elif target is not None and not target.deleted and item.revision > target.revision:
                event_type = "message.edited" if item.event == "edited" else "message.deleted"
                key = content_hash(f"{connector.id}\0{conv.id}\0" + content_hash(f"{item.id}:{event_type}:{item.revision}"))
                if db.scalar(select(MessageEvent.id).where(MessageEvent.event_id == key)):
                    stats["duplicates"] += 1
                    continue
                if item.event == "deleted":
                    target.text, target.deleted, target.excluded_from_learning = "", True, True
                elif item.text.strip():
                    target.text = item.text
                target.revision = item.revision
                db.add(MessageEvent(workspace_id=conv.workspace_id, conversation_id=conv.id, message_id=target.id,
                                    event_id=key, event_type=event_type, source_revision=item.revision))
                stats["accepted"] += 1
                changed = True
            else:
                stats["ignored"] += 1
        if changed:
            conv.revision += 1
            sync.updated_at = now()
            if db.scalar(select(Draft.id).where(Draft.conversation_id == conv.id,
                                                Draft.status.in_(PENDING_DRAFTS)).limit(1)):
                invalidate_conversation(db, conv, "personal_history_synced")


def live_ingest(db, connector, resolver, messages, stats):
    """Live messages keep canonical semantics: phone takeover, live eligibility and outbox."""
    outbound = [item.id for item in messages if item.from_me]
    assistant_ids = set(db.scalars(select(SendAttempt.provider_message_id).where(
        SendAttempt.workspace_id == connector.workspace_id, SendAttempt.provider_message_id.in_(outbound)))) if outbound else set()
    for item in sorted(messages, key=lambda value: value.timestamp):
        conv = resolver.resolve(item.chat_jid, item.chat_alt_jid,
                                None if item.from_me else item.push_name, "push")
        if conv is None or not resolver.can_store(conv) or (item.event == "created" and not item.text.strip()):
            stats["ignored"] += 1
            continue
        sync = resolver.state(conv)
        event_type = {"created": "message.created", "edited": "message.edited", "deleted": "message.deleted"}[item.event]
        revision = 1 if item.event == "created" else item.revision
        db.flush()
        result = ingest_event(db, CanonicalEvent(
            schema_version=1, event_id=content_hash(f"{item.id}:{event_type}:{revision}"),
            workspace_id=connector.workspace_id, connector_id=connector.id, conversation_id=conv.id,
            provider=PROVIDER, account_id=connector.account_id, provider_message_id=item.id,
            sender_id=connector.owner_sender_id if item.from_me else conv.provider_chat_id,
            direction="outbound" if item.from_me else "inbound", origin="live", event_type=event_type,
            author_kind=_author(resolver.mode, item, assistant_ids),
            provider_timestamp=item.timestamp, content={"type": "text", "text": item.text},
            reply_to=item.reply_to, source_revision=revision))
        status = result.get("status")
        stats["accepted" if status == "accepted" else "duplicates" if status == "duplicate" else "ignored"] += 1
        if status == "accepted" and item.event == "created":
            _record_anchor(sync, item)
            sync.unread_count = 0 if item.from_me else sync.unread_count + 1
            sync.updated_at = now()
            if item.kind != "text":
                message = db.get(Message, result.get("message_id"))
                if message is not None:
                    message.excluded_from_learning = True
            elif result.get("author_kind") == "human_owner":
                sync.style_dirty = True


def refresh_dirty_styles(db, connector, limit):
    from .intelligence import refresh_style_from_messages
    rows = list(db.scalars(select(PersonalChatSync).join(Conversation, Conversation.id == PersonalChatSync.conversation_id)
                           .where(PersonalChatSync.connector_id == connector.id, PersonalChatSync.style_dirty.is_(True))
                           .order_by(Conversation.last_message_at.desc().nulls_last(), PersonalChatSync.id).limit(limit)))
    for sync in rows:
        sync.style_dirty = False
        conv = db.get(Conversation, sync.conversation_id)
        if conv is not None and readable(db, conv):
            refresh_style_from_messages(db, conv)
    return len(rows)


def _session_owner(db, settings, row):
    owner = db.get(User, db.get(Workspace, row.workspace_id).owner_id)
    require_owner(settings, owner)


@router.post("/internal/whatsapp-session-sync", dependencies=[Depends(require_session_service)])
def session_sync(body: SyncBatch, request: Request, db=Depends(get_db)):
    settings = request.app.state.settings
    if not configured(settings):
        raise HTTPException(503, "Personal WhatsApp is disabled")
    db.rollback()
    with submit_guard(body.workspace_id):
        lock_workspace(db, body.workspace_id)
        row, session = current_identity(db, body, connected=True)
        _session_owner(db, settings, row)
        if db.get(Workspace, row.workspace_id).paused:
            if body.origin in {"history", "backfill"}:
                # Keep the batch in the private buffer until the owner resumes.
                raise HTTPException(423, "Workspace is paused")
            return {"status": "ignored", "reason": "workspace_paused", "accepted": 0}
        mode = import_mode(db, row.workspace_id)
        resolver = ChatResolver(db, row, mode)
        stats = {"accepted": 0, "duplicates": 0, "ignored": 0}
        for chat in body.chats:
            conv = resolver.resolve(chat.jid, chat.alt_jid, chat.title, chat.title_source or "chat",
                                    create=not chat.contact_only)
            if conv is None:
                continue
            sync = resolver.state(conv)
            if chat.unread_count is not None:
                sync.unread_count = chat.unread_count
            if chat.archived is not None:
                sync.archived = chat.archived
            if chat.last_activity_at is not None and conv.last_message_at is None:
                # Until its messages arrive, a chat sorts by its WhatsApp activity, not by import time.
                conv.last_message_at = chat.last_activity_at
            sync.updated_at = now()
        if body.origin == "live":
            live_ingest(db, row, resolver, body.messages, stats)
        else:
            bulk_ingest(db, row, resolver, body.messages, body.origin, stats)
        if body.progress is not None:
            session.sync_phase = body.progress.phase
            session.sync_progress = body.progress.percent
        session.last_sync_at = now()
        if resolver.created:
            audit(db, row.workspace_id, "system", "whatsapp.personal.chats_imported", row.id,
                  count=resolver.created, origin=body.origin)
        refreshed = refresh_dirty_styles(db, row, STYLE_REFRESH_PER_BATCH if body.origin != "live" else 1)
        db.commit()
    return {"status": "accepted", **stats, "chats_created": resolver.created, "styles_refreshed": refreshed}


@router.post("/internal/whatsapp-session-backfill", dependencies=[Depends(require_session_service)])
def session_backfill(body: BackfillInput, request: Request, db=Depends(get_db)):
    """Older history, newest chats first: report outcomes, receive the next anchors."""
    settings = request.app.state.settings
    if not configured(settings):
        raise HTTPException(503, "Personal WhatsApp is disabled")
    db.rollback()
    with submit_guard(body.workspace_id):
        lock_workspace(db, body.workspace_id)
        row, _ = current_identity(db, body, connected=True)
        _session_owner(db, settings, row)
        resolver = ChatResolver(db, row, "selected")
        for report in body.reports:
            conv = resolver.resolve(report.jid)
            if conv is None:
                continue
            sync = resolver.state(conv)
            if report.outcome == "exhausted":
                sync.backfill_state = "complete"
            elif report.outcome == "requested":
                sync.backfill_requests += 1
                if sync.backfill_requests >= MAX_BACKFILL_PAGES:
                    sync.backfill_state = "complete"
            sync.updated_at = now()
        targets = []
        if body.limit and not db.get(Workspace, row.workspace_id).paused:
            rows = db.execute(select(PersonalChatSync, Conversation).join(
                Conversation, Conversation.id == PersonalChatSync.conversation_id).join(
                Permission, Permission.conversation_id == Conversation.id).where(
                PersonalChatSync.connector_id == row.id, PersonalChatSync.backfill_state == "pending",
                PersonalChatSync.oldest_provider_message_id.is_not(None),
                Permission.read.is_(True), Permission.retain.is_(True),
                Permission.expires_at.is_(None) | (Permission.expires_at > now()))
                .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id).limit(body.limit))
            targets = [{"jid": conv.provider_chat_id, "oldest_id": sync.oldest_provider_message_id,
                        "oldest_from_me": bool(sync.oldest_from_me),
                        "oldest_at": aware(sync.oldest_message_at).isoformat()} for sync, conv in rows]
        refreshed = refresh_dirty_styles(db, row, 3)
        db.commit()
    return {"status": "accepted", "targets": targets, "styles_refreshed": refreshed}


def _personal_connector(db, workspace_id):
    return db.scalar(select(Connector).where(Connector.workspace_id == workspace_id, Connector.provider == PROVIDER))


@router.get(PREFIX + "/sync")
def sync_status(workspace_id: str, request: Request, response: Response,
                user=Depends(get_current_user), db=Depends(get_db)):
    workspace_for(db, user, workspace_id)
    response.headers["Cache-Control"] = "private, no-store"
    row = _personal_connector(db, workspace_id)
    result = {"workspace_id": workspace_id, "import_mode": import_mode(db, workspace_id), "connector_id": None,
              "chats": 0, "messages": 0, "phase": None, "progress": None, "last_sync_at": None,
              "oldest_message_at": None, "backfill_pending": 0, "backfill_complete": 0}
    if row is None:
        return result
    session = db.scalar(select(PersonalWhatsAppSession).where(PersonalWhatsAppSession.connector_id == row.id))
    counts = dict(db.execute(select(PersonalChatSync.backfill_state, func.count()).where(
        PersonalChatSync.connector_id == row.id).group_by(PersonalChatSync.backfill_state)).all())
    oldest = db.scalar(select(func.min(PersonalChatSync.oldest_message_at)).where(PersonalChatSync.connector_id == row.id))
    result.update(connector_id=row.id,
                  chats=db.scalar(select(func.count()).select_from(Conversation).where(Conversation.connector_id == row.id)),
                  messages=db.scalar(select(func.count()).select_from(Message).where(
                      Message.connector_id == row.id, Message.deleted.is_(False))),
                  phase=session.sync_phase if session else None, progress=session.sync_progress if session else None,
                  last_sync_at=aware(session.last_sync_at).isoformat() if session and session.last_sync_at else None,
                  oldest_message_at=aware(oldest).isoformat() if oldest else None,
                  backfill_pending=counts.get("pending", 0), backfill_complete=counts.get("complete", 0))
    return result


@router.put(PREFIX + "/preferences")
def set_preferences(body: PreferenceInput, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    require_owner(request.app.state.settings, user)
    workspace_for(db, user, body.workspace_id)
    db.rollback()
    with submit_guard(body.workspace_id):
        lock_workspace(db, body.workspace_id)
        workspace_for(db, user, body.workspace_id)
        row = db.get(PersonalSyncPreference, body.workspace_id)
        if row is None:
            row = PersonalSyncPreference(workspace_id=body.workspace_id, import_mode=body.import_mode, updated_at=now())
            db.add(row)
        else:
            row.import_mode, row.updated_at = body.import_mode, now()
        audit(db, body.workspace_id, user.id, "whatsapp.personal.import_mode_changed", body.workspace_id,
              import_mode=body.import_mode)
        db.commit()
    return {"workspace_id": body.workspace_id, "import_mode": body.import_mode}
