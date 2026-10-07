"""Durable, server-scoped authority for the six communication operations."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, EncryptedJSON
from .models import Entity, Tenant


class AutomationGrant(Entity, Tenant, Base):
    __tablename__ = "automation_grants"
    __table_args__ = (UniqueConstraint("conversation_id", name="uq_action_grant_chat"),)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str] = mapped_column(String(20), default="AUTO")
    allowed_actions: Mapped[list] = mapped_column(JSON, default=list)
    allowed_intents: Mapped[list] = mapped_column(JSON, default=list)
    reaction_palette: Mapped[list] = mapped_column(JSON, default=list)
    forward_route_ids: Mapped[list] = mapped_column(JSON, default=list)
    require_grounded_facts: Mapped[bool] = mapped_column(Boolean, default=True)
    max_outgoing_per_hour: Mapped[int] = mapped_column(Integer, default=6)
    max_trigger_age_seconds: Mapped[int] = mapped_column(Integer, default=300)
    quiet_start: Mapped[str] = mapped_column(String(5), default="21:00")
    quiet_end: Mapped[str] = mapped_column(String(5), default="09:00")
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Kolkata")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class ForwardRoute(Entity, Tenant, Base):
    __tablename__ = "forward_routes"
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"))
    source_conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    destination_conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    categories: Mapped[list] = mapped_column(JSON, default=list)
    audience: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboundAction(Entity, Tenant, Base):
    __tablename__ = "outbound_actions"
    __table_args__ = (UniqueConstraint("workspace_id", "logical_key", name="uq_action_logical_response"),)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    destination_conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    recipient_id: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20))
    intent: Mapped[str] = mapped_column(String(40))
    logical_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    trigger_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    target_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    native_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    evidence_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_revisions: Mapped[dict] = mapped_column(JSON, default=dict)
    source_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    destination_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    connector_fence: Mapped[int] = mapped_column(Integer)
    pause_generation: Mapped[int] = mapped_column(Integer)
    grant_id: Mapped[str | None] = mapped_column(ForeignKey("automation_grants.id"), nullable=True)
    grant_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("forward_routes.id"), nullable=True)
    route_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    authorized_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    authorized_job_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    authorized_job_run_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="ready", index=True)
    reason_code: Mapped[str | None] = mapped_column(String(60), nullable=True)

    @property
    def text(self):
        return self.payload.get("text", "")

    @property
    def emoji(self):
        return self.payload.get("emoji")


class SubmissionAttempt(Entity, Tenant, Base):
    __tablename__ = "submission_attempts"
    __table_args__ = (UniqueConstraint("action_id", name="uq_action_submission"),)
    action_id: Mapped[str] = mapped_column(ForeignKey("outbound_actions.id"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"))
    destination_conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    payload_hash: Mapped[str] = mapped_column(String(64))
    connector_fence: Mapped[int] = mapped_column(Integer)
    provider_message_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="submitting")
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
