"""Exact catalog on owned PG17. The socket cannot reach a serving database."""

from __future__ import annotations

from contextlib import contextmanager

import psycopg
import pytest
from infra.gcp import schema27_continuity_catalog as catalog
from sqlalchemy import text
from test_mail_budget_postgres import owned_pg_url as owned_pg_url
from test_notification_schema27_step_postgres import owned26 as owned26

NONCE = "a" * 32
PASSWORD = "x" * 56


@pytest.fixture
def connection_scope(owned26, monkeypatch):
    identity = owned26.identity
    runtime, observer = identity.runtime, identity.observer
    admin_url = owned26.engine.url
    driver_url = admin_url.set(drivername="postgresql", username=observer, password=PASSWORD)
    with owned26.engine.begin() as db:
        db.exec_driver_sql(f'ALTER ROLE "{observer}" PASSWORD \'{PASSWORD}\'')
        db.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "{observer}"')
        db.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
        db.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{observer}"')
        db.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO "{runtime}"')
        db.exec_driver_sql(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "{runtime}"')
        db.exec_driver_sql(f'REVOKE UPDATE,DELETE ON public.candidate_identity_history FROM "{runtime}"')
        db.exec_driver_sql(f'REVOKE DELETE ON public.employer_host_pacing FROM "{runtime}"')

    # Native TLS is covered by the retained identity tests. This test replaces
    # only connection creation; core SQL and read-only behavior are real PG17.
    @contextmanager
    def connect(_purpose, password, _actor, _application):
        assert password == PASSWORD
        assert driver_url.host == "127.0.0.1"
        with psycopg.connect(driver_url.render_as_string(hide_password=False),
                             options="-c default_transaction_read_only=on") as driver:
            driver.read_only = True
            driver.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            yield driver

    monkeypatch.setattr(catalog.neon, "_connection", connect)
    monkeypatch.setattr(catalog.neon, "OBSERVER", observer)
    monkeypatch.setattr(catalog.neon, "OBSERVER_OID", identity.observer_oid)
    monkeypatch.setattr(catalog.neon, "DATABASE", admin_url.database)
    monkeypatch.setattr(catalog.neon, "DATABASE_OID", identity.database_oid)
    monkeypatch.setattr(catalog.neon, "DATABASE_OWNER", identity.bootstrap)
    monkeypatch.setattr(catalog.neon, "NAMESPACE_OWNER_OID", identity.namespace_owner_oid)
    monkeypatch.setattr(catalog.neon, "CLUSTER_SHA256", identity.cluster_sha256)
    monkeypatch.setattr(catalog, "_identity", lambda _database: identity)
    monkeypatch.setattr(catalog.scope, "_observer_password", lambda: PASSWORD)
    return catalog.scope.capture_database_pin(NONCE), identity


def test_exact_catalog_liabilities_and_journal_share_readonly_session(owned26, connection_scope, tmp_path):
    database, identity = connection_scope
    with owned26.engine.connect() as connection:
        expected = catalog.Schema26Observation(
            catalog.schema26.observe(connection),
            catalog.scope.rolling.sha(catalog.retained.old_acls(connection, identity)),
            catalog.financial.liability_pins(connection),
        )
        with pytest.raises(catalog.neon.retained.NeonIdentityDenied, match="catalog_session_unverified"):
            catalog.read_schema26(connection)
    assert len(expected.protected_liabilities) == 1
    assert catalog.capture_schema26(database, NONCE) == expected
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(mode=0o700)
    custody = catalog.scope.BridgeCustody(NONCE, "b" * 64, database)
    journal = catalog.scope.rolling.Journal(journal_dir, custody)
    journal.record(catalog.scope.JOURNAL_BINDING, custody.journal_binding())
    observed = catalog.observe_schema26(custody, journal, expected_liabilities=expected.protected_liabilities)
    assert observed == expected
    with pytest.raises(catalog.neon.retained.NeonIdentityDenied, match="protected_liability_changed"):
        catalog.observe_schema26(custody, journal, expected_liabilities=())
    with catalog.authenticated_observer(database, NONCE, catalog.neon.Purpose.BEFORE_SCHEMA27) as observer:
        read_only, isolation, pid = observer.execute(text(
            "SELECT current_setting('transaction_read_only'),"
            "current_setting('transaction_isolation'),pg_backend_pid()"
        )).one()
        assert read_only == "on" and isolation == "repeatable read"
        assert catalog.read_schema26(observer) == expected
        assert observer.scalar(text("SELECT pg_backend_pid()")) == pid
        with owned26.engine.begin() as writer:
            writer.execute(text(
                "UPDATE public.model_cost_liabilities SET reserved_cost_micros=24 WHERE id='owned-unknown'"
            ))
        assert catalog.read_schema26(observer, expected_liabilities=expected.protected_liabilities) == expected
        observer.rollback()
        with pytest.raises(catalog.neon.retained.NeonIdentityDenied, match="catalog_session_unverified"):
            catalog.read_schema26(observer)
    with pytest.raises(catalog.neon.retained.NeonIdentityDenied, match="protected_liability_changed"):
        catalog.observe_schema26(custody, journal, expected_liabilities=expected.protected_liabilities)


def test_reconciliation_lock_precedes_snapshot_and_remains_held(owned26, connection_scope):
    database, _ = connection_scope
    purpose = catalog.neon.Purpose.BEFORE_SCHEMA27
    lock = text("SELECT pg_try_advisory_xact_lock(:key)")
    params = {"key": catalog.retained.LOCK_ID}
    with owned26.engine.begin() as writer:
        assert writer.scalar(lock, params) is True
        with pytest.raises(catalog.neon.retained.NeonIdentityDenied, match="original_transaction_pending"):
            with catalog.authenticated_observer(database, NONCE, purpose, recovery=True):
                pytest.fail("pending writer must refuse before creating an observer snapshot")
    with catalog.authenticated_observer(database, NONCE, purpose, recovery=True) as observer:
        assert catalog.read_schema26(observer).catalog_sha256
        with owned26.engine.begin() as another:
            assert another.scalar(lock, params) is False
    with owned26.engine.begin() as another:
        assert another.scalar(lock, params) is True
    with pytest.raises(catalog.neon.retained.NeonIdentityDenied, match="neon_continuity_exact_schema_mismatch"):
        with catalog.authenticated_observer(database, NONCE, catalog.neon.Purpose.AFTER_SCHEMA27, recovery=True):
            pytest.fail("schema27 context cannot expose a schema26 connection")
