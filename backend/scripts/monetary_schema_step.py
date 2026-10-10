"""One bounded, forward-only monetary migration; never an old-writer fence."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from alembic import command

CHAIN = ("20261008_0009", "20261009_0010", "20261009_0011", "20261009_0012", "20261009_0013")
LOCK_ID = 721_994_372
MAX_INDEX_BYTES = 256 * 1024 * 1024
INDEX_TABLES = (
    "payment_orders", "payment_refunds", "model_cost_liabilities", "service_credit_reservations",
)


class StepDenied(RuntimeError):
    pass


def upgrade_step(
    engine: Engine, *, expected: str, target: str, identifier_hash: str,
    database_oid: int, namespace: str, migration_role: str,
) -> None:
    if (
        engine.dialect.name != "postgresql" or expected not in CHAIN[:-1]
        or target != CHAIN[CHAIN.index(expected) + 1]
        or not re.fullmatch(r"[a-f0-9]{64}", identifier_hash)
        or type(database_oid) is not int or database_oid <= 0
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", namespace)
        or not migration_role or len(migration_role) > 63 or "\x00" in migration_role
    ):
        raise StepDenied("migration_step_or_identity_invalid")
    locked = False
    with engine.connect() as connection:
        try:
            connection.exec_driver_sql("SET statement_timeout = '300000ms'")
            connection.exec_driver_sql("SET lock_timeout = '5000ms'")
            connection.exec_driver_sql("SET idle_in_transaction_session_timeout = '10000ms'")
            if connection.execute(text("SELECT current_user")).scalar_one() != migration_role:
                raise StepDenied("migration_role_mismatch")
            identifier = connection.execute(text("SELECT system_identifier::text FROM pg_control_system()")).scalar_one()
            oid = connection.execute(text("SELECT oid FROM pg_database WHERE datname = current_database()")).scalar_one()
            if hashlib.sha256(str(identifier).encode()).hexdigest() != identifier_hash or oid != database_oid:
                raise StepDenied("migration_database_identity_mismatch")
            locked = connection.execute(text("SELECT pg_try_advisory_lock(:lock)"), {"lock": LOCK_ID}).scalar_one()
            if not locked:
                raise StepDenied("migration_already_running")
            quoted = connection.dialect.identifier_preparer.quote(namespace)
            connection.exec_driver_sql(f"SET search_path TO {quoted}")
            versions = list(connection.execute(text(f"SELECT version_num FROM {quoted}.alembic_version LIMIT 3")).scalars())
            if versions != [expected]:
                raise StepDenied("migration_observed_schema_mismatch")
            if target == CHAIN[-1]:
                # Ordinary CREATE INDEX blocks table writes. Only a bounded, drained
                # maintenance window admits this reviewed migration, never a claim
                # that concurrent traffic or a caller-provided table size is safe.
                size = connection.execute(text(
                    "SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=:namespace AND c.relkind='r' AND c.relname = ANY(:tables)"
                ), {"namespace": namespace, "tables": list(INDEX_TABLES)}).scalar_one()
                if size > MAX_INDEX_BYTES:
                    raise StepDenied("schema13_index_window_requires_separate_large_table_plan")
                if connection.execute(text("SELECT count(*) FROM pg_prepared_xacts WHERE database = current_database()")).scalar_one():
                    raise StepDenied("schema13_prepared_transactions_present")
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, target)
            after = list(connection.execute(text(f"SELECT version_num FROM {quoted}.alembic_version LIMIT 3")).scalars())
            if after != [target]:
                raise StepDenied("migration_target_not_observed")
            connection.commit()
        finally:
            # A timeout/error leaves no partial schema transaction. Do not attempt
            # downgrade, replay an unknown execution or remove monetary history.
            connection.rollback()
            if locked:
                connection.execute(text("SELECT pg_advisory_unlock(:lock)"), {"lock": LOCK_ID})
                connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, choices=CHAIN[:-1])
    parser.add_argument("--target", required=True, choices=CHAIN[1:])
    args = parser.parse_args()
    engine: Engine | None = None
    try:
        engine = create_engine(os.environ["DATABASE_URL"], poolclass=NullPool, echo=False,
                               hide_parameters=True, connect_args={"connect_timeout": 5})
        upgrade_step(engine, expected=args.expected, target=args.target,
                     identifier_hash=os.environ["HIREWIZ_CUTOVER_CLUSTER_SHA256"],
                     database_oid=int(os.environ["HIREWIZ_CUTOVER_DATABASE_OID"]),
                     namespace=os.environ["HIREWIZ_CUTOVER_NAMESPACE"],
                     migration_role=os.environ["HIREWIZ_CUTOVER_MIGRATION_ROLE"])
    except StepDenied as error:
        print(f"Monetary migration refused: {error}", file=sys.stderr)
        return 65
    except Exception:
        print("Monetary migration refused: observation_or_execution_unavailable; reconcile status before retry.", file=sys.stderr)
        return 65
    finally:
        if engine is not None:
            engine.dispose()
    print(f"Monetary schema step committed and observed: {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
