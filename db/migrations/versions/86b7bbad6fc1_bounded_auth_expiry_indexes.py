"""Indexes for bounded authentication metadata expiration.

Revision ID: 86b7bbad6fc1
Revises: 05439876fc2e
"""

from alembic import op


revision = "86b7bbad6fc1"
down_revision = "05439876fc2e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_sessions_expiry_page", "sessions", ["expires_at", "id"])
    op.create_index("ix_login_nonces_expiry_page", "login_nonces", ["expires_at", "id"])
    op.create_index("ix_native_sessions_refresh_expires_at", "native_sessions", ["refresh_expires_at"])


def downgrade():
    op.drop_index("ix_native_sessions_refresh_expires_at", table_name="native_sessions")
    op.drop_index("ix_login_nonces_expiry_page", table_name="login_nonces")
    op.drop_index("ix_sessions_expiry_page", table_name="sessions")
