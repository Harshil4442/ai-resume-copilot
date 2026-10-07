"""Retain original resume files for source-preserving exports.

Revision ID: 20261007_0004
Revises: 20260803_0003
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261007_0004"
down_revision: str | Sequence[str] | None = "20260803_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("resumes", sa.Column("source_document", sa.LargeBinary(), nullable=True))
    op.add_column("resumes", sa.Column("source_format", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("resumes", "source_format")
    op.drop_column("resumes", "source_document")
