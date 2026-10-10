"""Indexed paid-delivery proof and bounded, resumable ordinary history projection.

Revision ID: 20261011_0015
Revises: 20261010_0014
No old history is backfilled, charged, refunded or repriced by this migration.
"""
import sqlalchemy as sa

from alembic import op

revision = "20261011_0015"
down_revision = "20261010_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employer_searches", sa.Column("history_revision", sa.BigInteger(), nullable=False, server_default="1"))
    op.create_unique_constraint("uq_employer_search_history_owner", "employer_searches", ["id", "user_id"])
    op.create_unique_constraint("uq_employer_delivery_history_identity", "employer_job_deliveries", ["id", "user_id", "search_id", "posting_id", "charged_credits", "created_at"])
    op.create_table('employer_paid_delivery_history',
        sa.Column('delivery_id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('search_id', sa.String(64), nullable=False),
        sa.Column('search_owner_id', sa.Integer(), nullable=False),
        sa.Column('posting_id', sa.String(64), nullable=False),
        sa.Column('original_charged_credits', sa.Integer(), nullable=False),
        sa.Column('delivery_created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(24), nullable=False, server_default='pending'),
        sa.Column('opening_key', sa.String(64), nullable=True),
        sa.Column('saved_ordinal', sa.BigInteger(), nullable=True),
        sa.Column('snapshot_charged_credits', sa.Integer(), nullable=True),
        sa.Column('content_digest', sa.String(64), nullable=True),
        sa.Column('source_revision', sa.BigInteger(), nullable=True),
        sa.Column('static_reason', sa.String(64), nullable=True),
        sa.Column('provenance', sa.String(24), nullable=False),
        sa.Column('identity_proof', sa.JSON(), nullable=True),
        sa.Column('review_required', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('review_reason', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['posting_id'], ['employer_postings.id'], name=None),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=None),
        sa.CheckConstraint('content_digest IS NULL OR length(content_digest) = 64', name='ck_paid_history_digest'),
        sa.CheckConstraint('identity_proof IS NULL OR length(CAST(identity_proof AS TEXT)) <= 16384', name='ck_paid_history_identity_size'),
        sa.CheckConstraint("state != 'proven' OR provenance != 'new_search' OR (identity_proof IS NOT NULL AND CAST(identity_proof AS TEXT) != 'null')", name='ck_paid_history_new_identity'),
        sa.CheckConstraint('saved_ordinal IS NULL OR saved_ordinal >= 0', name='ck_paid_history_ordinal'),
        sa.CheckConstraint("state != 'proven' OR (search_owner_id = user_id AND original_charged_credits > 0 AND snapshot_charged_credits IS NOT NULL AND snapshot_charged_credits = original_charged_credits AND opening_key IS NOT NULL AND length(opening_key) = 64 AND saved_ordinal IS NOT NULL AND content_digest IS NOT NULL AND source_revision IS NOT NULL AND static_reason IS NULL)", name='ck_paid_history_proven'),
        sa.CheckConstraint("provenance IN ('legacy_backfill','new_search')", name='ck_paid_history_provenance'),
        sa.CheckConstraint('(review_required = false AND review_reason IS NULL) OR (review_required = true AND review_reason IS NOT NULL)', name='ck_paid_history_review_reason'),
        sa.CheckConstraint('source_revision IS NULL OR source_revision >= 1', name='ck_paid_history_source_revision'),
        sa.CheckConstraint("state IN ('pending','proven','unresolved')", name='ck_paid_history_state'),
        sa.CheckConstraint("state != 'unresolved' OR static_reason IS NOT NULL", name='ck_paid_history_unresolved'),
        sa.ForeignKeyConstraint(['delivery_id', 'user_id', 'search_id', 'posting_id', 'original_charged_credits', 'delivery_created_at'], ['employer_job_deliveries.id', 'employer_job_deliveries.user_id', 'employer_job_deliveries.search_id', 'employer_job_deliveries.posting_id', 'employer_job_deliveries.charged_credits', 'employer_job_deliveries.created_at'], name='fk_paid_history_delivery_identity'),
        sa.ForeignKeyConstraint(['search_id', 'search_owner_id'], ['employer_searches.id', 'employer_searches.user_id'], name='fk_paid_history_search_owner'),
        sa.UniqueConstraint('user_id', 'delivery_id', 'opening_key', 'state', name='uq_paid_history_registry_proof'),
    )
    op.create_index('ix_paid_history_owner_key_time', 'employer_paid_delivery_history', ['user_id', 'opening_key', 'state', 'delivery_created_at', 'delivery_id'])
    op.create_index('ix_paid_history_owner_time', 'employer_paid_delivery_history', ['user_id', 'delivery_created_at', 'delivery_id'])
    op.create_index('ix_paid_history_search_state', 'employer_paid_delivery_history', ['search_id', 'state', 'delivery_id'])
    op.create_table('employer_paid_opening_registry',
        sa.Column('user_id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('opening_key', sa.String(64), primary_key=True, nullable=False),
        sa.Column('canonical_delivery_id', sa.String(64), nullable=False),
        sa.Column('history_state', sa.String(24), nullable=False, server_default='proven'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=None),
        sa.CheckConstraint('length(opening_key) = 64', name='ck_paid_registry_opening'),
        sa.CheckConstraint("history_state = 'proven'", name='ck_paid_registry_proven'),
        sa.ForeignKeyConstraint(['user_id', 'canonical_delivery_id', 'opening_key', 'history_state'], ['employer_paid_delivery_history.user_id', 'employer_paid_delivery_history.delivery_id', 'employer_paid_delivery_history.opening_key', 'employer_paid_delivery_history.state'], name='fk_paid_registry_proven_history'),
    )
    op.create_table('employer_paid_history_progress',
        sa.Column('user_id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('generation', sa.BigInteger(), nullable=False, server_default='1'),
        sa.Column('state', sa.String(24), nullable=False, server_default='pending'),
        sa.Column('checkpoint_created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('checkpoint_delivery_id', sa.String(64), nullable=True),
        sa.Column('highwater_created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('highwater_delivery_id', sa.String(64), nullable=True),
        sa.Column('examined_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('proven_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('unresolved_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=None),
        sa.CheckConstraint('(checkpoint_created_at IS NULL) = (checkpoint_delivery_id IS NULL)', name='ck_paid_progress_checkpoint'),
        sa.CheckConstraint('examined_count >= 0 AND proven_count >= 0 AND unresolved_count >= 0 AND proven_count + unresolved_count <= examined_count', name='ck_paid_progress_counts'),
        sa.CheckConstraint('generation >= 1', name='ck_paid_progress_generation'),
        sa.CheckConstraint('(highwater_created_at IS NULL) = (highwater_delivery_id IS NULL)', name='ck_paid_progress_highwater'),
        sa.CheckConstraint("state != 'ready' OR unresolved_count = 0", name='ck_paid_progress_ready'),
        sa.CheckConstraint("state IN ('pending','backfilling','ready','unresolved')", name='ck_paid_progress_state'),
    )
    op.create_table('employer_search_history_progress',
        sa.Column('search_id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('generation', sa.BigInteger(), nullable=False, server_default='1'),
        sa.Column('stream_generation', sa.BigInteger(), nullable=False, server_default='1'),
        sa.Column('source_revision', sa.BigInteger(), nullable=False, server_default='1'),
        sa.Column('state', sa.String(24), nullable=False, server_default='pending'),
        sa.Column('source_size_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('byte_offset', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('next_ordinal', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('parser_checkpoint', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('source_sha256', sa.String(64), nullable=True),
        sa.Column('unresolved_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('source_sha256 IS NULL OR length(source_sha256) = 64', name='ck_search_history_progress_digest'),
        sa.CheckConstraint('generation >= 1 AND stream_generation >= 1 AND source_revision >= 1', name='ck_search_history_progress_generation'),
        sa.CheckConstraint('length(CAST(parser_checkpoint AS TEXT)) <= 8192', name='ck_search_history_progress_parser_size'),
        sa.CheckConstraint("state IN ('pending','scanning','reducing','complete','unresolved')", name='ck_search_history_progress_state'),
        sa.CheckConstraint('source_size_bytes >= 0 AND byte_offset >= 0 AND byte_offset <= source_size_bytes AND next_ordinal >= 0 AND unresolved_count >= 0', name='ck_search_history_progress_work'),
        sa.ForeignKeyConstraint(['search_id', 'user_id'], ['employer_searches.id', 'employer_searches.user_id'], name='fk_search_history_progress_owner'),
    )
    op.create_index('ix_search_history_progress_owner_state', 'employer_search_history_progress', ['user_id', 'state', 'search_id'])
    op.create_table('employer_search_history_seen',
        sa.Column('search_id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('generation', sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column('posting_id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('first_ordinal', sa.BigInteger(), nullable=False),
        sa.Column('occurrence_count', sa.BigInteger(), nullable=False, server_default='1'),
        sa.Column('content_digest', sa.String(64), nullable=True),
        sa.Column('opening_key', sa.String(64), nullable=True),
        sa.Column('facts', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('static_reason', sa.String(64), nullable=True),
        sa.Column('conflicting', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['search_id'], ['employer_search_history_progress.search_id'], name=None),
        sa.CheckConstraint('generation >= 1 AND first_ordinal >= 0 AND occurrence_count >= 1', name='ck_search_history_seen_counts'),
        sa.CheckConstraint('content_digest IS NULL OR length(content_digest) = 64', name='ck_search_history_seen_digest'),
        sa.CheckConstraint('length(CAST(facts AS TEXT)) <= 4096', name='ck_search_history_seen_facts_size'),
    )
    op.create_table('employer_search_history_nodes',
        sa.Column('search_id', sa.String(64), primary_key=True, nullable=False),
        sa.Column('generation', sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column('node_id', sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column('parent_node_id', sa.BigInteger(), nullable=True),
        sa.Column('member_key_hash', sa.String(64), nullable=True),
        sa.Column('member_key', sa.String(64), nullable=True),
        sa.Column('member_index', sa.BigInteger(), nullable=True),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('parser_expect', sa.String(24), nullable=True),
        sa.Column('content_digest', sa.String(64), nullable=True),
        sa.Column('small_value', sa.JSON(), nullable=True),
        sa.Column('hash_checkpoint', sa.JSON(), nullable=True),
        sa.Column('child_reduce_cursor', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('child_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['search_id'], ['employer_search_history_progress.search_id'], name=None),
        sa.CheckConstraint('generation >= 1 AND node_id >= 0 AND child_reduce_cursor >= 0 AND child_count >= 0 AND (member_index IS NULL OR member_index >= 0)', name='ck_search_history_node_counts'),
        sa.CheckConstraint('content_digest IS NULL OR length(content_digest) = 64', name='ck_search_history_node_digest'),
        sa.CheckConstraint('hash_checkpoint IS NULL OR length(CAST(hash_checkpoint AS TEXT)) <= 4096', name='ck_search_history_node_hash_size'),
        sa.CheckConstraint('member_key_hash IS NULL OR length(member_key_hash) = 64', name='ck_search_history_node_key_digest'),
        sa.CheckConstraint("kind IN ('object','array','string','number','boolean','null')", name='ck_search_history_node_kind'),
        sa.CheckConstraint('parent_node_id IS NULL OR (parent_node_id >= 0 AND parent_node_id < node_id)', name='ck_search_history_node_parent'),
        sa.CheckConstraint('small_value IS NULL OR length(CAST(small_value AS TEXT)) <= 4096', name='ck_search_history_node_value_size'),
        sa.ForeignKeyConstraint(['search_id', 'generation', 'parent_node_id'], ['employer_search_history_nodes.search_id', 'employer_search_history_nodes.generation', 'employer_search_history_nodes.node_id'], name='fk_search_history_node_parent'),
    )
    op.create_index('ix_search_history_nodes_array', 'employer_search_history_nodes', ['search_id', 'generation', 'parent_node_id', 'member_index'])
    op.create_index('ix_search_history_nodes_key', 'employer_search_history_nodes', ['search_id', 'generation', 'parent_node_id', 'member_key_hash'])
    op.create_index('ix_search_history_nodes_parent', 'employer_search_history_nodes', ['search_id', 'generation', 'parent_node_id', 'node_id'])
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        schema = connection.scalar(sa.text("SELECT current_schema()"))
        namespace = connection.dialect.identifier_preparer.quote(schema)
        connection.execute(sa.text(f'''CREATE FUNCTION {namespace}.employer_search_history_invalidate() RETURNS trigger
        LANGUAGE plpgsql SET search_path = {namespace}, pg_catalog AS $$
        BEGIN
          PERFORM id FROM users WHERE id IN (OLD.user_id, NEW.user_id)
          ORDER BY id FOR KEY SHARE NOWAIT;
          PERFORM user_id FROM employer_paid_history_progress
          WHERE user_id IN (OLD.user_id, NEW.user_id)
          ORDER BY user_id FOR UPDATE NOWAIT;
          NEW.history_revision := OLD.history_revision + 1;
          UPDATE employer_paid_history_progress SET state = 'pending', generation = generation + 1,
            updated_at = clock_timestamp()
          WHERE user_id IN (OLD.user_id, NEW.user_id);
          RETURN NEW;
        EXCEPTION WHEN lock_not_available THEN
          RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'Saved history source is busy. Retry the source update.';
        END $$'''))
        connection.execute(sa.text(f'''CREATE TRIGGER employer_search_history_revision
        BEFORE UPDATE OF items,user_id,history_revision ON {namespace}.employer_searches
        FOR EACH ROW EXECUTE FUNCTION {namespace}.employer_search_history_invalidate()'''))
        connection.execute(sa.text(f'''CREATE FUNCTION {namespace}.employer_paid_history_delivery_invalidate() RETURNS trigger
        LANGUAGE plpgsql SET search_path = {namespace}, pg_catalog AS $$
        BEGIN
          PERFORM id FROM users WHERE id = NEW.user_id FOR KEY SHARE;
          UPDATE employer_paid_history_progress SET state = 'pending', generation = generation + 1,
            updated_at = clock_timestamp() WHERE user_id = NEW.user_id;
          RETURN NEW;
        END $$'''))
        connection.execute(sa.text(f'''CREATE TRIGGER employer_paid_history_delivery_insert
        AFTER INSERT ON {namespace}.employer_job_deliveries
        FOR EACH ROW WHEN (NEW.charged_credits > 0)
        EXECUTE FUNCTION {namespace}.employer_paid_history_delivery_invalidate()'''))


def downgrade():
    connection = op.get_bind()
    # Retained projection/provenance and durable work cannot be silently discarded.
    for table in ('employer_paid_delivery_history', 'employer_paid_opening_registry', 'employer_paid_history_progress', 'employer_search_history_progress', 'employer_search_history_seen', 'employer_search_history_nodes'):
        if connection.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("Cannot downgrade retained paid-delivery history or resumable history work")
    if connection.dialect.name == "postgresql":
        schema = connection.scalar(sa.text("SELECT current_schema()"))
        namespace = connection.dialect.identifier_preparer.quote(schema)
        connection.execute(sa.text(f"DROP TRIGGER employer_search_history_revision ON {namespace}.employer_searches"))
        connection.execute(sa.text(f"DROP FUNCTION {namespace}.employer_search_history_invalidate()"))
        connection.execute(sa.text(f"DROP TRIGGER employer_paid_history_delivery_insert ON {namespace}.employer_job_deliveries"))
        connection.execute(sa.text(f"DROP FUNCTION {namespace}.employer_paid_history_delivery_invalidate()"))
    op.drop_table('employer_search_history_nodes')
    op.drop_table('employer_search_history_seen')
    op.drop_table('employer_search_history_progress')
    op.drop_table('employer_paid_history_progress')
    op.drop_table('employer_paid_opening_registry')
    op.drop_table('employer_paid_delivery_history')
    op.drop_constraint("uq_employer_delivery_history_identity", "employer_job_deliveries", type_="unique")
    op.drop_constraint("uq_employer_search_history_owner", "employer_searches", type_="unique")
    op.drop_column("employer_searches", "history_revision")
