"""Explicit automatic draft grants and bounded generation claims.

Revision ID: c8a46e921fd7
Revises: ae912f73c804
"""
from alembic import op
import sqlalchemy as sa

revision = "c8a46e921fd7"
down_revision = "ae912f73c804"
branch_labels = None
depends_on = None


def upgrade():
    slots = op.create_table("automatic_draft_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.String(36)), sa.Column("owner_hash", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.bulk_insert(slots, [{"id": value} for value in range(4)])
    op.create_table("automatic_draft_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), unique=True, nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_drafts_per_hour", sa.Integer(), nullable=False))
    op.create_index("ix_automatic_draft_grants_workspace_id", "automatic_draft_grants", ["workspace_id"])
    op.create_index("ix_automatic_draft_grants_owner_id", "automatic_draft_grants", ["owner_id"])
    op.create_table("automatic_draft_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id"), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("grant_id", sa.String(36), sa.ForeignKey("automatic_draft_grants.id"), nullable=False),
        sa.Column("source_outbox_id", sa.String(36), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("message_revision", sa.Integer(), nullable=False),
        sa.Column("authority_snapshot", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason_code", sa.String(80)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("draft_id", sa.String(36)),
        sa.UniqueConstraint("source_outbox_id", name="uq_automatic_draft_event"),
        sa.UniqueConstraint("message_id", "message_revision", name="uq_automatic_draft_message_revision"))
    op.create_index("ix_automatic_draft_jobs_workspace_id", "automatic_draft_jobs", ["workspace_id"])
    op.create_index("ix_automatic_draft_jobs_conversation_id", "automatic_draft_jobs", ["conversation_id"])
    op.create_index("ix_automatic_draft_claim", "automatic_draft_jobs", ["status", "last_checked_at", "created_at", "id"])
    op.create_index("ix_automatic_draft_owner_inflight", "automatic_draft_jobs", ["owner_id", "status", "claim_expires_at"])
    op.create_index("ix_automatic_draft_hourly", "automatic_draft_jobs", ["grant_id", "started_at"])
    op.create_index("ix_outbox_automatic_draft_source", "outbox", ["kind", "created_at", "id"])


def downgrade():
    op.drop_index("ix_outbox_automatic_draft_source", table_name="outbox")
    op.drop_table("automatic_draft_jobs")
    op.drop_table("automatic_draft_grants")
    op.drop_table("automatic_draft_slots")
