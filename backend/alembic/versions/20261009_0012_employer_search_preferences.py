"""Optional structural employer preferences; old postings stay unknown.

Revision ID: 20261009_0012
Revises: 20261009_0011
"""
from alembic import op
import sqlalchemy as sa

revision = "20261009_0012"
down_revision = "20261009_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employer_postings", sa.Column("preference_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("employer_postings", "preference_metadata")
