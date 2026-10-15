"""Durable upload guards and indefinitely recoverable private-object purges."""
import sqlalchemy as sa

from alembic import op

revision = "20261008_0008"
down_revision = "20261008_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employer_artifact_deletions", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("employer_artifact_deletions", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("employer_artifact_deletions", sa.Column("last_error_code", sa.String(80)))
    op.add_column("employer_artifact_deletions", sa.Column("absence_confirmed_at", sa.DateTime(timezone=True)))
    op.create_table(
        "employer_artifact_uploads",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("gcs_object", sa.Text(), nullable=False),
        sa.Column("gcs_generation", sa.String(80)),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_employer_artifact_uploads_user_id", "employer_artifact_uploads", ["user_id"])
    op.create_index("ix_employer_artifact_uploads_lease_until", "employer_artifact_uploads", ["lease_until"])


def downgrade() -> None:
    op.drop_table("employer_artifact_uploads")
    op.drop_column("employer_artifact_deletions", "absence_confirmed_at")
    op.drop_column("employer_artifact_deletions", "last_error_code")
    op.drop_column("employer_artifact_deletions", "attempt_count")
    op.drop_column("employer_artifact_deletions", "next_attempt_at")
