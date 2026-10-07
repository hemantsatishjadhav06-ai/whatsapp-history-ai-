"""Durable, explicitly owner-authorized jobs and their occurrence ledger."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, EncryptedText
from .models import Entity, Tenant


class AuthorizedJob(Entity, Tenant, Base):
    __tablename__ = "authorized_jobs"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key", name="uq_job_idempotency"),
                      Index("ix_jobs_due_fairness", "status", "last_checked_at", "due_at", "id"))
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), nullable=True, index=True)
    connector_id: Mapped[str | None] = mapped_column(ForeignKey("connectors.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(EncryptedText)
    action_kind: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(EncryptedText, default="")
    emoji: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    route_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    route_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_revisions: Mapped[dict] = mapped_column(JSON, default=dict)
    memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    memory_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(80))
    recurrence: Mapped[str] = mapped_column(String(12), default="none")
    local_time: Mapped[str] = mapped_column(String(15))
    weekday: Mapped[int] = mapped_column(Integer)
    ambiguity_policy: Mapped[str] = mapped_column(String(12), default="reject")
    gap_policy: Mapped[str] = mapped_column(String(12), default="reject")
    max_lateness_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    quiet_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    quiet_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    max_runs: Mapped[int] = mapped_column(Integer, default=1)
    max_runs_per_hour: Mapped[int] = mapped_column(Integer, default=3)
    runs_done: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    hold_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    permission_version: Mapped[int] = mapped_column(Integer, default=0)
    control_epoch: Mapped[int] = mapped_column(Integer, default=0)
    connector_fence: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def run_key(self):
        return f"{self.id}:{self.version}:{self.runs_done}"


class JobRun(Entity, Tenant, Base):
    __tablename__ = "job_runs"
    __table_args__ = (UniqueConstraint("job_id", "occurrence", name="uq_job_occurrence"),)
    job_id: Mapped[str] = mapped_column(ForeignKey("authorized_jobs.id"), index=True)
    occurrence: Mapped[int] = mapped_column(Integer)
    job_version: Mapped[int] = mapped_column(Integer)
    run_key: Mapped[str] = mapped_column(String(100), unique=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="ready")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
