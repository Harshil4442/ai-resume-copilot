from sqlalchemy import create_engine, inspect, text

from backend.app.database import Base
from backend.app.migrations import run_migrations


def test_fresh_payment_schema_migration_is_safe(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(engine)
    run_migrations(engine)
    run_migrations(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("payment_orders")}
    assert {
        "public_id",
        "provider_mode",
        "provider_key_id",
        "gross_amount_minor",
        "entitlement_kind",
        "entitlement_quantity",
        "billing_country_confirmed_at",
        "active_attempt_key",
    } <= columns
    engine.dispose()


def test_legacy_payment_order_is_backfilled_without_deletion(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY, email VARCHAR(255), password_hash VARCHAR(255))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE payment_orders ("
                "id INTEGER PRIMARY KEY, user_id INTEGER, provider VARCHAR(20), "
                "provider_order_id VARCHAR(120), order_type VARCHAR(20), credits INTEGER, "
                "amount FLOAT, currency VARCHAR(8), status VARCHAR(20), provisioned INTEGER, "
                "created_at TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO payment_orders "
                "(id, provider, provider_order_id, order_type, amount, currency, status, created_at) "
                "VALUES (1, 'cashfree', 'legacy_order', 'subscription', 999.0, 'INR', "
                "'paid', CURRENT_TIMESTAMP)"
            )
        )

    # Matches production startup ordering: create missing tables, then apply
    # additive changes to the existing legacy table.
    Base.metadata.create_all(engine)
    run_migrations(engine)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT public_id, provider_mode, sku, catalog_version, "
                "gross_amount_minor FROM payment_orders WHERE id=1"
            )
        ).mappings().one()
    assert row["public_id"].startswith("legacy_")
    assert row["provider_mode"] == "legacy"
    assert row["sku"] == "premium_30d"
    assert row["catalog_version"] == "legacy"
    assert row["gross_amount_minor"] == 99_900
    engine.dispose()
