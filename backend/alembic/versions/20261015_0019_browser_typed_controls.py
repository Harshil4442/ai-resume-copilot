"""Explicit version-two reviewed phone and choice controls, preserving old reviews."""
import sqlalchemy as sa

from alembic import op

revision = "20261015_0019"
down_revision = "20261015_0018"
branch_labels = None
depends_on = None


def upgrade():
    # Native PostgreSQL ALTER; SQLite's supported development/test path needs
    # Alembic's copy-and-move batch operation to replace a check constraint.
    with op.batch_alter_table("browser_assistance_reviews") as batch:
        batch.drop_constraint("ck_browser_review_index", type_="check")
        batch.create_check_constraint("ck_browser_review_index", "next_index BETWEEN 0 AND 12")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM browser_assistance_reviews WHERE next_index > 7 OR payload ->> 'review_protocol_version' = '2' LIMIT 1")).first():
        raise RuntimeError("Typed-control review history must be retained; downgrade refused.")
    with op.batch_alter_table("browser_assistance_reviews") as batch:
        batch.drop_constraint("ck_browser_review_index", type_="check")
        batch.create_check_constraint("ck_browser_review_index", "next_index BETWEEN 0 AND 7")
