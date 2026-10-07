"""Private provider originals and observed owner reactions, separate from text imports."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, EncryptedJSON
from .models import Entity, Tenant


class MessageContext(Entity, Tenant, Base):
    __tablename__ = "message_contexts"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), unique=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    sender_identity: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)
    participant_identity: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_addressed: Mapped[bool] = mapped_column(Boolean, default=False)
    view_once: Mapped[bool] = mapped_column(Boolean, default=False)


class NativeRecord(Entity, Tenant, Base):
    __tablename__ = "native_records"
    __table_args__ = (UniqueConstraint("connector_id", "provider_record_ref",
                                     name="uq_native_provider_ref"),)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), unique=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    provider_record_ref: Mapped[str] = mapped_column(String(180))
    provider_message_id: Mapped[str] = mapped_column(String(180))
    account_id: Mapped[str] = mapped_column(String(120))
    provider_chat_id: Mapped[str] = mapped_column(String(160))
    source_revision: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_addressed: Mapped[bool] = mapped_column(Boolean, default=False)
    view_once: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class ReactionExample(Entity, Tenant, Base):
    __tablename__ = "reaction_examples"
    __table_args__ = (UniqueConstraint("connector_id", "event_key", name="uq_reaction_event"),)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    event_key: Mapped[str] = mapped_column(String(64))
    target_revision: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(String(160))
    author_kind: Mapped[str] = mapped_column(String(40))
    emoji: Mapped[str] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(30))
    provider_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    learn_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    handled: Mapped[bool] = mapped_column(Boolean, default=False)
