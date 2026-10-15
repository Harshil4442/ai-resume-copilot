"""Private direct-upload admission and detached cleanup; no legacy source rewrite."""
import sqlalchemy as sa

from alembic import op

revision = "20261010_0014"
down_revision = "20261009_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("resume_uploads",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("source_format", sa.String(8), nullable=False),
        sa.Column("media_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("quarantine_bucket", sa.String(63), nullable=False),
        sa.Column("quarantine_name", sa.String(180), nullable=False),
        sa.Column("clean_bucket", sa.String(63), nullable=False),
        sa.Column("clean_name", sa.String(180), nullable=False),
        sa.Column("quarantine_generation", sa.String(20), nullable=True),
        sa.Column("clean_generation", sa.String(20), nullable=True),
        sa.Column("scan_receipt", sa.JSON(), nullable=True),
        sa.Column("enrich_skills", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("upload_grant_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("result_resume_id", sa.Integer(), sa.ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "idempotency_sha256", name="uq_resume_upload_owner_key"),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 5242880", name="ck_resume_upload_size"),
        sa.CheckConstraint("source_format IN ('pdf','docx')", name="ck_resume_upload_format"),
        sa.CheckConstraint("state IN ('awaiting_upload','queued','inspecting','released','rejected','failed','cancelled','expired')", name="ck_resume_upload_state"),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 3", name="ck_resume_upload_attempts"),
    )
    op.create_index("ix_resume_uploads_user_id", "resume_uploads", ["user_id"])
    op.create_index("ix_resume_upload_due", "resume_uploads", ["state", "next_attempt_at"])
    op.create_table("resume_upload_cleanups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("upload_id", sa.String(64), nullable=False, unique=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantine_closed_generation", sa.String(20), nullable=True),
        sa.Column("clean_closed_generation", sa.String(20), nullable=True),
        sa.Column("payload_retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        schema = connection.scalar(sa.text("SELECT current_schema()"))
        quote = connection.dialect.identifier_preparer.quote
        namespace = quote(schema)
        # FK owner/resume unlinking atomically revokes attempts and persists
        # deletion work, even if account deletion never calls this new domain.
        connection.execute(sa.text(f'''CREATE FUNCTION {namespace}.resume_upload_unlink() RETURNS trigger
        LANGUAGE plpgsql SET search_path = {namespace}, pg_catalog AS $$
        BEGIN
          IF (OLD.user_id IS NOT NULL AND NEW.user_id IS NULL)
             OR (OLD.result_resume_id IS NOT NULL AND NEW.result_resume_id IS NULL) THEN
            NEW.state := 'cancelled'; NEW.lease_token := NULL; NEW.lease_until := NULL;
            NEW.original_filename := NULL; NEW.enrich_skills := false; NEW.scan_receipt := NULL;
            INSERT INTO resume_upload_cleanups(id, upload_id, next_attempt_at, attempt_count, created_at)
            VALUES ('ruc_' || substr(NEW.id,5), NEW.id, clock_timestamp(),0,clock_timestamp())
            ON CONFLICT (upload_id) DO UPDATE SET next_attempt_at=clock_timestamp(), payload_retired_at=NULL;
          END IF;
          RETURN NEW;
        END $$'''))
        connection.execute(sa.text(f'''CREATE TRIGGER resume_upload_owner_unlink
        BEFORE UPDATE OF user_id,result_resume_id ON {namespace}.resume_uploads
        FOR EACH ROW EXECUTE FUNCTION {namespace}.resume_upload_unlink()'''))


def downgrade():
    connection = op.get_bind()
    for table in ("resume_uploads", "resume_upload_cleanups"):
        if connection.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("Cannot downgrade retained resume-upload cleanup obligations")
    if connection.dialect.name == "postgresql":
        schema = connection.scalar(sa.text("SELECT current_schema()"))
        namespace = connection.dialect.identifier_preparer.quote(schema)
        connection.execute(sa.text(f"DROP TRIGGER resume_upload_owner_unlink ON {namespace}.resume_uploads"))
        connection.execute(sa.text(f"DROP FUNCTION {namespace}.resume_upload_unlink()"))
    op.drop_table("resume_upload_cleanups")
    op.drop_index("ix_resume_upload_due", table_name="resume_uploads")
    op.drop_index("ix_resume_uploads_user_id", table_name="resume_uploads")
    op.drop_table("resume_uploads")
