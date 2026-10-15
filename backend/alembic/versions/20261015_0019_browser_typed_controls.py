"""Explicit version-two reviewed phone and choice controls, preserving old reviews."""
import sqlalchemy as sa

from alembic import op

revision = "20261015_0019"
down_revision = "20261015_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_browser_review_index", "browser_assistance_reviews", type_="check")
    op.create_check_constraint("ck_browser_review_index", "browser_assistance_reviews", "next_index BETWEEN 0 AND 12")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM browser_assistance_reviews WHERE next_index > 7 OR payload ->> 'review_protocol_version' = '2' LIMIT 1")).first():
        raise RuntimeError("Typed-control review history must be retained; downgrade refused.")
    op.drop_constraint("ck_browser_review_index", "browser_assistance_reviews", type_="check")
    op.create_check_constraint("ck_browser_review_index", "browser_assistance_reviews", "next_index BETWEEN 0 AND 7")
