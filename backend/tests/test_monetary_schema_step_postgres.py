"""Actual bounded forward migration on UUID-owned local PostgreSQL schemas."""

from __future__ import annotations

import pytest
from backend.tests.test_monetary_cutover_schema11_postgres import context as context
from sqlalchemy import text

from scripts import monetary_schema_step as step


def execute(context, expected="20261008_0009", target="20261009_0010", **updates):
    engine, _, operator, inventory = context
    arguments = dict(expected=expected, target=target,
                     identifier_hash=inventory["expected_system_identifier_sha256"],
                     database_oid=inventory["expected_database_oid"],
                     namespace=inventory["database_namespace"], migration_role=operator)
    arguments.update(updates)
    step.upgrade_step(engine, **arguments)


def schema(context):
    with context[0].connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_actual_four_forward_steps_observe_each_revision_and_nullable_expense_history(context):
    for expected, target in zip(step.CHAIN[:-1], step.CHAIN[1:], strict=True):
        assert schema(context) == expected
        execute(context, expected, target)
        assert schema(context) == target
    with context[0].connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() AND indexname IN ('ix_payment_orders_paid_at','ix_payment_refunds_processed_at','ix_model_liability_created','ix_model_liability_state_created','ix_service_reservations_state_created')")).scalar_one() == 5


@pytest.mark.parametrize("updates,reason", [
    ({"identifier_hash": "f" * 64}, "migration_database_identity_mismatch"),
    ({"database_oid": 1}, "migration_database_identity_mismatch"),
    ({"migration_role": "synthetic_wrong_role"}, "migration_role_mismatch"),
    ({"expected": "20261009_0010", "target": "20261009_0011"}, "migration_observed_schema_mismatch"),
    ({"target": "20261009_0013"}, "migration_step_or_identity_invalid"),
])
def test_wrong_identity_jump_or_replayed_step_does_not_migrate(context, updates, reason):
    with pytest.raises(step.StepDenied, match=reason):
        execute(context, **updates)
    assert schema(context) == "20261008_0009"


def test_actual_migration_lock_contention_refuses_without_waiting_or_advancing(context):
    with context[0].connect() as owner:
        owner.execute(text("SELECT pg_advisory_lock(:lock)"), {"lock": step.LOCK_ID})
        try:
            with pytest.raises(step.StepDenied, match="migration_already_running"):
                execute(context)
            assert schema(context) == "20261008_0009"
        finally:
            owner.execute(text("SELECT pg_advisory_unlock(:lock)"), {"lock": step.LOCK_ID})
            owner.commit()


def test_failure_rolls_back_partial_ddl_and_releases_the_actual_advisory_lock(context, monkeypatch):
    def fail(config, target):
        config.attributes["connection"].execute(text("CREATE TABLE synthetic_partial (id integer)"))
        raise RuntimeError("synthetic migration transport failure")
    monkeypatch.setattr(step.command, "upgrade", fail)
    with pytest.raises(RuntimeError, match="synthetic migration transport failure"):
        execute(context)
    assert schema(context) == "20261008_0009"
    with context[0].connect() as connection:
        assert connection.execute(text("SELECT to_regclass('synthetic_partial')")).scalar_one() is None
        assert connection.execute(text("SELECT pg_try_advisory_lock(:lock)"), {"lock": step.LOCK_ID}).scalar_one() is True
        connection.execute(text("SELECT pg_advisory_unlock(:lock)"), {"lock": step.LOCK_ID})
        connection.commit()


def test_schema13_uses_observed_table_size_and_refuses_a_larger_index_window(context, monkeypatch):
    for expected, target in zip(step.CHAIN[:3], step.CHAIN[1:4], strict=True):
        execute(context, expected, target)
    monkeypatch.setattr(step, "MAX_INDEX_BYTES", 0)
    with pytest.raises(step.StepDenied, match="schema13_index_window_requires_separate_large_table_plan"):
        execute(context, "20261009_0012", "20261009_0013")
    assert schema(context) == "20261009_0012"
    with context[0].connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND column_name='cost_policy_snapshot'")).scalar_one() == 0


def test_actual_runtime_permissions_refuse_missing_new_table_and_sequence_grants(context):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra/gcp"))
    import monetary_release as release
    engine, roles, operator, inventory = context
    runtime = roles["replacement_runtime"]
    namespace = inventory["database_namespace"]
    with engine.begin() as connection:
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{runtime}"'))
        connection.execute(text(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{namespace}" TO "{runtime}"'))
        connection.execute(text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{namespace}" TO "{runtime}"'))
        release.verify_runtime_permissions(connection, namespace, runtime, operator)
        connection.execute(text(f'REVOKE DELETE ON employer_artifact_uploads FROM "{runtime}"'))
        with pytest.raises(release.ReleaseDenied, match="runtime_table_or_migration_ownership_permissions_missing"):
            release.verify_runtime_permissions(connection, namespace, runtime, operator)
        connection.execute(text(f'GRANT DELETE ON employer_artifact_uploads TO "{runtime}"'))
    execute(context)
    with engine.connect() as connection:
        with pytest.raises(release.ReleaseDenied, match="runtime_table_or_migration_ownership_permissions_missing"):
            release.verify_runtime_permissions(connection, namespace, runtime, operator)
    with engine.begin() as connection:
        connection.execute(text(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{namespace}" TO "{runtime}"'))
        connection.execute(text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{namespace}" TO "{runtime}"'))
        release.verify_runtime_permissions(connection, namespace, runtime, operator)
        connection.execute(text(f'REVOKE USAGE ON ALL SEQUENCES IN SCHEMA "{namespace}" FROM "{runtime}"'))
        with pytest.raises(release.ReleaseDenied, match="runtime_sequence_usage_missing"):
            release.verify_runtime_permissions(connection, namespace, runtime, operator)


def test_owned_backup_exporter_holds_real_snapshot_and_scrubs_ambient_libpq(context, monkeypatch):
    import os
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra/gcp"))
    import monetary_release as release
    engine, _, _, data = context
    inventory = SimpleNamespace(expected_system_identifier_sha256=data["expected_system_identifier_sha256"],
                                expected_database_oid=data["expected_database_oid"], database_namespace=data["database_namespace"])
    boundary = release.NativeBoundary(Path(__file__).resolve().parents[2])
    monkeypatch.setattr(boundary, "_database_context", lambda *args: (None, (engine, inventory), {}, ()))
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.123")
    monkeypatch.setenv("PGSERVICE", "synthetic-unreviewed-service")
    with boundary._backup_snapshot(None) as (snapshot, _):
        assert "PGHOSTADDR" not in os.environ and "PGSERVICE" not in os.environ
        with engine.begin() as writer:
            writer.execute(text("INSERT INTO users(id,email,password_hash,ai_credits,job_service_credits) VALUES (321,'snapshot@example.invalid','synthetic',50,0)"))
        with engine.connect() as importer:
            importer.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            importer.exec_driver_sql(f"SET TRANSACTION SNAPSHOT '{snapshot}'")
            assert importer.execute(text("SELECT count(*) FROM users WHERE id=321")).scalar_one() == 0
    assert os.environ["PGHOSTADDR"] == "192.0.2.123" and os.environ["PGSERVICE"] == "synthetic-unreviewed-service"
    monkeypatch.delenv("PGHOSTADDR")
    monkeypatch.delenv("PGSERVICE")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users WHERE id=321")).scalar_one() == 1
