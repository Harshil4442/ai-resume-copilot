import logging
import uuid
from contextlib import contextmanager
from sqlalchemy import inspect, text
from .database import engine

log = logging.getLogger("ai_resume_copilot.migrations")

@contextmanager
def _migration_lock(bind):
    """Serialize additive startup migrations across PostgreSQL instances."""
    connection = bind.connect()
    locked = False
    try:
        if bind.dialect.name == "postgresql":
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_key)"),
                {"lock_key": 721_994_372},
            )
            locked = True
        yield
    finally:
        if locked:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": 721_994_372},
            )
        connection.close()


def run_migrations(bind=None):
    bind = bind or engine
    with _migration_lock(bind):
        _run_migrations_unlocked(bind)


def _run_migrations_unlocked(bind):
    """
    Safely runs database migrations by checking table columns and adding
    missing ones dynamically. Works on both SQLite and PostgreSQL.
    """
    log.info("Running database schema migrations...")
    inspector = inspect(bind)
    
    # Check columns for the 'users' table
    if not inspector.has_table("users"):
        log.info("Table 'users' does not exist yet. It will be created by metadata.")
        return
        
    columns = {col["name"] for col in inspector.get_columns("users")}
    
    with bind.begin() as connection:
        # Add 'tier'
        if "tier" not in columns:
            log.info("Adding column 'tier' to 'users' table")
            # SQLite and Postgres support basic ALTER TABLE ADD COLUMN
            connection.execute(text("ALTER TABLE users ADD COLUMN tier VARCHAR(50) DEFAULT 'free'"))
            
        # Add 'ai_credits'
        if "ai_credits" not in columns:
            log.info("Adding column 'ai_credits' to 'users' table")
            connection.execute(text("ALTER TABLE users ADD COLUMN ai_credits INTEGER DEFAULT 5 NOT NULL"))
            
        # Add 'premium_until' (expiry for time-limited premium grants)
        if "premium_until" not in columns:
            log.info("Adding column 'premium_until' to 'users' table")
            connection.execute(text("ALTER TABLE users ADD COLUMN premium_until TIMESTAMP NULL"))
        if "terms_accepted_at" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN terms_accepted_at TIMESTAMP NULL"))
        if "terms_version" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN terms_version VARCHAR(32) NULL"))
        if "privacy_version" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN privacy_version VARCHAR(32) NULL"))
        if "age_confirmed_at" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN age_confirmed_at TIMESTAMP NULL"))

    # ``create_all`` creates the new payment-domain tables on fresh and
    # existing deployments. The additive migration below upgrades the legacy
    # Cashfree/PayPal payment_orders table without deleting accounting rows.
    inspector = inspect(bind)
    if inspector.has_table("payment_orders"):
        payment_columns = {col["name"] for col in inspector.get_columns("payment_orders")}
        additions = {
            "public_id": "VARCHAR(64) NULL",
            "provider_mode": "VARCHAR(16) NULL",
            "provider_key_id": "VARCHAR(120) NULL",
            "provider_customer_id": "VARCHAR(120) NULL",
            "provider_subscription_id": "VARCHAR(120) NULL",
            "sku": "VARCHAR(64) NULL",
            "catalog_version": "VARCHAR(64) NULL",
            "billing_type": "VARCHAR(32) NULL",
            "entitlement_kind": "VARCHAR(40) NULL",
            "entitlement_quantity": "INTEGER NULL",
            "billing_country": "VARCHAR(2) NULL",
            "billing_country_confirmed_at": "TIMESTAMP NULL",
            "gross_amount_minor": "INTEGER NULL",
            "customer_tax_amount_minor": "INTEGER NULL",
            "provider_fee_amount_minor": "INTEGER NULL",
            "provider_fee_tax_minor": "INTEGER NULL",
            "estimated_net_amount_minor": "INTEGER NULL",
            "refunded_amount_minor": "INTEGER DEFAULT 0",
            "active_attempt_key": "VARCHAR(160) NULL",
            "client_confirmed_at": "TIMESTAMP NULL",
            "paid_at": "TIMESTAMP NULL",
            "refunded_at": "TIMESTAMP NULL",
            "customer_deleted_at": "TIMESTAMP NULL",
            "updated_at": "TIMESTAMP NULL",
        }
        with bind.begin() as connection:
            for name, ddl in additions.items():
                if name not in payment_columns:
                    log.info("Adding column '%s' to 'payment_orders' table", name)
                    connection.execute(
                        text(f"ALTER TABLE payment_orders ADD COLUMN {name} {ddl}")
                    )

            # Backfill legacy rows with safe internal identifiers and normalized
            # integer amounts. They remain historical and can never be offered
            # through the new catalog.
            amount_select = "amount" if "amount" in payment_columns else "NULL AS amount"
            order_type_select = (
                "order_type" if "order_type" in payment_columns else "NULL AS order_type"
            )
            legacy_rows = connection.execute(
                text(
                    "SELECT id, public_id, "
                    f"{amount_select}, {order_type_select}, gross_amount_minor "
                    "FROM payment_orders"
                )
            ).mappings()
            for row in legacy_rows:
                public_id = row["public_id"] or f"legacy_{uuid.uuid4().hex}"
                amount_minor = row["gross_amount_minor"]
                if amount_minor is None:
                    amount_minor = int(round(float(row["amount"] or 0) * 100))
                sku = (
                    "premium_30d"
                    if row["order_type"] == "subscription"
                    else "legacy_unavailable"
                )
                connection.execute(
                    text(
                        "UPDATE payment_orders SET public_id=:public_id, "
                        "sku=COALESCE(sku, :sku), "
                        "provider_mode=COALESCE(provider_mode, 'legacy'), "
                        "catalog_version=COALESCE(catalog_version, 'legacy'), "
                        "billing_type=COALESCE(billing_type, 'one_time'), "
                        "billing_country=COALESCE(billing_country, 'IN'), "
                        "gross_amount_minor=:amount_minor, "
                        "refunded_amount_minor=COALESCE(refunded_amount_minor, 0), "
                        "updated_at=COALESCE(updated_at, created_at) "
                        "WHERE id=:id"
                    ),
                    {
                        "id": row["id"],
                        "public_id": public_id,
                        "sku": sku,
                        "amount_minor": amount_minor,
                    },
                )

            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_orders_public_id "
                    "ON payment_orders (public_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_orders_active_attempt "
                    "ON payment_orders (active_attempt_key)"
                )
            )

    inspector = inspect(bind)
    if inspector.has_table("payment_transactions"):
        transaction_columns = {
            col["name"] for col in inspector.get_columns("payment_transactions")
        }
        if "instrument_international" not in transaction_columns:
            with bind.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE payment_transactions "
                        "ADD COLUMN instrument_international BOOLEAN NULL"
                    )
                )

    inspector = inspect(bind)
    if inspector.has_table("payment_events"):
        event_columns = {col["name"] for col in inspector.get_columns("payment_events")}
        if "provider_created_at" not in event_columns:
            with bind.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE payment_events "
                        "ADD COLUMN provider_created_at TIMESTAMP NULL"
                    )
                )

    log.info("Database migrations completed successfully.")
