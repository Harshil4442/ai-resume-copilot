"""Durable transactional work dispatch.

Revision ID: 20261008_0007
Revises: 20261008_0006
"""
import sqlalchemy as sa
from alembic import op

revision = "20261008_0007"
down_revision = "20261008_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dispatch_outbox",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("task_name", sa.String(512)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_dispatch_due", "dispatch_outbox", ["status", "available_at"])
    op.create_index("ix_dispatch_outbox_aggregate_id", "dispatch_outbox", ["aggregate_id"])


def downgrade() -> None:
    op.drop_table("dispatch_outbox")
