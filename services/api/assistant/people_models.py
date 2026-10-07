"""Private local address book and metadata-only workspace quota records."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, EncryptedText
from .models import Entity, Tenant


class LocalContact(Entity, Tenant, Base):
    __tablename__ = "local_contacts"
    __table_args__ = (UniqueConstraint("workspace_id", "connector_id", "provider_identity", name="uq_local_contact_identity"),)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    provider_identity: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str] = mapped_column(EncryptedText)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ContactSource(Entity, Tenant, Base):
    __tablename__ = "contact_sources"
    __table_args__ = (UniqueConstraint("contact_id", "message_id", name="uq_contact_source"),)
    contact_id: Mapped[str] = mapped_column(ForeignKey("local_contacts.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    source_revision: Mapped[int] = mapped_column(Integer)


class ContactSaveGrant(Entity, Tenant, Base):
    __tablename__ = "contact_save_grants"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class WorkspaceBudget(Entity, Tenant, Base):
    __tablename__ = "workspace_budgets"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_workspace_budget"),)
    max_actions_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tokens_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cost_microusd_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class UsageLedger(Entity, Tenant, Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (UniqueConstraint("workspace_id", "operation_key", name="uq_usage_operation"),)
    operation_key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(40))
    window_day: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(20), default="reserved")
    action_units: Mapped[int] = mapped_column(Integer, default=0)
    token_units: Mapped[int] = mapped_column(Integer, default=0)
    cost_microusd: Mapped[int] = mapped_column(Integer, default=0)
