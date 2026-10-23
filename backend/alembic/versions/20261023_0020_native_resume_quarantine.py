"""Admit native sources to the existing quarantine and retained cleanup chain."""

import sqlalchemy as sa

from alembic import op

revision = "20261023_0020"
down_revision = "20261015_0019"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("resume_uploads") as batch:
        batch.drop_constraint("ck_resume_upload_format", type_="check")
        batch.create_check_constraint(
            "ck_resume_upload_format", "source_format IN ('pdf','docx','tex','texzip')"
        )


def downgrade():
    # A source row also retains cleanup obligations after candidate deletion.
    # Never erase those rows or relabel their original format to force rollback.
    # A retained parent can prove PDF/DOCX custody. Detached cleanup rows have
    # no format provenance and cannot be silently discarded for a downgrade.
    # Application/flag rollback can retain schema20 without a data downgrade.
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE resume_uploads,resume_upload_cleanups IN SHARE ROW EXCLUSIVE MODE")
        )
    if connection.execute(
        sa.text(
            "SELECT 1 FROM resume_upload_cleanups c LEFT JOIN resume_uploads u ON u.id=c.upload_id "
            "WHERE u.source_format IS NULL OR u.source_format NOT IN ('pdf','docx') LIMIT 1"
        )
    ).first():
        raise RuntimeError(
            "Resume upload cleanup history has native or unknown source-format provenance; downgrade refused."
        )
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM resume_uploads WHERE source_format IN ('tex','texzip') LIMIT 1")
        )
        .first()
    ):
        raise RuntimeError(
            "Native resume upload and cleanup history must be retained; downgrade refused."
        )
    with op.batch_alter_table("resume_uploads") as batch:
        batch.drop_constraint("ck_resume_upload_format", type_="check")
        batch.create_check_constraint("ck_resume_upload_format", "source_format IN ('pdf','docx')")
