"""Paid verified employer discovery and exact-package applications."""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0006"
down_revision = "20261008_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("job_service_credits", sa.Integer(), nullable=False, server_default="0"))
    op.create_table('employer_sources',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('employer', sa.String(200), primary_key=False, nullable=False),
        sa.Column('platform', sa.String(40), primary_key=False, nullable=False),
        sa.Column('board_token', sa.String(120), primary_key=False, nullable=False),
        sa.Column('region', sa.String(16), primary_key=False, nullable=False),
        sa.Column('careers_url', sa.Text(), primary_key=False, nullable=False),
        sa.Column('allowed_hosts', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('verification_url', sa.Text(), primary_key=False, nullable=False),
        sa.Column('verification_note', sa.String(1000), primary_key=False, nullable=False),
        sa.Column('verified_by', sa.Integer(), sa.ForeignKey('users.id'), primary_key=False, nullable=True),
        sa.Column('verified_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('enabled', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('submission_enabled', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('submission_grant', sa.String(500), primary_key=False, nullable=True),
        sa.Column('credential_env', sa.String(120), primary_key=False, nullable=True),
        sa.Column('form_parity_verified', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('receipt_contract', sa.JSON(), primary_key=False, nullable=True),
        sa.Column('status', sa.String(24), primary_key=False, nullable=False),
        sa.Column('last_success_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('last_error_code', sa.String(80), primary_key=False, nullable=True),
        sa.Column('next_refresh_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('scan_token', sa.String(64), primary_key=False, nullable=True),
        sa.Column('scan_started_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.UniqueConstraint('platform', 'region', 'board_token', name='uq_employer_source_tenant'),
    )
    op.create_index('ix_employer_sources_due', 'employer_sources', ['enabled', 'next_refresh_at'], unique=False)

    op.create_table('employer_postings',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('source_id', sa.String(64), sa.ForeignKey('employer_sources.id'), primary_key=False, nullable=False),
        sa.Column('external_id', sa.String(160), primary_key=False, nullable=False),
        sa.Column('requisition_id', sa.String(160), primary_key=False, nullable=True),
        sa.Column('title', sa.String(300), primary_key=False, nullable=False),
        sa.Column('employer', sa.String(200), primary_key=False, nullable=False),
        sa.Column('location', sa.String(400), primary_key=False, nullable=False),
        sa.Column('country', sa.String(2), primary_key=False, nullable=True),
        sa.Column('remote', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('description', sa.Text(), primary_key=False, nullable=False),
        sa.Column('skills', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('canonical_url', sa.Text(), primary_key=False, nullable=False),
        sa.Column('apply_url', sa.Text(), primary_key=False, nullable=False),
        sa.Column('language', sa.String(20), primary_key=False, nullable=False),
        sa.Column('publication_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('source_updated_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('content_sha256', sa.String(64), primary_key=False, nullable=False),
        sa.Column('is_open', sa.Boolean(), primary_key=False, nullable=False),
        sa.UniqueConstraint('source_id', 'external_id', name='uq_employer_source_posting'),
    )
    op.create_index('ix_employer_postings_location', 'employer_postings', ['location'], unique=False)
    op.create_index('ix_employer_postings_open_checked', 'employer_postings', ['is_open', 'last_checked_at'], unique=False)
    op.create_index('ix_employer_postings_source_id', 'employer_postings', ['source_id'], unique=False)
    op.create_index('ix_employer_postings_title', 'employer_postings', ['title'], unique=False)

    op.create_table('service_credit_events',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=False, nullable=True),
        sa.Column('event_type', sa.String(24), primary_key=False, nullable=False),
        sa.Column('amount', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('balance_after', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('idempotency_key', sa.String(200), primary_key=False, nullable=False),
        sa.Column('source_type', sa.String(48), primary_key=False, nullable=False),
        sa.Column('source_id', sa.String(120), primary_key=False, nullable=False),
        sa.Column('reason', sa.String(240), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.UniqueConstraint('user_id', 'idempotency_key', name='uq_service_credit_user_key'),
    )
    op.create_index('ix_service_credit_events_user_id', 'service_credit_events', ['user_id'], unique=False)
    op.create_index('ix_service_credit_user_created', 'service_credit_events', ['user_id', 'created_at'], unique=False)

    op.create_table('service_credit_reservations',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=False, nullable=True),
        sa.Column('operation', sa.String(40), primary_key=False, nullable=False),
        sa.Column('source_id', sa.String(64), primary_key=False, nullable=False),
        sa.Column('unit_price', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('requested_count', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('reserved_amount', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('committed_amount', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('released_amount', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('state', sa.String(24), primary_key=False, nullable=False),
        sa.Column('pricing_version', sa.String(64), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('settled_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.UniqueConstraint('operation', 'source_id', name='uq_service_reservation_source'),
    )
    op.create_index('ix_service_credit_reservations_user_id', 'service_credit_reservations', ['user_id'], unique=False)

    op.create_table('employer_searches',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=False, nullable=False),
        sa.Column('resume_id', sa.Integer(), sa.ForeignKey('resumes.id'), primary_key=False, nullable=False),
        sa.Column('idempotency_key', sa.String(160), primary_key=False, nullable=False),
        sa.Column('input_fingerprint', sa.String(64), primary_key=False, nullable=False),
        sa.Column('query', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('status', sa.String(24), primary_key=False, nullable=False),
        sa.Column('desired_count', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('delivered_count', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('reserved_credits', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('charged_credits', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('refunded_credits', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('items', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('scope', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.UniqueConstraint('user_id', 'idempotency_key', name='uq_employer_search_user_key'),
    )
    op.create_index('ix_employer_search_user_created', 'employer_searches', ['user_id', 'created_at'], unique=False)
    op.create_index('ix_employer_searches_user_id', 'employer_searches', ['user_id'], unique=False)

    op.create_table('employer_job_deliveries',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=False, nullable=False),
        sa.Column('posting_id', sa.String(64), sa.ForeignKey('employer_postings.id'), primary_key=False, nullable=False),
        sa.Column('search_id', sa.String(64), sa.ForeignKey('employer_searches.id'), primary_key=False, nullable=False),
        sa.Column('charged_credits', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.UniqueConstraint('user_id', 'posting_id', name='uq_employer_delivery_user_posting'),
    )
    op.create_index('ix_employer_job_deliveries_user_id', 'employer_job_deliveries', ['user_id'], unique=False)

    op.create_table('sealed_application_artifacts',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=False, nullable=False),
        sa.Column('resume_id', sa.Integer(), sa.ForeignKey('resumes.id'), primary_key=False, nullable=False),
        sa.Column('resume_version_id', sa.String(64), sa.ForeignKey('resume_versions.id'), primary_key=False, nullable=True),
        sa.Column('sha256', sa.String(64), primary_key=False, nullable=False),
        sa.Column('filename', sa.String(240), primary_key=False, nullable=False),
        sa.Column('media_type', sa.String(160), primary_key=False, nullable=False),
        sa.Column('size_bytes', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('content', sa.LargeBinary(), primary_key=False, nullable=True),
        sa.Column('gcs_object', sa.Text(), primary_key=False, nullable=True),
        sa.Column('gcs_generation', sa.String(80), primary_key=False, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
    )
    op.create_index('ix_sealed_application_artifacts_user_id', 'sealed_application_artifacts', ['user_id'], unique=False)

    op.create_table('employer_applications',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=False, nullable=False),
        sa.Column('posting_id', sa.String(64), sa.ForeignKey('employer_postings.id'), primary_key=False, nullable=False),
        sa.Column('idempotency_key', sa.String(160), primary_key=False, nullable=False),
        sa.Column('input_fingerprint', sa.String(64), primary_key=False, nullable=False),
        sa.Column('active_key', sa.String(160), primary_key=False, nullable=True),
        sa.Column('resume_id', sa.Integer(), sa.ForeignKey('resumes.id'), primary_key=False, nullable=False),
        sa.Column('resume_choice', sa.String(16), primary_key=False, nullable=False),
        sa.Column('resume_version_id', sa.String(64), sa.ForeignKey('resume_versions.id'), primary_key=False, nullable=True),
        sa.Column('artifact_id', sa.String(64), sa.ForeignKey('sealed_application_artifacts.id'), primary_key=False, nullable=False),
        sa.Column('form', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('answers', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('consents', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('package_digest', sa.String(64), primary_key=False, nullable=False),
        sa.Column('job_content_sha256', sa.String(64), primary_key=False, nullable=False),
        sa.Column('application_mode', sa.String(24), primary_key=False, nullable=False),
        sa.Column('status', sa.String(32), primary_key=False, nullable=False),
        sa.Column('allowed_actions', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('approved_digest', sa.String(64), primary_key=False, nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('approval_expires_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('credit_cost', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('charged_credits', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('reservation_id', sa.String(64), sa.ForeignKey('service_credit_reservations.id'), primary_key=False, nullable=True),
        sa.Column('receipt', sa.JSON(), primary_key=False, nullable=True),
        sa.Column('error_code', sa.String(80), primary_key=False, nullable=True),
        sa.Column('error_message', sa.String(500), primary_key=False, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.UniqueConstraint('user_id', 'idempotency_key', name='uq_employer_application_user_key'),
    )
    op.create_index('ix_employer_application_user_created', 'employer_applications', ['user_id', 'created_at'], unique=False)
    op.create_index('ix_employer_applications_active_key', 'employer_applications', ['active_key'], unique=True)
    op.create_index('ix_employer_applications_posting_id', 'employer_applications', ['posting_id'], unique=False)
    op.create_index('ix_employer_applications_user_id', 'employer_applications', ['user_id'], unique=False)

    op.create_table('employer_application_attempts',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('application_id', sa.String(64), sa.ForeignKey('employer_applications.id'), primary_key=False, nullable=False),
        sa.Column('launch_token', sa.String(64), primary_key=False, nullable=False),
        sa.Column('package_digest', sa.String(64), primary_key=False, nullable=False),
        sa.Column('state', sa.String(24), primary_key=False, nullable=False),
        sa.Column('response_status', sa.Integer(), primary_key=False, nullable=True),
        sa.Column('receipt', sa.JSON(), primary_key=False, nullable=True),
        sa.Column('error_code', sa.String(80), primary_key=False, nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.UniqueConstraint('launch_token'),
    )
    op.create_index('ix_employer_application_attempts_application_id', 'employer_application_attempts', ['application_id'], unique=False)

    op.create_table('employer_application_approvals',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('application_id', sa.String(64), sa.ForeignKey('employer_applications.id'), nullable=False),
        sa.Column('package_digest', sa.String(64), nullable=False),
        sa.Column('review_snapshot', sa.JSON(), nullable=False),
        sa.Column('allowed_actions', sa.JSON(), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_employer_application_approvals_application_id', 'employer_application_approvals', ['application_id'])
    op.create_table('employer_artifact_deletions',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('gcs_object', sa.Text(), nullable=False),
        sa.Column('gcs_generation', sa.String(80), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE INDEX ix_employer_postings_fulltext ON employer_postings USING GIN (to_tsvector('simple'::regconfig, coalesce(title, '') || ' ' || coalesce(description, '')))")


def downgrade():
    op.drop_table('employer_artifact_deletions')
    op.drop_table('employer_application_approvals')
    op.drop_table('employer_application_attempts')
    op.drop_table('employer_applications')
    op.drop_table('sealed_application_artifacts')
    op.drop_table('employer_job_deliveries')
    op.drop_table('employer_searches')
    op.drop_table('service_credit_reservations')
    op.drop_table('service_credit_events')
    op.drop_table('employer_postings')
    op.drop_table('employer_sources')
    op.drop_column("users", "job_service_credits")
