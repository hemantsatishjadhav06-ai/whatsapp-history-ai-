"""Detect rotated refresh-token reuse and bind personal pairing to an expected number.

Revision ID: e2b7c9d1a4f0
Revises: d41c7e9a2b10
"""

from alembic import op
import sqlalchemy as sa


revision = "e2b7c9d1a4f0"
down_revision = "d41c7e9a2b10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("native_sessions") as batch:
        batch.add_column(sa.Column("previous_refresh_token_hash", sa.String(64), nullable=True))
    op.create_index("ix_native_sessions_previous_refresh_token_hash", "native_sessions",
                    ["previous_refresh_token_hash"])
    with op.batch_alter_table("whatsapp_personal_sessions") as batch:
        batch.add_column(sa.Column("expected_account_hash", sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table("whatsapp_personal_sessions") as batch:
        batch.drop_column("expected_account_hash")
    op.drop_index("ix_native_sessions_previous_refresh_token_hash", table_name="native_sessions")
    with op.batch_alter_table("native_sessions") as batch:
        batch.drop_column("previous_refresh_token_hash")
