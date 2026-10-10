"""Pinned public catalogue revisions and bounded durable search preparation.

Revision ID: 20261011_0016
Revises: 20261011_0015
No search charge, financial history rewrite or catalogue backfill occurs here.
"""
import sqlalchemy as sa
from alembic import op

revision = "20261011_0016"
down_revision = "20261011_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('employer_catalogue_clock',
        sa.CheckConstraint('id = 1 AND watermark >= 0', name='ck_catalogue_clock_singleton'),
        sa.Column('id', sa.Integer, primary_key=True, default=1),
        sa.Column('watermark', sa.BigInteger, nullable=False, default=0, server_default='0'),
        sa.Column('inventory_state', sa.String(16), nullable=False, default='sources', server_default='sources'),
        sa.Column('source_cursor', sa.String(64), nullable=True),
        sa.Column('posting_cursor', sa.String(64), nullable=True),
        sa.Column('prune_cursor', sa.String(64), nullable=True)
    )
    op.create_table('employer_catalogue_source_revisions',
        sa.CheckConstraint('valid_from >= 0 AND (valid_to IS NULL OR valid_to > valid_from)', name='ck_catalogue_source_interval'),
        sa.Column('source_id', sa.String(64), primary_key=True),
        sa.Column('valid_from', sa.BigInteger, primary_key=True),
        sa.Column('valid_to', sa.BigInteger, nullable=True),
        sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('platform', sa.String(40), nullable=False),
        sa.Column('region', sa.String(16), nullable=False),
        sa.Column('board_token', sa.String(120), nullable=False),
        sa.Column('employer_key', sa.String(120), nullable=True),
        sa.Column('employer', sa.String(200), nullable=False)
    )
    op.create_table('employer_catalogue_contents',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('preference_metadata', sa.JSON, nullable=True),
        sa.Column('public_fields', sa.JSON, nullable=False)
    )
    op.create_table('employer_catalogue_projections',
        sa.CheckConstraint("projection_state IN ('pending','preparing','ready')", name='ck_catalogue_projection_projection_state'),
        sa.CheckConstraint("projection_state <> 'ready' OR (required_skills IS NOT NULL AND CAST(required_skills AS TEXT) <> 'null' AND scoring_warnings IS NOT NULL AND CAST(scoring_warnings AS TEXT) <> 'null' AND preference_valid IS NOT NULL AND preference_facts IS NOT NULL AND CAST(preference_facts AS TEXT) <> 'null')", name='ck_catalogue_projection_ready'),
        sa.CheckConstraint('projection_offset >= 0', name='ck_catalogue_projection_offset'),
        sa.CheckConstraint('length(CAST(required_skills AS TEXT)) <= 16384', name='ck_catalogue_projection_skills_size'),
        sa.CheckConstraint('length(CAST(scoring_warnings AS TEXT)) <= 16384', name='ck_catalogue_projection_warnings_size'),
        sa.CheckConstraint('length(CAST(preference_facts AS TEXT)) <= 8192', name='ck_catalogue_projection_facts_size'),
        sa.CheckConstraint('length(CAST(projection_checkpoint AS TEXT)) <= 3145728', name='ck_catalogue_projection_checkpoint_size'),
        sa.Column('content_id', sa.String(64), sa.ForeignKey('employer_catalogue_contents.id'), primary_key=True),
        sa.Column('projection_version', sa.String(160), primary_key=True),
        sa.Column('projection_state', sa.String(16), nullable=False, default='pending', server_default='pending'),
        sa.Column('required_skills', sa.JSON, nullable=True),
        sa.Column('scoring_warnings', sa.JSON, nullable=True),
        sa.Column('preference_valid', sa.Boolean, nullable=True),
        sa.Column('preference_provider', sa.String(40), nullable=True),
        sa.Column('preference_facts', sa.JSON, nullable=True),
        sa.Column('projection_phase', sa.String(16), nullable=False, default='description', server_default='description'),
        sa.Column('projection_offset', sa.BigInteger, nullable=False, default=0, server_default='0'),
        sa.Column('projection_checkpoint', sa.JSON, nullable=True),
        sa.Column('lease_token', sa.String(64), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table('employer_catalogue_posting_revisions',
        sa.CheckConstraint('valid_from >= 0 AND (valid_to IS NULL OR valid_to > valid_from)', name='ck_catalogue_posting_interval'),
        sa.Column('posting_id', sa.String(64).with_variant(sa.String(64, collation='C'), 'postgresql'), primary_key=True),
        sa.Column('valid_from', sa.BigInteger, primary_key=True),
        sa.Column('valid_to', sa.BigInteger, nullable=True),
        sa.Column('content_id', sa.String(64), sa.ForeignKey('employer_catalogue_contents.id'), nullable=False),
        sa.Column('source_id', sa.String(64), nullable=False),
        sa.Column('external_id', sa.String(160), nullable=False),
        sa.Column('requisition_id', sa.String(160), nullable=True),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('employer', sa.String(200), nullable=False),
        sa.Column('location', sa.String(400), nullable=False),
        sa.Column('remote', sa.Boolean, nullable=False),
        sa.Column('is_open', sa.Boolean, nullable=False),
        sa.Column('publication_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False)
    )
    op.create_table('employer_catalogue_resume_bindings',
        sa.CheckConstraint('revision >= 1', name='ck_catalogue_resume_binding_revision'),
        sa.Column('resume_id', sa.Integer, sa.ForeignKey('resumes.id', ondelete='CASCADE', onupdate='CASCADE'), primary_key=True),
        sa.Column('revision', sa.BigInteger, nullable=False, default=1, server_default='1')
    )
    op.create_table('employer_search_preparations',
        sa.UniqueConstraint('user_id', 'idempotency_key', name='uq_search_preparation_owner_key'),
        sa.UniqueConstraint('id', 'user_id', name='uq_search_preparation_owner'),
        sa.CheckConstraint("state IN ('accepting','pending','scanning','classifying','selecting','completed','failed','cancelled')", name='ck_search_preparation_state'),
        sa.CheckConstraint("resume_offset >= 0 AND (resume_checkpoint IS NULL OR length(CAST(resume_checkpoint AS TEXT)) <= 16384)", name='ck_search_preparation_resume_checkpoint'),
        sa.CheckConstraint("state = 'accepting' OR resume_source_revision IS NOT NULL", name='ck_search_preparation_resume_revision'),
        sa.CheckConstraint('resume_source_revision IS NULL OR resume_source_revision >= 1', name='ck_search_preparation_resume_revision_positive'),
        sa.CheckConstraint('watermark >= 0 AND desired_count BETWEEN 1 AND 100', name='ck_search_preparation_bounds'),
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('resume_id', sa.Integer, sa.ForeignKey('resumes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('reservation_id', sa.String(64), sa.ForeignKey('service_credit_reservations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('idempotency_key', sa.String(160), nullable=False),
        sa.Column('input_fingerprint', sa.String(64), nullable=False),
        sa.Column('query', sa.JSON, nullable=False),
        sa.Column('worker_query', sa.JSON, nullable=False),
        sa.Column('projection_version', sa.String(160), nullable=False),
        sa.Column('accepted_quote', sa.JSON, nullable=False),
        sa.Column('resume_fingerprint', sa.String(64), nullable=False),
        sa.Column('resume_source_revision', sa.BigInteger, nullable=True),
        sa.Column('resume_skills', sa.JSON, nullable=False),
        sa.Column('resume_offset', sa.BigInteger, nullable=False, default=0, server_default='0'),
        sa.Column('resume_checkpoint', sa.JSON, nullable=True),
        sa.Column('resume_ready', sa.Boolean, nullable=False, default=False, server_default=sa.false()),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
        sa.Column('watermark', sa.BigInteger, nullable=False),
        sa.Column('max_source_age_hours', sa.Integer, nullable=False),
        sa.Column('desired_count', sa.Integer, nullable=False),
        sa.Column('state', sa.String(16), nullable=False, default='pending', server_default='pending'),
        sa.Column('posting_cursor', sa.String(64), nullable=True),
        sa.Column('classification_cursor', sa.String(64), nullable=True),
        sa.Column('history_generation', sa.BigInteger, nullable=True),
        sa.Column('candidates_before_preferences', sa.BigInteger, nullable=False, default=0, server_default='0'),
        sa.Column('known_preference_conflicts', sa.BigInteger, nullable=False, default=0, server_default='0'),
        sa.Column('candidates_with_unknown_preferences', sa.BigInteger, nullable=False, default=0, server_default='0'),
        sa.Column('eligible_candidates', sa.BigInteger, nullable=False, default=0, server_default='0'),
        sa.Column('work_generation', sa.BigInteger, nullable=False, default=1, server_default='1'),
        sa.Column('lease_token', sa.String(64), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_reason', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table('employer_catalogue_snapshots',
        sa.UniqueConstraint('id', 'user_id', name='uq_catalogue_snapshot_owner'),
        sa.ForeignKeyConstraint(['preparation_id', 'user_id'], ['employer_search_preparations.id', 'employer_search_preparations.user_id'], name='fk_catalogue_snapshot_preparation_owner', ondelete='CASCADE'),
        sa.CheckConstraint("state IN ('building','proven')", name='ck_catalogue_snapshot_state'),
        sa.CheckConstraint('item_count BETWEEN 0 AND 100 AND history_generation >= 0', name='ck_catalogue_snapshot_bounds'),
        sa.CheckConstraint("state <> 'proven' OR (items IS NOT NULL AND CAST(items AS TEXT) <> 'null' AND snapshot_digest IS NOT NULL AND length(snapshot_digest) = 64)", name='ck_catalogue_snapshot_proven'),
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.Integer, nullable=False),
        sa.Column('preparation_id', sa.String(64), nullable=False),
        sa.Column('query', sa.JSON, nullable=False),
        sa.Column('items', sa.JSON, nullable=True),
        sa.Column('state', sa.String(16), nullable=False, default='building', server_default='building'),
        sa.Column('item_count', sa.Integer, nullable=False, default=0, server_default='0'),
        sa.Column('snapshot_digest', sa.String(64), nullable=True),
        sa.Column('history_generation', sa.BigInteger, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False)
    )
    op.create_table('employer_catalogue_snapshot_items',
        sa.ForeignKeyConstraint(['snapshot_id', 'user_id'], ['employer_catalogue_snapshots.id', 'employer_catalogue_snapshots.user_id'], name='fk_catalogue_snapshot_item_owner', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['posting_id', 'posting_revision'], ['employer_catalogue_posting_revisions.posting_id', 'employer_catalogue_posting_revisions.valid_from'], name='fk_catalogue_snapshot_item_posting_revision'),
        sa.ForeignKeyConstraint(['source_id', 'source_revision'], ['employer_catalogue_source_revisions.source_id', 'employer_catalogue_source_revisions.valid_from'], name='fk_catalogue_snapshot_item_source_revision'),
        sa.CheckConstraint('ordinal BETWEEN 0 AND 99 AND unit_price >= 0', name='ck_catalogue_snapshot_item_bounds'),
        sa.CheckConstraint("proof_state IN ('prepared','proven')", name='ck_catalogue_snapshot_item_proof_state'),
        sa.CheckConstraint('length(item_digest) = 64 AND length(CAST(identity_proof AS TEXT)) <= 16384', name='ck_catalogue_snapshot_item_proof_size'),
        sa.Column('snapshot_id', sa.String(64), primary_key=True),
        sa.Column('ordinal', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Integer, nullable=False),
        sa.Column('posting_id', sa.String(64).with_variant(sa.String(64, collation='C'), 'postgresql'), nullable=False),
        sa.Column('posting_revision', sa.BigInteger, nullable=False),
        sa.Column('source_id', sa.String(64), nullable=False),
        sa.Column('source_revision', sa.BigInteger, nullable=False),
        sa.Column('opening_key', sa.String(64), nullable=False),
        sa.Column('seen', sa.Boolean, nullable=False),
        sa.Column('unit_price', sa.Integer, nullable=False),
        sa.Column('item_digest', sa.String(64), nullable=False),
        sa.Column('identity_proof', sa.JSON, nullable=False),
        sa.Column('body', sa.JSON, nullable=False),
        sa.Column('proof_state', sa.String(16), nullable=False, default='prepared', server_default='prepared')
    )
    op.add_column('employer_searches', sa.Column('native_snapshot_id', sa.String(64), nullable=True))
    with op.batch_alter_table('employer_searches') as batch:
        batch.create_foreign_key('fk_employer_search_native_snapshot_owner', 'employer_catalogue_snapshots',
                                 ['native_snapshot_id', 'user_id'], ['id', 'user_id'])
    op.create_index('ix_employer_search_native_snapshot', 'employer_searches', ['native_snapshot_id'])
    op.create_index('ix_catalogue_snapshot_preparation', 'employer_catalogue_snapshots', ['preparation_id', 'history_generation', 'created_at', 'id'])
    op.create_table('employer_catalogue_winners',
        sa.ForeignKeyConstraint(['posting_id', 'posting_revision'], ['employer_catalogue_posting_revisions.posting_id', 'employer_catalogue_posting_revisions.valid_from'], name='fk_catalogue_winner_posting_revision'),
        sa.ForeignKeyConstraint(['source_id', 'source_revision'], ['employer_catalogue_source_revisions.source_id', 'employer_catalogue_source_revisions.valid_from'], name='fk_catalogue_winner_source_revision'),
        sa.CheckConstraint('score_tenths BETWEEN 0 AND 1000', name='ck_catalogue_winner_score'),
        sa.CheckConstraint('length(CAST(fit AS TEXT)) <= 16384 AND length(CAST(preference_evaluation AS TEXT)) <= 8192', name='ck_catalogue_winner_result_size'),
        sa.Column('preparation_id', sa.String(64), sa.ForeignKey('employer_search_preparations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('opening_key', sa.String(64), primary_key=True),
        sa.Column('posting_id', sa.String(64).with_variant(sa.String(64, collation='C'), 'postgresql'), nullable=False),
        sa.Column('posting_revision', sa.BigInteger, nullable=False),
        sa.Column('source_id', sa.String(64), nullable=False),
        sa.Column('source_revision', sa.BigInteger, nullable=False),
        sa.Column('score_tenths', sa.Integer, nullable=False),
        sa.Column('title_hit', sa.Boolean, nullable=False),
        sa.Column('fit', sa.JSON, nullable=False),
        sa.Column('preference_evaluation', sa.JSON, nullable=False),
        sa.Column('seen', sa.Boolean, nullable=True),
        sa.Column('classification_generation', sa.BigInteger, nullable=True),
        sa.Column('stale', sa.Boolean, nullable=False, default=False, server_default=sa.false()),
        sa.Column('stale_reason', sa.String(64), nullable=True)
    )
    op.create_index('ix_catalogue_sources_snapshot', 'employer_catalogue_source_revisions', ['source_id', 'valid_from', 'valid_to'])
    op.create_index('ix_catalogue_postings_snapshot', 'employer_catalogue_posting_revisions', ['posting_id', 'valid_from', 'valid_to'])
    op.create_index('ix_catalogue_postings_open_checked', 'employer_catalogue_posting_revisions', ['is_open', 'last_checked_at', 'posting_id'])
    op.create_index('ix_search_preparation_active_pin', 'employer_search_preparations', ['state', 'watermark', 'id'])
    op.create_index('ix_search_preparation_owner_created', 'employer_search_preparations', ['user_id', 'created_at'])
    op.create_index('ix_search_preparation_owner_lease', 'employer_search_preparations', ['user_id', 'lease_until', 'id'])
    op.create_index('ix_catalogue_winner_classify', 'employer_catalogue_winners', ['preparation_id', 'opening_key'])
    op.create_index('ix_catalogue_winner_posting_revision', 'employer_catalogue_winners', ['posting_id', 'posting_revision'])
    op.create_index('ix_catalogue_winner_source_revision', 'employer_catalogue_winners', ['source_id', 'source_revision'])
    op.create_index('ix_catalogue_snapshot_item_posting_revision', 'employer_catalogue_snapshot_items', ['posting_id', 'posting_revision'])
    op.create_index('ix_catalogue_snapshot_item_source_revision', 'employer_catalogue_snapshot_items', ['source_id', 'source_revision'])
    op.create_index("ix_catalogue_winner_selection", "employer_catalogue_winners", ["preparation_id", "classification_generation", "stale", "seen", sa.text("score_tenths DESC"), sa.text("title_hit DESC"), "posting_id"])
    op.execute(sa.text("INSERT INTO employer_catalogue_clock (id,watermark,inventory_state) VALUES (1,0,'sources')"))
    if op.get_bind().dialect.name == "postgresql":
        namespace = op.get_bind().dialect.identifier_preparer.quote(op.get_bind().scalar(sa.text("SELECT current_schema()")))
        for statement in POSTGRES_PUBLICATION_SQL:
            # Trigger functions resolve only the migration's table namespace.
            op.execute(sa.text(statement.replace("LANGUAGE plpgsql AS $$", f"LANGUAGE plpgsql SET search_path = {namespace}, pg_catalog AS $$")))
        op.execute(sa.text(f"CREATE TRIGGER employer_search_native_history_revision BEFORE UPDATE OF native_snapshot_id ON {namespace}.employer_searches FOR EACH ROW WHEN (OLD.native_snapshot_id IS DISTINCT FROM NEW.native_snapshot_id) EXECUTE FUNCTION {namespace}.employer_search_history_invalidate()"))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM employer_searches WHERE native_snapshot_id IS NOT NULL LIMIT 1")).first() is not None:
        raise RuntimeError("Refusing to discard a published native saved-search snapshot")
    for table in ("employer_catalogue_snapshot_items", "employer_catalogue_snapshots", "employer_search_preparations", "employer_catalogue_winners", "employer_catalogue_posting_revisions",
                  "employer_catalogue_source_revisions", "employer_catalogue_projections", "employer_catalogue_contents", "employer_catalogue_resume_bindings"):
        if bind.execute(sa.text("SELECT 1 FROM " + table + " LIMIT 1")).first() is not None:
            raise RuntimeError("Refusing to discard populated durable catalogue/search state")
    if bind.execute(sa.text("SELECT 1 FROM employer_catalogue_clock WHERE watermark <> 0 OR inventory_state <> 'sources' LIMIT 1")).first() is not None:
        raise RuntimeError("Refusing to discard active catalogue progress")
    if bind.dialect.name == "postgresql":
        namespace = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
        op.execute(sa.text(f"DROP TRIGGER employer_search_native_history_revision ON {namespace}.employer_searches"))
        for statement in POSTGRES_REMOVE_SQL:
            op.execute(sa.text(statement))
    op.drop_index('ix_employer_search_native_snapshot', table_name='employer_searches')
    with op.batch_alter_table('employer_searches') as batch:
        batch.drop_constraint('fk_employer_search_native_snapshot_owner', type_='foreignkey')
        batch.drop_column('native_snapshot_id')
    op.drop_table('employer_catalogue_snapshot_items')
    op.drop_table('employer_catalogue_snapshots')
    op.drop_table('employer_catalogue_winners')
    op.drop_table('employer_search_preparations')
    op.drop_table('employer_catalogue_resume_bindings')
    op.drop_table('employer_catalogue_posting_revisions')
    op.drop_table('employer_catalogue_projections')
    op.drop_table('employer_catalogue_contents')
    op.drop_table('employer_catalogue_source_revisions')
    op.drop_table('employer_catalogue_clock')

POSTGRES_PUBLICATION_SQL = [
    r"""CREATE FUNCTION employer_catalogue_resume_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      INSERT INTO employer_catalogue_resume_bindings(resume_id,revision) VALUES(NEW.id,2)
        ON CONFLICT(resume_id) DO UPDATE SET revision=employer_catalogue_resume_bindings.revision+1;
      RETURN NEW;
    END $$""",
    r"""CREATE TRIGGER employer_catalogue_resume_revision AFTER UPDATE OF skills,user_id ON resumes
        FOR EACH ROW WHEN (OLD.skills::text IS DISTINCT FROM NEW.skills::text OR OLD.user_id IS DISTINCT FROM NEW.user_id)
        EXECUTE FUNCTION employer_catalogue_resume_revision()""",
    r"""CREATE FUNCTION employer_catalogue_projection_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.projection_state='ready' AND (NEW.content_id IS DISTINCT FROM OLD.content_id
          OR NEW.projection_version IS DISTINCT FROM OLD.projection_version OR NEW.projection_state IS DISTINCT FROM OLD.projection_state
          OR NEW.required_skills::text IS DISTINCT FROM OLD.required_skills::text
          OR NEW.scoring_warnings::text IS DISTINCT FROM OLD.scoring_warnings::text
          OR NEW.preference_valid IS DISTINCT FROM OLD.preference_valid
          OR NEW.preference_provider IS DISTINCT FROM OLD.preference_provider
          OR NEW.preference_facts::text IS DISTINCT FROM OLD.preference_facts::text) THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Ready catalogue projection is immutable';
      END IF;
      RETURN NEW;
    END $$""",
    r"""CREATE TRIGGER employer_catalogue_projection_immutable BEFORE UPDATE OF content_id,projection_version,projection_state,
        required_skills,scoring_warnings,preference_valid,preference_provider,preference_facts ON employer_catalogue_projections
        FOR EACH ROW EXECUTE FUNCTION employer_catalogue_projection_immutable()""",
    r"""CREATE FUNCTION employer_catalogue_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE cached text; mark bigint; current_clock boolean; row_key text;
    BEGIN
      IF to_jsonb(NEW) = to_jsonb(OLD) THEN RETURN NEW; END IF;
      cached := current_setting('hirewiz.catalogue_watermark', true);
      IF cached IS NULL OR cached = '' THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Published catalogue revision is immutable';
      END IF;
      mark := cached::bigint;
      SELECT watermark = mark
        INTO current_clock FROM employer_catalogue_clock WHERE id=1;
      row_key := to_jsonb(OLD)->>TG_ARGV[0];
      IF NOT coalesce(current_clock, false) OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
          OR to_jsonb(NEW)->>TG_ARGV[0] IS DISTINCT FROM row_key THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Published catalogue revision identity is immutable';
      END IF;
      -- The allocator sets this transaction-local value only after incrementing
      -- the serialized clock. Savepoint publication shares the same watermark.
      -- Privileged SQL/GUC spoofing is not a security boundary of this mechanism.
      IF OLD.valid_from = mark AND NEW.valid_to IS NOT DISTINCT FROM OLD.valid_to THEN
        RETURN NEW;
      END IF;
      IF OLD.valid_to IS NULL AND NEW.valid_to = mark AND OLD.valid_from < mark
          AND (to_jsonb(NEW)-'valid_to') = (to_jsonb(OLD)-'valid_to') THEN RETURN NEW; END IF;
      RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Published catalogue revision fields are immutable';
    END $$""",
    r"""CREATE TRIGGER employer_catalogue_source_revision_immutable BEFORE UPDATE ON employer_catalogue_source_revisions
        FOR EACH ROW EXECUTE FUNCTION employer_catalogue_revision_immutable('source_id')""",
    r"""CREATE TRIGGER employer_catalogue_posting_revision_immutable BEFORE UPDATE ON employer_catalogue_posting_revisions
        FOR EACH ROW EXECUTE FUNCTION employer_catalogue_revision_immutable('posting_id')""",
    r"""CREATE FUNCTION employer_catalogue_snapshot_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE item_total integer; proven_total integer; first_ordinal integer; last_ordinal integer; aggregate_body jsonb; desired integer; accepted_query jsonb;
    BEGIN
      IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'building' THEN
          RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Native saved-search snapshot must be assembled before proving';
        END IF;
        RETURN NEW;
      END IF;
      IF TG_OP = 'DELETE' THEN
        IF EXISTS(SELECT 1 FROM employer_searches WHERE native_snapshot_id=OLD.id) THEN
          RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Published saved-search snapshot is retained';
        END IF;
        RETURN OLD;
      END IF;
      IF OLD.state = 'proven' THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Proven saved-search snapshot is immutable';
      END IF;
      IF NEW.id IS DISTINCT FROM OLD.id OR NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.preparation_id IS DISTINCT FROM OLD.preparation_id THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Native saved-search snapshot identity is immutable';
      END IF;
      IF NEW.state = 'proven' THEN
        SELECT desired_count,query::jsonb INTO desired,accepted_query FROM employer_search_preparations WHERE id=NEW.preparation_id AND user_id=NEW.user_id;
        SELECT count(*),count(*) FILTER(WHERE proof_state='proven'),min(ordinal),max(ordinal),
            coalesce(jsonb_agg(body::jsonb ORDER BY ordinal),'[]'::jsonb)
          INTO item_total,proven_total,first_ordinal,last_ordinal,aggregate_body
          FROM employer_catalogue_snapshot_items WHERE snapshot_id=NEW.id AND user_id=NEW.user_id;
        IF desired IS NULL OR NEW.query::jsonb IS DISTINCT FROM accepted_query OR item_total > desired OR item_total <> NEW.item_count OR proven_total <> item_total
            OR (item_total > 0 AND (first_ordinal <> 0 OR last_ordinal <> item_total-1))
            OR NEW.items::jsonb IS DISTINCT FROM aggregate_body
            OR NEW.snapshot_digest IS DISTINCT FROM encode(sha256(convert_to(jsonb_build_object('query',NEW.query::jsonb,'items',aggregate_body)::text,'UTF8')),'hex') THEN
          RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Native saved-search snapshot proof is incomplete';
        END IF;
      END IF;
      RETURN NEW;
    END $$""",
    r"""CREATE TRIGGER employer_catalogue_snapshot_immutable BEFORE INSERT OR UPDATE OR DELETE ON employer_catalogue_snapshots
        FOR EACH ROW EXECUTE FUNCTION employer_catalogue_snapshot_immutable()""",
    r"""CREATE FUNCTION employer_catalogue_snapshot_item_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE parent_id text; parent_owner integer; parent_state text; expected_external text; expected_platform text; expected_recipe jsonb;
    BEGIN
      IF TG_OP='DELETE' THEN parent_id:=OLD.snapshot_id; parent_owner:=OLD.user_id;
      ELSE parent_id:=NEW.snapshot_id; parent_owner:=NEW.user_id; END IF;
      SELECT state INTO parent_state FROM employer_catalogue_snapshots
        WHERE id=parent_id AND user_id=parent_owner FOR UPDATE NOWAIT;
      IF TG_OP='DELETE' THEN
        IF EXISTS(SELECT 1 FROM employer_searches WHERE native_snapshot_id=parent_id) THEN
          RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Published saved-search item is retained';
        END IF;
        -- A parent FK cascade may already have removed the snapshot row.
        RETURN OLD;
      END IF;
      IF parent_state IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Native item snapshot owner is unavailable';
      END IF;
      IF TG_OP='UPDATE' AND (NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id OR NEW.user_id IS DISTINCT FROM OLD.user_id
          OR NEW.ordinal IS DISTINCT FROM OLD.ordinal) THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Native item snapshot identity is immutable';
      END IF;
      IF parent_state='proven' THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Proven saved-search item is immutable';
      END IF;
      SELECT p.external_id,s.platform,jsonb_build_object(
          'recipe_version','employer-opening-identity@7cc49c2c08a296b33cb30f96e383b959caf214c0',
          'source_id',s.source_id,'platform',s.platform,'region',s.region,'tenant',s.board_token,
          'verified_employer_key',s.employer_key,'requisition_id',p.requisition_id,'external_id',p.external_id)
        INTO expected_external,expected_platform,expected_recipe
        FROM employer_catalogue_posting_revisions p JOIN employer_catalogue_source_revisions s ON s.source_id=p.source_id
        WHERE p.posting_id=NEW.posting_id AND p.valid_from=NEW.posting_revision
          AND s.source_id=NEW.source_id AND s.valid_from=NEW.source_revision;
      IF expected_recipe IS NULL OR NEW.identity_proof::jsonb IS DISTINCT FROM expected_recipe
          OR NEW.body::jsonb#>>'{posting,id}' IS DISTINCT FROM NEW.posting_id
          OR NEW.body::jsonb#>>'{posting,source_id}' IS DISTINCT FROM NEW.source_id
          OR NEW.body::jsonb#>>'{posting,opening_key}' IS DISTINCT FROM NEW.opening_key
          OR NEW.body::jsonb#>>'{posting,external_id}' IS DISTINCT FROM expected_external
          OR NEW.body::jsonb#>>'{posting,platform}' IS DISTINCT FROM expected_platform
          OR jsonb_typeof(NEW.body::jsonb->'charged_credits') IS DISTINCT FROM 'number'
          OR NEW.body::jsonb->>'charged_credits' IS DISTINCT FROM NEW.unit_price::text THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Native saved-search item identity is invalid';
      END IF;
      IF NEW.proof_state='proven' AND NEW.item_digest IS DISTINCT FROM encode(sha256(convert_to(NEW.body::jsonb::text,'UTF8')),'hex') THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Native saved-search item digest is invalid';
      END IF;
      RETURN NEW;
    EXCEPTION WHEN lock_not_available THEN
      RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Native item snapshot is busy';
    END $$""",
    r"""CREATE TRIGGER employer_catalogue_snapshot_item_immutable BEFORE INSERT OR UPDATE OR DELETE ON employer_catalogue_snapshot_items
        FOR EACH ROW EXECUTE FUNCTION employer_catalogue_snapshot_item_immutable()""",
    r"""CREATE FUNCTION employer_search_native_snapshot_ready() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE snapshot_state text; snapshot_count integer; preparation text; accepted_count integer;
    BEGIN
      IF NEW.native_snapshot_id IS NULL THEN RETURN NEW; END IF;
      SELECT state,item_count,preparation_id INTO snapshot_state,snapshot_count,preparation FROM employer_catalogue_snapshots
        WHERE id=NEW.native_snapshot_id AND user_id=NEW.user_id FOR KEY SHARE NOWAIT;
      SELECT desired_count INTO accepted_count FROM employer_search_preparations WHERE id=preparation AND user_id=NEW.user_id;
      IF snapshot_state IS DISTINCT FROM 'proven' OR snapshot_count IS DISTINCT FROM NEW.delivered_count
          OR accepted_count IS DISTINCT FROM NEW.desired_count OR snapshot_count > accepted_count THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Completed saved-search snapshot is not proven for its owner';
      END IF;
      RETURN NEW;
    EXCEPTION WHEN lock_not_available THEN
      RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Completed saved-search snapshot is busy';
    END $$""",
    r"""CREATE TRIGGER employer_search_native_snapshot_ready BEFORE INSERT OR UPDATE OF native_snapshot_id,user_id ON employer_searches
        FOR EACH ROW EXECUTE FUNCTION employer_search_native_snapshot_ready()""",
    r"""CREATE FUNCTION employer_catalogue_watermark() RETURNS bigint LANGUAGE plpgsql AS $$
    DECLARE mark bigint; cached text;
    BEGIN
      cached := current_setting('hirewiz.catalogue_watermark', true);
      IF cached IS NOT NULL AND cached <> '' THEN RETURN cached::bigint; END IF;
      UPDATE employer_catalogue_clock SET watermark = watermark + 1 WHERE id = 1 RETURNING watermark INTO mark;
      IF mark IS NULL THEN RAISE EXCEPTION 'Catalogue clock is unavailable'; END IF;
      PERFORM set_config('hirewiz.catalogue_watermark', mark::text, true);
      RETURN mark;
    END $$""",
    r"""CREATE FUNCTION employer_catalogue_content(row_value employer_postings) RETURNS text LANGUAGE plpgsql AS $$
    DECLARE body jsonb; content_key text;
    BEGIN
      body := jsonb_build_object('canonical_url', row_value.canonical_url, 'apply_url', row_value.apply_url,
          'language', row_value.language, 'country', row_value.country);
      content_key := encode(sha256(convert_to(jsonb_build_object('description', row_value.description,
          'preference_metadata', row_value.preference_metadata, 'public_fields', body)::text, 'UTF8')), 'hex');
      INSERT INTO employer_catalogue_contents(id,description,preference_metadata,public_fields)
          VALUES(content_key,row_value.description,row_value.preference_metadata,body) ON CONFLICT (id) DO NOTHING;
      RETURN content_key;
    END $$""",
    r"""CREATE FUNCTION employer_catalogue_source_publication() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE mark bigint;
    BEGIN
      mark := employer_catalogue_watermark();
      IF TG_OP <> 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM employer_catalogue_source_revisions WHERE source_id = OLD.id) THEN
          INSERT INTO employer_catalogue_source_revisions(source_id,valid_from,enabled,platform,region,board_token,employer_key,employer)
            VALUES(OLD.id,0,OLD.enabled,OLD.platform,OLD.region,OLD.board_token,OLD.employer_key,OLD.employer);
        END IF;
        UPDATE employer_catalogue_source_revisions SET valid_to = mark
          WHERE source_id = OLD.id AND valid_to IS NULL AND valid_from < mark;
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      INSERT INTO employer_catalogue_source_revisions(source_id,valid_from,enabled,platform,region,board_token,employer_key,employer)
        VALUES(NEW.id,mark,NEW.enabled,NEW.platform,NEW.region,NEW.board_token,NEW.employer_key,NEW.employer)
        ON CONFLICT(source_id,valid_from) DO UPDATE SET enabled=EXCLUDED.enabled,platform=EXCLUDED.platform,
          region=EXCLUDED.region,
                "board_token"=EXCLUDED."board_token",
                "employer_key"=EXCLUDED."employer_key",
                employer=EXCLUDED.employer;
      RETURN NEW;
    END $$""",
    r"""CREATE FUNCTION employer_catalogue_posting_publication() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE mark bigint; content_key text;
    BEGIN
      mark := employer_catalogue_watermark();
      IF TG_OP <> 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM employer_catalogue_posting_revisions WHERE posting_id = OLD.id) THEN
          content_key := employer_catalogue_content(OLD);
          INSERT INTO employer_catalogue_posting_revisions(posting_id,valid_from,content_id,source_id,external_id,requisition_id,
              title,employer,location,remote,is_open,publication_at,first_seen_at,last_checked_at,content_sha256)
            VALUES(OLD.id,0,content_key,OLD.source_id,OLD.external_id,OLD.requisition_id,OLD.title,OLD.employer,OLD.location,
              OLD.remote,OLD.is_open,OLD.publication_at,OLD.first_seen_at,OLD.last_checked_at,OLD.content_sha256);
        END IF;
        UPDATE employer_catalogue_posting_revisions SET valid_to = mark
          WHERE posting_id = OLD.id AND valid_to IS NULL AND valid_from < mark;
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      content_key := employer_catalogue_content(NEW);
      INSERT INTO employer_catalogue_posting_revisions(posting_id,valid_from,content_id,source_id,external_id,requisition_id,
          title,employer,location,remote,is_open,publication_at,first_seen_at,last_checked_at,content_sha256)
        VALUES(NEW.id,mark,content_key,NEW.source_id,NEW.external_id,NEW.requisition_id,NEW.title,NEW.employer,NEW.location,
          NEW.remote,NEW.is_open,NEW.publication_at,NEW.first_seen_at,NEW.last_checked_at,NEW.content_sha256)
        ON CONFLICT(posting_id,valid_from) DO UPDATE SET content_id=EXCLUDED.content_id,source_id=EXCLUDED.source_id,
          external_id=EXCLUDED.external_id,requisition_id=EXCLUDED.requisition_id,title=EXCLUDED.title,employer=EXCLUDED.employer,
          location=EXCLUDED.location,remote=EXCLUDED.remote,is_open=EXCLUDED.is_open,publication_at=EXCLUDED.publication_at,
          first_seen_at=EXCLUDED.first_seen_at,last_checked_at=EXCLUDED.last_checked_at,content_sha256=EXCLUDED.content_sha256;
      RETURN NEW;
    END $$""",
    r"""CREATE TRIGGER employer_catalogue_source_insert AFTER INSERT OR DELETE ON employer_sources
        FOR EACH ROW EXECUTE FUNCTION employer_catalogue_source_publication()""",
    r"""CREATE TRIGGER employer_catalogue_source_update AFTER UPDATE OF enabled,platform,region,board_token,employer_key,employer
        ON employer_sources FOR EACH ROW EXECUTE FUNCTION employer_catalogue_source_publication()""",
    r"""CREATE TRIGGER employer_catalogue_posting_insert AFTER INSERT OR DELETE ON employer_postings
        FOR EACH ROW EXECUTE FUNCTION employer_catalogue_posting_publication()""",
    r"""CREATE TRIGGER employer_catalogue_posting_update AFTER UPDATE OF source_id,external_id,requisition_id,title,employer,location,
        description,canonical_url,apply_url,language,country,remote,preference_metadata,publication_at,first_seen_at,last_checked_at,
        content_sha256,is_open ON employer_postings FOR EACH ROW EXECUTE FUNCTION employer_catalogue_posting_publication()""",
    r"""CREATE FUNCTION employer_catalogue_public_data_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.description IS DISTINCT FROM OLD.description OR NEW.preference_metadata::text IS DISTINCT FROM OLD.preference_metadata::text
          OR NEW.public_fields::text IS DISTINCT FROM OLD.public_fields::text OR NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='Published public catalogue data is immutable';
      END IF;
      RETURN NEW;
    END $$""",
    r"""CREATE TRIGGER employer_catalogue_content_immutable BEFORE UPDATE OF description,preference_metadata,public_fields,id
        ON employer_catalogue_contents FOR EACH ROW EXECUTE FUNCTION employer_catalogue_public_data_immutable()""",
    r"""CREATE INDEX ix_catalogue_content_fulltext ON employer_catalogue_contents
        USING gin(to_tsvector('simple'::regconfig, coalesce(description,'')))""",
]

POSTGRES_REMOVE_SQL = [
    "DROP TRIGGER employer_catalogue_resume_revision ON resumes",
    "DROP FUNCTION employer_catalogue_resume_revision()",
    "DROP TRIGGER employer_catalogue_projection_immutable ON employer_catalogue_projections",
    "DROP FUNCTION employer_catalogue_projection_immutable()",
    "DROP TRIGGER employer_search_native_snapshot_ready ON employer_searches",
    "DROP TRIGGER employer_catalogue_snapshot_item_immutable ON employer_catalogue_snapshot_items",
    "DROP TRIGGER employer_catalogue_snapshot_immutable ON employer_catalogue_snapshots",
    "DROP TRIGGER employer_catalogue_posting_revision_immutable ON employer_catalogue_posting_revisions",
    "DROP TRIGGER employer_catalogue_source_revision_immutable ON employer_catalogue_source_revisions",
    "DROP FUNCTION employer_search_native_snapshot_ready()",
    "DROP FUNCTION employer_catalogue_snapshot_item_immutable()",
    "DROP FUNCTION employer_catalogue_snapshot_immutable()",
    "DROP FUNCTION employer_catalogue_revision_immutable()",
    "DROP TRIGGER employer_catalogue_content_immutable ON employer_catalogue_contents",
    "DROP TRIGGER employer_catalogue_posting_update ON employer_postings",
    "DROP TRIGGER employer_catalogue_posting_insert ON employer_postings",
    "DROP TRIGGER employer_catalogue_source_update ON employer_sources",
    "DROP TRIGGER employer_catalogue_source_insert ON employer_sources",
    "DROP FUNCTION employer_catalogue_public_data_immutable()",
    "DROP FUNCTION employer_catalogue_posting_publication()",
    "DROP FUNCTION employer_catalogue_source_publication()",
    "DROP FUNCTION employer_catalogue_content(employer_postings)",
    "DROP FUNCTION employer_catalogue_watermark()",
]
