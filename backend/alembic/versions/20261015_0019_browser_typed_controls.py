"""Undeployed reviewed v2 controls, v3 attachment and v4 submit history."""
import sqlalchemy as sa

from alembic import op

revision = "20261015_0019"
down_revision = "20261015_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("candidate_google_enrollment_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nonce_sha256", sa.String(64), nullable=False),
        sa.Column("principal_sha256", sa.String(64), nullable=False),
        sa.Column("email_sha256", sa.String(64), nullable=False),
        sa.Column("credential_sha256", sa.String(64), nullable=False),
        sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False))
    op.create_index("ix_candidate_google_enrollment_challenges_user_id", "candidate_google_enrollment_challenges", ["user_id"])
    op.create_table("candidate_google_identities",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("principal_sha256", sa.String(64), unique=True, nullable=False),
        sa.Column("enrollment_proof_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False))
    op.create_table("candidate_identity_history", sa.Column("registration_id", sa.String(36), primary_key=True))
    op.add_column("browser_assistance_reviews", sa.Column("review_protocol_version", sa.Integer(), nullable=False, server_default="1"))
    op.execute(sa.text("UPDATE browser_assistance_reviews SET review_protocol_version=CAST(payload ->> 'review_protocol_version' AS INTEGER) WHERE payload ->> 'review_protocol_version' IN ('2','3','4')"))
    # Native PostgreSQL ALTER; SQLite's supported development/test path needs
    # Alembic's copy-and-move batch operation to replace a check constraint.
    with op.batch_alter_table("browser_assistance_reviews") as batch:
        batch.drop_constraint("ck_browser_review_index", type_="check")
        batch.create_check_constraint("ck_browser_review_index", "next_index BETWEEN 0 AND 64")
        batch.create_check_constraint("ck_browser_review_protocol", "review_protocol_version BETWEEN 1 AND 4")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM candidate_identity_history LIMIT 1")).first():
        raise RuntimeError("Protected Google identity history must be retained; downgrade refused.")
    if op.get_bind().execute(sa.text("SELECT 1 FROM browser_assistance_reviews WHERE next_index > 7 OR review_protocol_version > 1 OR payload ->> 'review_protocol_version' IN ('2','3','4') LIMIT 1")).first():
        raise RuntimeError("Typed-control review history must be retained; downgrade refused.")
    with op.batch_alter_table("browser_assistance_reviews") as batch:
        batch.drop_constraint("ck_browser_review_protocol", type_="check")
        batch.drop_constraint("ck_browser_review_index", type_="check")
        batch.create_check_constraint("ck_browser_review_index", "next_index BETWEEN 0 AND 7")
        batch.drop_column("review_protocol_version")
    op.drop_table("candidate_identity_history")
    op.drop_table("candidate_google_identities")
    op.drop_index("ix_candidate_google_enrollment_challenges_user_id", table_name="candidate_google_enrollment_challenges")
    op.drop_table("candidate_google_enrollment_challenges")
