"""Bounded verified-fact automation, disabled until an owner grants a rule."""
from alembic import op
import sqlalchemy as sa
from cryptography.fernet import Fernet
from assistant.config import Settings

revision = "05968846b410"
down_revision = "95f566068880"
branch_labels = None
depends_on = None


def upgrade():
    empty_template = Fernet(Settings().prepare().encryption_key.encode()).encrypt(b"{fact}").decode()
    with op.batch_alter_table("automations") as batch:
        batch.add_column(sa.Column("memory_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("memory_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reply_template", sa.Text(), nullable=False, server_default=empty_template))
        batch.create_foreign_key("fk_automation_memory", "memories", ["memory_id"], ["id"])
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("automations", "reply_template", server_default=None)
    op.add_column("drafts", sa.Column("automation_id", sa.String(36), nullable=True))
    op.add_column("drafts", sa.Column("automation_version", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("automations") as batch:
        batch.drop_constraint("fk_automation_memory", type_="foreignkey")
        batch.drop_column("reply_template")
        batch.drop_column("memory_version")
        batch.drop_column("memory_id")
    op.drop_column("drafts", "automation_version")
    op.drop_column("drafts", "automation_id")
