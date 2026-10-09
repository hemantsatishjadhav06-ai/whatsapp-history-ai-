"""Personal WhatsApp session metadata; Signal credentials stay Node-owned."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Entity, Tenant


class PersonalWhatsAppSession(Entity, Tenant, Base):
    __tablename__ = "whatsapp_personal_sessions"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_personal_session_workspace"),
                      UniqueConstraint("connector_id", name="uq_personal_session_connector"))
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="starting")
    connector_fence: Mapped[int] = mapped_column(Integer, default=1)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # SHA-256 of the owner-entered international number; a linked account must match it.
    expected_account_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Content-free history sync progress reported by the session service.
    sync_phase: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sync_progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PersonalSyncPreference(Base):
    """Owner choice for which personal WhatsApp chats are imported. Absent means "all"."""
    __tablename__ = "wa_personal_preferences"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    import_mode: Mapped[str] = mapped_column(String(20), default="all")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonalChatAlias(Base):
    """Maps every JID WhatsApp uses for one contact (phone number and LID) to one conversation."""
    __tablename__ = "wa_personal_chat_aliases"
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), primary_key=True)
    alias_jid: Mapped[str] = mapped_column(String(160), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)


class PersonalChatSync(Entity, Tenant, Base):
    """Per-chat sync metadata: WhatsApp unread count, title source and the oldest stored
    message, which anchors on-demand backfill of older history (newest to oldest)."""
    __tablename__ = "wa_personal_chat_sync"
    __table_args__ = (UniqueConstraint("conversation_id", name="uq_personal_chat_sync_conversation"),
                      Index("ix_personal_chat_sync_backfill", "connector_id", "backfill_state", "updated_at"))
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    title_source: Mapped[str] = mapped_column(String(20), default="number")
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    oldest_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    oldest_provider_message_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    oldest_from_me: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    backfill_state: Mapped[str] = mapped_column(String(20), default="pending")
    backfill_requests: Mapped[int] = mapped_column(Integer, default=0)
    style_dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonalAuthKey(Base):
    """Opaque AES-GCM envelopes written and decrypted only by the session service."""
    __tablename__ = "wa_personal_auth_keys"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), primary_key=True)
    key_type: Mapped[str] = mapped_column(String(80), primary_key=True)
    key_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonalAccountAlias(Base):
    """Content-free PN/LID ownership tombstones prevent alias-based account reuse."""
    __tablename__ = "wa_personal_account_aliases"
    alias_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
