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
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM resume_uploads WHERE source_format IN ('tex','texzip') LIMIT 1"
    )).first():
        raise RuntimeError("Native resume upload and cleanup history must be retained; downgrade refused.")
    with op.batch_alter_table("resume_uploads") as batch:
        batch.drop_constraint("ck_resume_upload_format", type_="check")
        batch.create_check_constraint("ck_resume_upload_format", "source_format IN ('pdf','docx')")
