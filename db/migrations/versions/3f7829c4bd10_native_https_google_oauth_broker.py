"""Native HTTPS Google OAuth broker and proof-bound single-use handoffs.

Revision ID: 3f7829c4bd10
Revises: 86b7bbad6fc1
"""
from alembic import op
import sqlalchemy as sa


revision = '3f7829c4bd10'
down_revision = '86b7bbad6fc1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('native_oauth_attempts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state_hash', sa.String(length=64), nullable=False),
        sa.Column('code_challenge', sa.String(length=43), nullable=False),
        sa.Column('platform', sa.String(length=10), nullable=False),
        sa.Column('device_name', sa.Text(), nullable=False),
        sa.Column('google_nonce', sa.Text(), nullable=False),
        sa.Column('google_verifier', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('handoff_hash', sa.String(length=64), nullable=True),
        sa.Column('verified_identity', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('state_hash'),
        sa.UniqueConstraint('handoff_hash'))
    op.create_index('ix_native_oauth_attempts_expires_at', 'native_oauth_attempts', ['expires_at'])


def downgrade():
    op.drop_index('ix_native_oauth_attempts_expires_at', table_name='native_oauth_attempts')
    op.drop_table('native_oauth_attempts')
