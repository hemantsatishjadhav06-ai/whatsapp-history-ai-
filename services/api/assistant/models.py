from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, EncryptedJSON, EncryptedText, now, uid


class Entity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Tenant:
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)


class User(Entity, Base):
    __tablename__ = "users"
    subject: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(120), default="Owner")


class SessionRecord(Entity, Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_expiry_page", "expires_at", "id"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginNonce(Entity, Base):
    __tablename__ = "login_nonces"
    __table_args__ = (Index("ix_login_nonces_expiry_page", "expires_at", "id"),)
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Workspace(Entity, Base):
    __tablename__ = "workspaces"
    __table_args__ = (Index("uq_workspace_creation_key", "owner_id", "creation_key_hash", unique=True),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    creation_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creation_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Kolkata")
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_generation: Mapped[int] = mapped_column(Integer, default=0)


class Connector(Entity, Tenant, Base):
    __tablename__ = "connectors"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", "account_id", name="uq_connector_workspace_account"),
        # Provider identities stay globally unique; owner-chosen export labels are per workspace.
        Index("uq_connector_provider_account", "provider", "account_id", unique=True,
              postgresql_where=text("NOT (provider = 'export_only')"),
              sqlite_where=text("NOT (provider = 'export_only')")),
    )
    provider: Mapped[str] = mapped_column(String(40))
    account_id: Mapped[str] = mapped_column(String(120))
    owner_sender_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), default="connected")
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    fence: Mapped[int] = mapped_column(Integer, default=1)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Conversation(Entity, Tenant, Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("connector_id", "provider_chat_id", name="uq_conversation_provider"),
                      Index("ix_conversations_activity", "workspace_id", "last_message_at", "id"))
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    provider_chat_id: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20), default="contact")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    control_epoch: Mapped[int] = mapped_column(Integer, default=0)
    control_state: Mapped[str] = mapped_column(String(30), default="DRAFT_MODE")
    recipient_opted_in: Mapped[bool] = mapped_column(Boolean, default=False)
    recipient_opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    group_send_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Newest observed message (any direction or origin); orders the inbox by recent activity.
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Permission(Entity, Tenant, Base):
    __tablename__ = "permissions"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), unique=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    retain: Mapped[bool] = mapped_column(Boolean, default=False)
    learn: Mapped[bool] = mapped_column(Boolean, default=False)
    draft: Mapped[bool] = mapped_column(Boolean, default=False)
    send: Mapped[bool] = mapped_column(Boolean, default=False)
    share: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Entity, Tenant, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "connector_id", "conversation_id", "provider_message_id", name="uq_message_key"),
        Index("ix_messages_read_page", "workspace_id", "conversation_id", "provider_timestamp", "id",
              postgresql_where=text("deleted IS FALSE"), sqlite_where=text("deleted IS 0")),
        Index("ix_messages_retention_page", "workspace_id", "received_at", "id",
              postgresql_where=text("deleted IS FALSE"), sqlite_where=text("deleted IS 0")),
    )
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    provider_message_id: Mapped[str] = mapped_column(String(180))
    sender_id: Mapped[str] = mapped_column(String(160))
    direction: Mapped[str] = mapped_column(String(20))
    origin: Mapped[str] = mapped_column(String(30))
    author_kind: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(EncryptedText)
    provider_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    reply_to: Mapped[str | None] = mapped_column(String(180), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    excluded_from_learning: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class MessageEvent(Entity, Tenant, Base):
    __tablename__ = "message_events"
    event_id: Mapped[str] = mapped_column(String(200), unique=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    event_type: Mapped[str] = mapped_column(String(40))
    source_revision: Mapped[int] = mapped_column(Integer)


class ImportRecord(Entity, Tenant, Base):
    __tablename__ = "imports"
    __table_args__ = (UniqueConstraint("conversation_id", "source_hash", name="uq_import_source"),)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    source_hash: Mapped[str] = mapped_column(String(64))
    owner_sender_label: Mapped[str] = mapped_column(String(160))
    timezone: Mapped[str] = mapped_column(String(80))
    date_order: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default="completed")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)


class StyleProfile(Entity, Tenant, Base):
    __tablename__ = "style_profiles"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    sufficiency: Mapped[str] = mapped_column(String(30), default="weak")
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    owner_rules: Mapped[list] = mapped_column(EncryptedJSON, default=list)


class Memory(Entity, Tenant, Base):
    __tablename__ = "memories"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    text: Mapped[str] = mapped_column(EncryptedText)
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    visibility: Mapped[str] = mapped_column(String(30), default="conversation")
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_revision: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    suppression_version: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(40), default="owner")
    profile_or_model_version: Mapped[str] = mapped_column(String(120), default="owner-v1")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Suppression(Entity, Tenant, Base):
    __tablename__ = "suppressions"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Draft(Entity, Tenant, Base):
    __tablename__ = "drafts"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    recipient_id: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(EncryptedText)
    evidence_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    missing_facts: Mapped[list] = mapped_column(EncryptedJSON, default=list)
    model_version: Mapped[str] = mapped_column(String(120))
    profile_version: Mapped[int] = mapped_column(Integer, default=0)
    conversation_revision: Mapped[int] = mapped_column(Integer)
    control_epoch: Mapped[int] = mapped_column(Integer)
    permission_version: Mapped[int] = mapped_column(Integer)
    pause_generation: Mapped[int] = mapped_column(Integer)
    connector_fence: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="needs_approval")
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    automation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    automation_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ScheduledIntent(Entity, Tenant, Base):
    __tablename__ = "scheduled_intents"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key", name="uq_schedule_idempotency"),
                      Index("ix_schedules_due_fairness", "status", "last_checked_at", "due_at", "id"))
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(80))
    original_expression: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    workflow_registered: Mapped[bool] = mapped_column(Boolean, default=False)


class Outbox(Entity, Tenant, Base):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_automatic_draft_source", "kind", "created_at", "id"),)
    kind: Mapped[str] = mapped_column(String(60))
    aggregate_id: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)


class SendAttempt(Entity, Tenant, Base):
    __tablename__ = "send_attempts"
    __table_args__ = (UniqueConstraint("draft_id", name="uq_send_draft"),)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="dispatching")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    connector_fence: Mapped[int] = mapped_column(Integer)


class Automation(Entity, Tenant, Base):
    __tablename__ = "automations"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_intents: Mapped[list] = mapped_column(JSON, default=list)
    max_replies_per_hour: Mapped[int] = mapped_column(Integer, default=3)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quiet_start: Mapped[str] = mapped_column(String(5), default="21:00")
    quiet_end: Mapped[str] = mapped_column(String(5), default="09:00")
    version: Mapped[int] = mapped_column(Integer, default=1)
    memory_id: Mapped[str | None] = mapped_column(ForeignKey("memories.id", name="fk_automation_memory"), nullable=True)
    memory_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reply_template: Mapped[str] = mapped_column(EncryptedText, default="{fact}")


class AuditEvent(Entity, Tenant, Base):
    __tablename__ = "audit_events"
    actor_id: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(200))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Task(Entity, Tenant, Base):
    __tablename__ = "tasks"
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(EncryptedText)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_revision: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(40), default="owner")
    version: Mapped[int] = mapped_column(Integer, default=1)


# Register additive feature schemas after the common tenant/entity definitions.
from . import action_models, automatic_drafts_models, jobs_models, lifecycle_models, mobile_models, native_models, people_models, whatsapp_personal_models  # noqa: E402, F401
