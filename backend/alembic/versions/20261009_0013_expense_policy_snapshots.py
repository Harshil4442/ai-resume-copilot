"""Freeze reviewed expenses on prospective orders and service reservations."""
import sqlalchemy as sa

from alembic import op

revision = "20261009_0013"
down_revision = "20261009_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment_orders", sa.Column("cost_policy_snapshot", sa.JSON(), nullable=True))
    op.add_column("service_credit_reservations", sa.Column("cost_policy_snapshot", sa.JSON(), nullable=True))

    op.create_index("ix_payment_orders_paid_at", "payment_orders", ["paid_at"])
    op.create_index("ix_payment_refunds_processed_at", "payment_refunds", ["processed_at"])
    op.create_index("ix_model_liability_created", "model_cost_liabilities", ["created_at"])
    op.create_index("ix_model_liability_state_created", "model_cost_liabilities", ["cost_state", "created_at"])
    op.create_index("ix_service_reservations_state_created", "service_credit_reservations", ["state", "created_at"])


def downgrade():
    connection = op.get_bind()
    for table in ("payment_orders", "service_credit_reservations"):
        count = connection.scalar(sa.text(f"SELECT count(*) FROM {table} WHERE cost_policy_snapshot IS NOT NULL"))
        if count:
            raise RuntimeError("Cannot downgrade reviewed expense snapshots without loss of accepted financial history")
    for name,table in (("ix_payment_orders_paid_at","payment_orders"),("ix_payment_refunds_processed_at","payment_refunds"),
                       ("ix_model_liability_created","model_cost_liabilities"),("ix_model_liability_state_created","model_cost_liabilities"),
                       ("ix_service_reservations_state_created","service_credit_reservations")):
        op.drop_index(name,table_name=table)
    op.drop_column("service_credit_reservations", "cost_policy_snapshot")
    op.drop_column("payment_orders", "cost_policy_snapshot")
