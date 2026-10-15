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
    op.add_column("analysis_runs", sa.Column("model_cost_group_id", sa.String(64), nullable=True))
    op.create_table(
        "model_cost_liabilities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("financial_group_id", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("endpoint_key", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("pricing_quote", sa.JSON(), nullable=False),
        sa.Column("input_token_estimate", sa.Integer(), nullable=False),
        sa.Column("reserved_cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("settled_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("usage_provenance", sa.String(64), nullable=True),
        sa.Column("cost_state", sa.String(32), nullable=False, server_default="reserved"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("financial_group_id", "attempt_number", name="uq_model_liability_attempt"),
    )
    op.create_index("ix_model_cost_liabilities_financial_group_id", "model_cost_liabilities", ["financial_group_id"])
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
        batch.add_column(sa.Column("liability_id", sa.String(64), nullable=True))
        batch.create_foreign_key("fk_model_call_liability", "model_cost_liabilities", ["liability_id"], ["id"])
        batch.create_index("ix_model_call_events_liability_id", ["liability_id"])
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
    if connection.execute(sa.text("SELECT 1 FROM model_cost_liabilities LIMIT 1")).first():
        raise RuntimeError(
            "Cannot downgrade retained model-cost liabilities without loss. "
            "Keep revision 20261009_0010 and AI writers paused; do not delete financial evidence."
        )
    # Do not pretend unavailable new telemetry was a free historical request.
    # The old schema requires an integer, so compatibility uses zero only on
    # downgrade; rollout docs forbid resuming old AI writers as a safe rollback.
    op.execute("UPDATE model_call_events SET estimated_cost_micros = 0 WHERE estimated_cost_micros IS NULL")
    with op.batch_alter_table("model_call_events") as batch:
        batch.drop_constraint("fk_model_call_liability", type_="foreignkey")
        batch.drop_index("ix_model_call_events_liability_id")
        batch.drop_column("liability_id")
        batch.alter_column("estimated_cost_micros", existing_type=sa.BigInteger(), type_=sa.Integer(), nullable=False)
        for name in ("settled_at", "output_token_limit", "usage_provenance", "token_estimate_provenance", "cost_state", "settled_cost_micros", "reserved_cost_micros", "pricing_quote"):
            batch.drop_column(name)
    op.drop_index("ix_model_cost_liabilities_financial_group_id", table_name="model_cost_liabilities")
    op.drop_table("model_cost_liabilities")
    for name in ("model_cost_group_id", "model_cost_state", "model_cost_settled_micros", "model_cost_reserved_micros", "model_cost_ceiling_micros", "model_cost_quote"):
        op.drop_column("analysis_runs", name)
