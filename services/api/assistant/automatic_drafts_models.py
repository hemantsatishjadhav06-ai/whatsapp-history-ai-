"""Explicit automatic draft authority and content-free generation ledger."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Entity, Tenant


class AutoDraftGrant(Entity, Tenant, Base):
    __tablename__ = "automatic_draft_grants"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), unique=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_drafts_per_hour: Mapped[int] = mapped_column(Integer, default=3)


class AutomaticDraftJob(Entity, Tenant, Base):
    __tablename__ = "automatic_draft_jobs"
    __table_args__ = (
        UniqueConstraint("source_outbox_id", name="uq_automatic_draft_event"),
        UniqueConstraint("message_id", "message_revision", name="uq_automatic_draft_message_revision"),
        Index("ix_automatic_draft_claim", "status", "last_checked_at", "created_at", "id"),
        Index("ix_automatic_draft_owner_inflight", "owner_id", "status", "claim_expires_at"),
        Index("ix_automatic_draft_hourly", "grant_id", "started_at"),
    )
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    grant_id: Mapped[str] = mapped_column(ForeignKey("automatic_draft_grants.id"))
    source_outbox_id: Mapped[str] = mapped_column(String(36))
    event_id: Mapped[str] = mapped_column(String(200))
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    message_revision: Mapped[int] = mapped_column(Integer)
    authority_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class AutomaticDraftSlot(Base):
    """Four transient, content-free slots survive tenant erasure during a call."""
    __tablename__ = "automatic_draft_slots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    owner_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
