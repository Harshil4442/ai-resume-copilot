"""Persist total generation attempts and truthful token provenance.

Revision ID: 20261008_0005
Revises: 20261007_0004
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0005"
down_revision = "20261007_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_runs", sa.Column("generation_attempt_limit", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("analysis_runs", sa.Column("generation_attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("model_call_events", sa.Column("tokens_estimated", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("model_call_events", sa.Column("attempt_number", sa.Integer(), nullable=True))
    op.create_table(
        "analysis_request_keys",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("idempotency_key", sa.String(160), primary_key=True),
        sa.Column("analysis_run_id", sa.String(64), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_analysis_request_keys_analysis_run_id", "analysis_request_keys", ["analysis_run_id"])


def downgrade():
    op.drop_index("ix_analysis_request_keys_analysis_run_id", table_name="analysis_request_keys")
    op.drop_table("analysis_request_keys")
    op.drop_column("model_call_events", "attempt_number")
    op.drop_column("model_call_events", "tokens_estimated")
    op.drop_column("analysis_runs", "generation_attempt_count")
    op.drop_column("analysis_runs", "generation_attempt_limit")
