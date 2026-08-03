"""Add lifecycle delivery, support audit, and analysis retention state.

Revision ID: 20260803_0003
Revises: 20260803_0002
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0003"
down_revision: Union[str, Sequence[str], None] = "20260803_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("input_purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("result_purged_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("notification_type", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False, server_default="email"),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_notification_outbox_idempotency"),
    )
    op.create_index("ix_notification_outbox_user_id", "notification_outbox", ["user_id"])
    op.create_index(
        "ix_notification_outbox_status_available",
        "notification_outbox",
        ["status", "available_at"],
    )

    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_email", sa.String(320), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=False),
        sa.Column("after_state", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_audit_events_actor_user_id", "admin_audit_events", ["actor_user_id"])
    op.create_index("ix_admin_audit_created", "admin_audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("admin_audit_events")
    op.drop_table("notification_outbox")
    op.drop_column("analysis_runs", "result_purged_at")
    op.drop_column("analysis_runs", "input_purged_at")
