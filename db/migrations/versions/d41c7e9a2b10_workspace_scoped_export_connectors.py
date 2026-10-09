"""Scope owner-named export connector labels to their workspace.

Revision ID: d41c7e9a2b10
Revises: c8a46e921fd7
"""

from contextlib import contextmanager

from alembic import op
import sqlalchemy as sa


revision = "d41c7e9a2b10"
down_revision = "c8a46e921fd7"
branch_labels = None
depends_on = None

# Provider identities (Business phone-number IDs, linked-device accounts and
# simulated accounts) stay globally unique. Owner-chosen export labels are only
# unique per workspace, so one tenant's label can neither block nor reveal another's.
LABEL_PROVIDERS = "provider = 'export_only'"


@contextmanager
def rebuild_safely():
    # SQLite rebuilds the table for constraint changes. Child rows keep the same
    # connector ids, so suspend enforcement for the copy: foreign_keys applies
    # outside a transaction and defer_foreign_keys inside one.
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("PRAGMA defer_foreign_keys=ON")
    yield
    if sqlite:
        violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("Connector rebuild would orphan dependent rows")
        op.execute("PRAGMA foreign_keys=ON")


def upgrade():
    with rebuild_safely(), op.batch_alter_table("connectors") as batch:
        batch.drop_constraint("uq_connector_account", type_="unique")
        batch.create_unique_constraint("uq_connector_workspace_account", ["workspace_id", "provider", "account_id"])
    op.create_index("uq_connector_provider_account", "connectors", ["provider", "account_id"], unique=True,
                    postgresql_where=sa.text(f"NOT ({LABEL_PROVIDERS})"),
                    sqlite_where=sa.text(f"NOT ({LABEL_PROVIDERS})"))


def downgrade():
    op.drop_index("uq_connector_provider_account", table_name="connectors")
    with rebuild_safely(), op.batch_alter_table("connectors") as batch:
        batch.drop_constraint("uq_connector_workspace_account", type_="unique")
        batch.create_unique_constraint("uq_connector_account", ["provider", "account_id"])
