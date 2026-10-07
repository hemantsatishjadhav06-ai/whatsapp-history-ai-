"""native actions contacts jobs and retention

Revision ID: 2c71848ffaec
Revises: 05968846b410
"""
from alembic import op
import sqlalchemy as sa


revision = '2c71848ffaec'
down_revision = '05968846b410'
branch_labels = None
depends_on = None

def upgrade():
    # Private fields use physical TEXT; the application encrypts every write.
    op.create_table('retention_policies',
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('raw_days', sa.Integer(), nullable=False),
    sa.Column('derived_days', sa.Integer(), nullable=False),
    sa.Column('audit_days', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('backup_status', sa.String(length=40), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id')
    )
    op.create_table('usage_ledger',
    sa.Column('operation_key', sa.String(length=160), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('window_day', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('action_units', sa.Integer(), nullable=False),
    sa.Column('token_units', sa.Integer(), nullable=False),
    sa.Column('cost_microusd', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'operation_key', name='uq_usage_operation')
    )
    op.create_index(op.f('ix_usage_ledger_window_day'), 'usage_ledger', ['window_day'], unique=False)
    op.create_index(op.f('ix_usage_ledger_workspace_id'), 'usage_ledger', ['workspace_id'], unique=False)
    op.create_table('workspace_budgets',
    sa.Column('max_actions_per_day', sa.Integer(), nullable=True),
    sa.Column('max_tokens_per_day', sa.Integer(), nullable=True),
    sa.Column('max_cost_microusd_per_day', sa.Integer(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', name='uq_workspace_budget')
    )
    op.create_index(op.f('ix_workspace_budgets_workspace_id'), 'workspace_budgets', ['workspace_id'], unique=False)
    op.create_table('local_contacts',
    sa.Column('connector_id', sa.String(length=36), nullable=False),
    sa.Column('provider_identity', sa.String(length=160), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'connector_id', 'provider_identity', name='uq_local_contact_identity')
    )
    op.create_index(op.f('ix_local_contacts_connector_id'), 'local_contacts', ['connector_id'], unique=False)
    op.create_index(op.f('ix_local_contacts_workspace_id'), 'local_contacts', ['workspace_id'], unique=False)
    op.create_table('authorized_jobs',
    sa.Column('conversation_id', sa.String(length=36), nullable=True),
    sa.Column('connector_id', sa.String(length=36), nullable=True),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('purpose', sa.Text(), nullable=False),
    sa.Column('action_kind', sa.String(length=30), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('emoji', sa.String(length=32), nullable=True),
    sa.Column('target_message_id', sa.String(length=36), nullable=True),
    sa.Column('route_id', sa.String(length=36), nullable=True),
    sa.Column('route_version', sa.Integer(), nullable=True),
    sa.Column('destination_snapshot', sa.JSON(), nullable=False),
    sa.Column('evidence_message_ids', sa.JSON(), nullable=False),
    sa.Column('source_revisions', sa.JSON(), nullable=False),
    sa.Column('memory_ids', sa.JSON(), nullable=False),
    sa.Column('memory_versions', sa.JSON(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('timezone', sa.String(length=80), nullable=False),
    sa.Column('recurrence', sa.String(length=12), nullable=False),
    sa.Column('local_time', sa.String(length=15), nullable=False),
    sa.Column('weekday', sa.Integer(), nullable=False),
    sa.Column('ambiguity_policy', sa.String(length=12), nullable=False),
    sa.Column('gap_policy', sa.String(length=12), nullable=False),
    sa.Column('max_lateness_seconds', sa.Integer(), nullable=False),
    sa.Column('quiet_start', sa.String(length=5), nullable=True),
    sa.Column('quiet_end', sa.String(length=5), nullable=True),
    sa.Column('max_runs', sa.Integer(), nullable=False),
    sa.Column('max_runs_per_hour', sa.Integer(), nullable=False),
    sa.Column('runs_done', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('hold_reason', sa.String(length=60), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('permission_version', sa.Integer(), nullable=False),
    sa.Column('control_epoch', sa.Integer(), nullable=False),
    sa.Column('connector_fence', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'idempotency_key', name='uq_job_idempotency')
    )
    op.create_index(op.f('ix_authorized_jobs_conversation_id'), 'authorized_jobs', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_authorized_jobs_due_at'), 'authorized_jobs', ['due_at'], unique=False)
    op.create_index(op.f('ix_authorized_jobs_workspace_id'), 'authorized_jobs', ['workspace_id'], unique=False)
    op.create_table('automation_grants',
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('connector_id', sa.String(length=36), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('allowed_actions', sa.JSON(), nullable=False),
    sa.Column('allowed_intents', sa.JSON(), nullable=False),
    sa.Column('reaction_palette', sa.JSON(), nullable=False),
    sa.Column('forward_route_ids', sa.JSON(), nullable=False),
    sa.Column('require_grounded_facts', sa.Boolean(), nullable=False),
    sa.Column('max_outgoing_per_hour', sa.Integer(), nullable=False),
    sa.Column('max_trigger_age_seconds', sa.Integer(), nullable=False),
    sa.Column('quiet_start', sa.String(length=5), nullable=False),
    sa.Column('quiet_end', sa.String(length=5), nullable=False),
    sa.Column('timezone', sa.String(length=80), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conversation_id', name='uq_action_grant_chat')
    )
    op.create_index(op.f('ix_automation_grants_conversation_id'), 'automation_grants', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_automation_grants_workspace_id'), 'automation_grants', ['workspace_id'], unique=False)
    op.create_table('contact_save_grants',
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conversation_id')
    )
    op.create_index(op.f('ix_contact_save_grants_workspace_id'), 'contact_save_grants', ['workspace_id'], unique=False)
    op.create_table('forward_routes',
    sa.Column('connector_id', sa.String(length=36), nullable=False),
    sa.Column('source_conversation_id', sa.String(length=36), nullable=False),
    sa.Column('destination_conversation_id', sa.String(length=36), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('categories', sa.JSON(), nullable=False),
    sa.Column('audience', sa.String(length=20), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['destination_conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['source_conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_forward_routes_destination_conversation_id'), 'forward_routes', ['destination_conversation_id'], unique=False)
    op.create_index(op.f('ix_forward_routes_source_conversation_id'), 'forward_routes', ['source_conversation_id'], unique=False)
    op.create_index(op.f('ix_forward_routes_workspace_id'), 'forward_routes', ['workspace_id'], unique=False)
    op.create_table('contact_sources',
    sa.Column('contact_id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('message_id', sa.String(length=36), nullable=False),
    sa.Column('source_revision', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['local_contacts.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('contact_id', 'message_id', name='uq_contact_source')
    )
    op.create_index(op.f('ix_contact_sources_contact_id'), 'contact_sources', ['contact_id'], unique=False)
    op.create_index(op.f('ix_contact_sources_conversation_id'), 'contact_sources', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_contact_sources_message_id'), 'contact_sources', ['message_id'], unique=False)
    op.create_index(op.f('ix_contact_sources_workspace_id'), 'contact_sources', ['workspace_id'], unique=False)
    op.create_table('job_runs',
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('occurrence', sa.Integer(), nullable=False),
    sa.Column('job_version', sa.Integer(), nullable=False),
    sa.Column('run_key', sa.String(length=100), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('action_id', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('error_code', sa.String(length=80), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['authorized_jobs.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'occurrence', name='uq_job_occurrence'),
    sa.UniqueConstraint('run_key')
    )
    op.create_index(op.f('ix_job_runs_job_id'), 'job_runs', ['job_id'], unique=False)
    op.create_index(op.f('ix_job_runs_workspace_id'), 'job_runs', ['workspace_id'], unique=False)
    op.create_table('message_contexts',
    sa.Column('message_id', sa.String(length=36), nullable=False),
    sa.Column('connector_id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('sender_identity', sa.Text(), nullable=False),
    sa.Column('participant_identity', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('owner_addressed', sa.Boolean(), nullable=False),
    sa.Column('view_once', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('message_id')
    )
    op.create_index(op.f('ix_message_contexts_connector_id'), 'message_contexts', ['connector_id'], unique=False)
    op.create_index(op.f('ix_message_contexts_conversation_id'), 'message_contexts', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_message_contexts_workspace_id'), 'message_contexts', ['workspace_id'], unique=False)
    op.create_table('native_records',
    sa.Column('message_id', sa.String(length=36), nullable=False),
    sa.Column('connector_id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('provider_record_ref', sa.String(length=180), nullable=False),
    sa.Column('provider_message_id', sa.String(length=180), nullable=False),
    sa.Column('account_id', sa.String(length=120), nullable=False),
    sa.Column('provider_chat_id', sa.String(length=160), nullable=False),
    sa.Column('source_revision', sa.Integer(), nullable=False),
    sa.Column('provenance', sa.String(length=20), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('owner_addressed', sa.Boolean(), nullable=False),
    sa.Column('view_once', sa.Boolean(), nullable=False),
    sa.Column('deleted', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('connector_id', 'provider_record_ref', name='uq_native_provider_ref'),
    sa.UniqueConstraint('message_id')
    )
    op.create_index(op.f('ix_native_records_connector_id'), 'native_records', ['connector_id'], unique=False)
    op.create_index(op.f('ix_native_records_conversation_id'), 'native_records', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_native_records_workspace_id'), 'native_records', ['workspace_id'], unique=False)
    op.create_table('outbound_actions',
    sa.Column('connector_id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('destination_conversation_id', sa.String(length=36), nullable=False),
    sa.Column('recipient_id', sa.String(length=160), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('intent', sa.String(length=40), nullable=False),
    sa.Column('logical_key', sa.String(length=64), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('trigger_message_id', sa.String(length=36), nullable=True),
    sa.Column('target_message_id', sa.String(length=36), nullable=True),
    sa.Column('native_record_id', sa.String(length=36), nullable=True),
    sa.Column('evidence_message_ids', sa.JSON(), nullable=False),
    sa.Column('source_revisions', sa.JSON(), nullable=False),
    sa.Column('source_snapshot', sa.JSON(), nullable=False),
    sa.Column('destination_snapshot', sa.JSON(), nullable=False),
    sa.Column('connector_fence', sa.Integer(), nullable=False),
    sa.Column('pause_generation', sa.Integer(), nullable=False),
    sa.Column('grant_id', sa.String(length=36), nullable=True),
    sa.Column('grant_version', sa.Integer(), nullable=True),
    sa.Column('route_id', sa.String(length=36), nullable=True),
    sa.Column('route_version', sa.Integer(), nullable=True),
    sa.Column('authorized_job_id', sa.String(length=36), nullable=True),
    sa.Column('authorized_job_version', sa.Integer(), nullable=True),
    sa.Column('authorized_job_run_key', sa.String(length=160), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('reason_code', sa.String(length=60), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['destination_conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['grant_id'], ['automation_grants.id'], ),
    sa.ForeignKeyConstraint(['route_id'], ['forward_routes.id'], ),
    sa.ForeignKeyConstraint(['target_message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['trigger_message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'logical_key', name='uq_action_logical_response')
    )
    op.create_index(op.f('ix_outbound_actions_connector_id'), 'outbound_actions', ['connector_id'], unique=False)
    op.create_index(op.f('ix_outbound_actions_conversation_id'), 'outbound_actions', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_outbound_actions_destination_conversation_id'), 'outbound_actions', ['destination_conversation_id'], unique=False)
    op.create_index(op.f('ix_outbound_actions_status'), 'outbound_actions', ['status'], unique=False)
    op.create_index(op.f('ix_outbound_actions_workspace_id'), 'outbound_actions', ['workspace_id'], unique=False)
    op.create_table('reaction_examples',
    sa.Column('connector_id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('message_id', sa.String(length=36), nullable=False),
    sa.Column('event_key', sa.String(length=64), nullable=False),
    sa.Column('target_revision', sa.Integer(), nullable=False),
    sa.Column('actor_id', sa.String(length=160), nullable=False),
    sa.Column('author_kind', sa.String(length=40), nullable=False),
    sa.Column('emoji', sa.String(length=32), nullable=False),
    sa.Column('origin', sa.String(length=30), nullable=False),
    sa.Column('provider_timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('context', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('learn_eligible', sa.Boolean(), nullable=False),
    sa.Column('handled', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('connector_id', 'event_key', name='uq_reaction_event')
    )
    op.create_index(op.f('ix_reaction_examples_connector_id'), 'reaction_examples', ['connector_id'], unique=False)
    op.create_index(op.f('ix_reaction_examples_conversation_id'), 'reaction_examples', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_reaction_examples_message_id'), 'reaction_examples', ['message_id'], unique=False)
    op.create_index(op.f('ix_reaction_examples_workspace_id'), 'reaction_examples', ['workspace_id'], unique=False)
    op.create_table('submission_attempts',
    sa.Column('action_id', sa.String(length=36), nullable=False),
    sa.Column('connector_id', sa.String(length=36), nullable=False),
    sa.Column('destination_conversation_id', sa.String(length=36), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('connector_fence', sa.Integer(), nullable=False),
    sa.Column('provider_message_id', sa.String(length=180), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('error_code', sa.String(length=60), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['action_id'], ['outbound_actions.id'], ),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['destination_conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('action_id', name='uq_action_submission')
    )
    op.create_index(op.f('ix_submission_attempts_action_id'), 'submission_attempts', ['action_id'], unique=False)
    op.create_index(op.f('ix_submission_attempts_workspace_id'), 'submission_attempts', ['workspace_id'], unique=False)

def downgrade():
    # Private fields use physical TEXT; the application encrypts every write.
    op.drop_index(op.f('ix_submission_attempts_workspace_id'), table_name='submission_attempts')
    op.drop_index(op.f('ix_submission_attempts_action_id'), table_name='submission_attempts')
    op.drop_table('submission_attempts')
    op.drop_index(op.f('ix_reaction_examples_workspace_id'), table_name='reaction_examples')
    op.drop_index(op.f('ix_reaction_examples_message_id'), table_name='reaction_examples')
    op.drop_index(op.f('ix_reaction_examples_conversation_id'), table_name='reaction_examples')
    op.drop_index(op.f('ix_reaction_examples_connector_id'), table_name='reaction_examples')
    op.drop_table('reaction_examples')
    op.drop_index(op.f('ix_outbound_actions_workspace_id'), table_name='outbound_actions')
    op.drop_index(op.f('ix_outbound_actions_status'), table_name='outbound_actions')
    op.drop_index(op.f('ix_outbound_actions_destination_conversation_id'), table_name='outbound_actions')
    op.drop_index(op.f('ix_outbound_actions_conversation_id'), table_name='outbound_actions')
    op.drop_index(op.f('ix_outbound_actions_connector_id'), table_name='outbound_actions')
    op.drop_table('outbound_actions')
    op.drop_index(op.f('ix_native_records_workspace_id'), table_name='native_records')
    op.drop_index(op.f('ix_native_records_conversation_id'), table_name='native_records')
    op.drop_index(op.f('ix_native_records_connector_id'), table_name='native_records')
    op.drop_table('native_records')
    op.drop_index(op.f('ix_message_contexts_workspace_id'), table_name='message_contexts')
    op.drop_index(op.f('ix_message_contexts_conversation_id'), table_name='message_contexts')
    op.drop_index(op.f('ix_message_contexts_connector_id'), table_name='message_contexts')
    op.drop_table('message_contexts')
    op.drop_index(op.f('ix_job_runs_workspace_id'), table_name='job_runs')
    op.drop_index(op.f('ix_job_runs_job_id'), table_name='job_runs')
    op.drop_table('job_runs')
    op.drop_index(op.f('ix_contact_sources_workspace_id'), table_name='contact_sources')
    op.drop_index(op.f('ix_contact_sources_message_id'), table_name='contact_sources')
    op.drop_index(op.f('ix_contact_sources_conversation_id'), table_name='contact_sources')
    op.drop_index(op.f('ix_contact_sources_contact_id'), table_name='contact_sources')
    op.drop_table('contact_sources')
    op.drop_index(op.f('ix_forward_routes_workspace_id'), table_name='forward_routes')
    op.drop_index(op.f('ix_forward_routes_source_conversation_id'), table_name='forward_routes')
    op.drop_index(op.f('ix_forward_routes_destination_conversation_id'), table_name='forward_routes')
    op.drop_table('forward_routes')
    op.drop_index(op.f('ix_contact_save_grants_workspace_id'), table_name='contact_save_grants')
    op.drop_table('contact_save_grants')
    op.drop_index(op.f('ix_automation_grants_workspace_id'), table_name='automation_grants')
    op.drop_index(op.f('ix_automation_grants_conversation_id'), table_name='automation_grants')
    op.drop_table('automation_grants')
    op.drop_index(op.f('ix_authorized_jobs_workspace_id'), table_name='authorized_jobs')
    op.drop_index(op.f('ix_authorized_jobs_due_at'), table_name='authorized_jobs')
    op.drop_index(op.f('ix_authorized_jobs_conversation_id'), table_name='authorized_jobs')
    op.drop_table('authorized_jobs')
    op.drop_index(op.f('ix_local_contacts_workspace_id'), table_name='local_contacts')
    op.drop_index(op.f('ix_local_contacts_connector_id'), table_name='local_contacts')
    op.drop_table('local_contacts')
    op.drop_index(op.f('ix_workspace_budgets_workspace_id'), table_name='workspace_budgets')
    op.drop_table('workspace_budgets')
    op.drop_index(op.f('ix_usage_ledger_workspace_id'), table_name='usage_ledger')
    op.drop_index(op.f('ix_usage_ledger_window_day'), table_name='usage_ledger')
    op.drop_table('usage_ledger')
    op.drop_table('retention_policies')
