"""Bounded rotating native refresh sessions

Revision ID: 05439876fc2e
Revises: 776b8a3e1f52
"""
from alembic import op
import sqlalchemy as sa


revision = '05439876fc2e'
down_revision = '776b8a3e1f52'
branch_labels = None
depends_on = None

def upgrade():
    # Existing device sessions receive no invented refresh credential. They keep
    # their bounded access expiry and must complete Google sign-in again.
    with op.batch_alter_table('native_sessions') as batch:
        batch.add_column(sa.Column('refresh_token_hash', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint('uq_native_session_refresh', ['refresh_token_hash'])

def downgrade():
    with op.batch_alter_table('native_sessions') as batch:
        batch.drop_constraint('uq_native_session_refresh', type_='unique')
        batch.drop_column('refresh_expires_at')
        batch.drop_column('refresh_token_hash')
