"""Owner-scoped personal WhatsApp metadata and opaque encrypted Signal state.

Revision ID: 9b621f7a4c10
Revises: 7e4a91c2d530
"""

from alembic import op
import sqlalchemy as sa

revision = "9b621f7a4c10"
down_revision = "7e4a91c2d530"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workspaces", sa.Column("creation_key_hash", sa.String(64), nullable=True))
    op.add_column("workspaces", sa.Column("creation_payload_hash", sa.String(64), nullable=True))
    # A portable unique index avoids recreating SQLite's referenced parent table.
    op.create_index("uq_workspace_creation_key", "workspaces", ["owner_id", "creation_key_hash"], unique=True)
    op.create_table("whatsapp_personal_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("connector_fence", sa.Integer(), nullable=False),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.UniqueConstraint("workspace_id", name="uq_personal_session_workspace"),
        sa.UniqueConstraint("connector_id", name="uq_personal_session_connector"))
    op.create_index("ix_whatsapp_personal_sessions_workspace_id", "whatsapp_personal_sessions", ["workspace_id"])
    op.create_index("ix_whatsapp_personal_sessions_connector_id", "whatsapp_personal_sessions", ["connector_id"])
    op.create_table("wa_personal_auth_keys",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id"), primary_key=True),
        sa.Column("key_type", sa.String(80), primary_key=True),
        sa.Column("key_id", sa.String(256), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_wa_personal_auth_keys_workspace_id", "wa_personal_auth_keys", ["workspace_id"])

    op.create_table("wa_personal_account_aliases",
        sa.Column("alias_id", sa.String(120), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id"), nullable=False))
    op.create_index("ix_wa_personal_account_aliases_workspace_id", "wa_personal_account_aliases", ["workspace_id"])
    op.create_index("ix_wa_personal_account_aliases_connector_id", "wa_personal_account_aliases", ["connector_id"])


def downgrade():
    op.drop_table("wa_personal_account_aliases")
    op.drop_table("wa_personal_auth_keys")
    op.drop_table("whatsapp_personal_sessions")
    op.drop_index("uq_workspace_creation_key", table_name="workspaces")
    op.drop_column("workspaces", "creation_payload_hash")
    op.drop_column("workspaces", "creation_key_hash")
