"""Index retained message pages by exact workspace and conversation.

Revision ID: ae912f73c804
Revises: 9b621f7a4c10
"""

from alembic import op
import sqlalchemy as sa


revision = "ae912f73c804"
down_revision = "9b621f7a4c10"
branch_labels = None
depends_on = None


def upgrade():
    # This ordinary CREATE INDEX is appropriate for the bounded pilot. A large
    # existing installation needs a separately scheduled online index build.
    op.create_index(
        "ix_messages_read_page",
        "messages",
        # Both engines scan this ascending B-tree backwards for a descending
        # page after the two leading equality predicates. Plain columns also
        # allow SQLite's index reflection to verify model/migration parity.
        ["workspace_id", "conversation_id", "provider_timestamp", "id"],
        postgresql_where=sa.text("deleted IS FALSE"),
        sqlite_where=sa.text("deleted IS 0"),
    )
    op.create_index(
        "ix_messages_retention_page",
        "messages",
        ["workspace_id", "received_at", "id"],
        postgresql_where=sa.text("deleted IS FALSE"),
        sqlite_where=sa.text("deleted IS 0"),
    )


def downgrade():
    op.drop_index("ix_messages_retention_page", table_name="messages")
    op.drop_index("ix_messages_read_page", table_name="messages")
