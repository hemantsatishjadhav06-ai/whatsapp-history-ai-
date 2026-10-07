"""Persist fair rotation of bounded due-job batches.

Revision ID: 7e4a91c2d530
Revises: 3f7829c4bd10
"""

from alembic import op
import sqlalchemy as sa


revision = "7e4a91c2d530"
down_revision = "3f7829c4bd10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("authorized_jobs", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_due_fairness", "authorized_jobs", ["status", "last_checked_at", "due_at", "id"])
    op.add_column("scheduled_intents", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_schedules_due_fairness", "scheduled_intents", ["status", "last_checked_at", "due_at", "id"])


def downgrade():
    op.drop_index("ix_schedules_due_fairness", table_name="scheduled_intents")
    op.drop_column("scheduled_intents", "last_checked_at")
    op.drop_index("ix_jobs_due_fairness", table_name="authorized_jobs")
    op.drop_column("authorized_jobs", "last_checked_at")
