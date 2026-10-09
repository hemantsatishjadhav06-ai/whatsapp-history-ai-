"""Full personal WhatsApp sync: chat aliases, per-chat sync state, import preference, inbox activity order.

Revision ID: f7c3a1d9b2e8
Revises: e2b7c9d1a4f0
"""

from alembic import op
import sqlalchemy as sa


revision = "f7c3a1d9b2e8"
down_revision = "e2b7c9d1a4f0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_conversations_activity", "conversations", ["workspace_id", "last_message_at", "id"])
    # Existing chats order by their newest stored message.
    op.execute("UPDATE conversations SET last_message_at = (SELECT max(messages.provider_timestamp) "
               "FROM messages WHERE messages.conversation_id = conversations.id)")
    op.add_column("whatsapp_personal_sessions", sa.Column("sync_phase", sa.String(30), nullable=True))
    op.add_column("whatsapp_personal_sessions", sa.Column("sync_progress", sa.Integer(), nullable=True))
    op.add_column("whatsapp_personal_sessions", sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("wa_personal_preferences",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("import_mode", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("wa_personal_chat_aliases",
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id"), primary_key=True),
        sa.Column("alias_jid", sa.String(160), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False))
    op.create_index("ix_wa_personal_chat_aliases_workspace_id", "wa_personal_chat_aliases", ["workspace_id"])
    op.create_index("ix_wa_personal_chat_aliases_conversation_id", "wa_personal_chat_aliases", ["conversation_id"])
    op.create_table("wa_personal_chat_sync",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("title_source", sa.String(20), nullable=False),
        sa.Column("unread_count", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("oldest_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oldest_provider_message_id", sa.String(180), nullable=True),
        sa.Column("oldest_from_me", sa.Boolean(), nullable=True),
        sa.Column("backfill_state", sa.String(20), nullable=False),
        sa.Column("backfill_requests", sa.Integer(), nullable=False),
        sa.Column("style_dirty", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", name="uq_personal_chat_sync_conversation"))
    op.create_index("ix_wa_personal_chat_sync_workspace_id", "wa_personal_chat_sync", ["workspace_id"])
    op.create_index("ix_wa_personal_chat_sync_connector_id", "wa_personal_chat_sync", ["connector_id"])
    op.create_index("ix_personal_chat_sync_backfill", "wa_personal_chat_sync",
                    ["connector_id", "backfill_state", "updated_at"])


def downgrade():
    op.drop_table("wa_personal_chat_sync")
    op.drop_table("wa_personal_chat_aliases")
    op.drop_table("wa_personal_preferences")
    with op.batch_alter_table("whatsapp_personal_sessions") as batch:
        batch.drop_column("last_sync_at")
        batch.drop_column("sync_progress")
        batch.drop_column("sync_phase")
    op.drop_index("ix_conversations_activity", table_name="conversations")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("last_message_at")
