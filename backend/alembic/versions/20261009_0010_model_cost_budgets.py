"""Quoted estimated-spend budgets and durable per-attempt monetary holds.

Existing unquoted history is retained but cannot authorize additional AI calls.
Pause/drain old AI writers before this migration; old binaries do not enforce it.
"""
import sqlalchemy as sa

from alembic import op

revision = "20261009_0010"
down_revision = "20261008_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("model_cost_quote", sa.JSON(), nullable=True))
    op.add_column("analysis_runs", sa.Column("model_cost_ceiling_micros", sa.BigInteger(), nullable=True))
    op.add_column("analysis_runs", sa.Column("model_cost_reserved_micros", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("analysis_runs", sa.Column("model_cost_settled_micros", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("analysis_runs", sa.Column("model_cost_state", sa.String(32), nullable=False, server_default="unquoted"))
    for column in (
        sa.Column("pricing_quote", sa.JSON(), nullable=True),
        sa.Column("reserved_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("settled_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("cost_state", sa.String(32), nullable=False, server_default="unavailable"),
        sa.Column("token_estimate_provenance", sa.String(64), nullable=True),
        sa.Column("usage_provenance", sa.String(64), nullable=True),
        sa.Column("output_token_limit", sa.Integer(), nullable=True),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
    ):
        op.add_column("model_call_events", column)
    with op.batch_alter_table("model_call_events") as batch:
        batch.alter_column("estimated_cost_micros", existing_type=sa.Integer(), type_=sa.BigInteger(), nullable=True)
    # Old telemetry (including zero values) is historical, never authority for
    # a price quote. No attempt count or historical cost is reset/backfilled.


def downgrade() -> None:
    connection = op.get_bind()
    incompatible = connection.execute(sa.text(
        "SELECT 1 FROM model_call_events WHERE estimated_cost_micros > 2147483647 "
        "OR estimated_cost_micros < -2147483648 LIMIT 1"
    )).first()
    if incompatible:
        raise RuntimeError(
            "Cannot downgrade model-cost history to the legacy Integer range without loss. "
            "Keep revision 20261009_0010 and AI writers paused; do not truncate accounting history."
        )
    # Do not pretend unavailable new telemetry was a free historical request.
    # The old schema requires an integer, so compatibility uses zero only on
    # downgrade; rollout docs forbid resuming old AI writers as a safe rollback.
    op.execute("UPDATE model_call_events SET estimated_cost_micros = 0 WHERE estimated_cost_micros IS NULL")
    with op.batch_alter_table("model_call_events") as batch:
        batch.alter_column("estimated_cost_micros", existing_type=sa.BigInteger(), type_=sa.Integer(), nullable=False)
        for name in ("settled_at", "output_token_limit", "usage_provenance", "token_estimate_provenance", "cost_state", "settled_cost_micros", "reserved_cost_micros", "pricing_quote"):
            batch.drop_column(name)
    for name in ("model_cost_state", "model_cost_settled_micros", "model_cost_reserved_micros", "model_cost_ceiling_micros", "model_cost_quote"):
        op.drop_column("analysis_runs", name)
