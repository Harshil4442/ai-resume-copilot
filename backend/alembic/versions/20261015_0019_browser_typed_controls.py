"""Undeployed reviewed v2 controls, v3 attachment and v4 submit history."""
import sqlalchemy as sa

from alembic import op

revision = "20261015_0019"
down_revision = "20261015_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("browser_assistance_reviews", sa.Column("review_protocol_version", sa.Integer(), nullable=False, server_default="1"))
    op.execute(sa.text("UPDATE browser_assistance_reviews SET review_protocol_version=CAST(payload ->> 'review_protocol_version' AS INTEGER) WHERE payload ->> 'review_protocol_version' IN ('2','3','4')"))
    # Native PostgreSQL ALTER; SQLite's supported development/test path needs
    # Alembic's copy-and-move batch operation to replace a check constraint.
    with op.batch_alter_table("browser_assistance_reviews") as batch:
        batch.drop_constraint("ck_browser_review_index", type_="check")
        batch.create_check_constraint("ck_browser_review_index", "next_index BETWEEN 0 AND 64")
        batch.create_check_constraint("ck_browser_review_protocol", "review_protocol_version BETWEEN 1 AND 4")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM browser_assistance_reviews WHERE next_index > 7 OR review_protocol_version > 1 OR payload ->> 'review_protocol_version' IN ('2','3','4') LIMIT 1")).first():
        raise RuntimeError("Typed-control review history must be retained; downgrade refused.")
    with op.batch_alter_table("browser_assistance_reviews") as batch:
        batch.drop_constraint("ck_browser_review_protocol", type_="check")
        batch.drop_constraint("ck_browser_review_index", type_="check")
        batch.create_check_constraint("ck_browser_review_index", "next_index BETWEEN 0 AND 7")
        batch.drop_column("review_protocol_version")
