"""Scope owner-named export/mock connector labels to their workspace.

Revision ID: d41c7e9a2b10
Revises: c8a46e921fd7
"""

from alembic import op
import sqlalchemy as sa


revision = "d41c7e9a2b10"
down_revision = "c8a46e921fd7"
branch_labels = None
depends_on = None

# Provider identities (Business phone-number IDs, linked-device accounts) stay
# globally unique. Owner-chosen export/mock labels are only unique per workspace,
# so one tenant's label can neither block nor reveal another tenant's label.
LABEL_PROVIDERS = "provider IN ('export_only', 'mock')"


def upgrade():
    with op.batch_alter_table("connectors") as batch:
        batch.drop_constraint("uq_connector_account", type_="unique")
        batch.create_unique_constraint("uq_connector_workspace_account", ["workspace_id", "provider", "account_id"])
    op.create_index("uq_connector_provider_account", "connectors", ["provider", "account_id"], unique=True,
                    postgresql_where=sa.text(f"NOT ({LABEL_PROVIDERS})"),
                    sqlite_where=sa.text(f"NOT ({LABEL_PROVIDERS})"))


def downgrade():
    op.drop_index("uq_connector_provider_account", table_name="connectors")
    with op.batch_alter_table("connectors") as batch:
        batch.drop_constraint("uq_connector_workspace_account", type_="unique")
        batch.create_unique_constraint("uq_connector_account", ["provider", "account_id"])
