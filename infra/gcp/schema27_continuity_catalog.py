"""Native read-only catalog and liability observation on the recovered database."""

from __future__ import annotations

import hmac
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from weakref import WeakKeyDictionary

from infra.gcp import browser_schema19_release as browser19
from infra.gcp import schema27_continuity_scope as scope
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from scripts import browser_schema19_step as roles
from scripts import email_password_schema21_contract as reset21
from scripts import email_password_schema21_step as financial_catalog
from scripts import evidence_writer_schema26_contract as schema26
from scripts import monetary_cutover_preflight as native
from scripts import notification_schema27_step as retained
from scripts import schema25_app_release_contract as financial
from scripts import unified_credit_schema25_step as unified25

BOOTSTRAP_OID = 16392
neon = scope.neon
_authenticated = WeakKeyDictionary()


@dataclass(frozen=True, slots=True)
class Schema26Observation:
    catalog_sha256: str
    retained_acl_sha256: str
    protected_liabilities: tuple[tuple[str, str, int], ...]


def _need(value, reason):
    if not value:
        raise neon.retained.NeonIdentityDenied("neon_continuity_" + reason)


def _identity(database: scope.ContinuityDatabasePin) -> roles.MigrationIdentity:
    return roles.MigrationIdentity(
        database.cluster_sha256, database.database_oid, BOOTSTRAP_OID,
        "public", 2200, neon.NAMESPACE_OWNER_OID, neon.DATABASE_OWNER, BOOTSTRAP_OID,
        *database.migrator_role, *database.runtime_role, *database.observer_role,
    )


def _take_recovery_lock(driver, identity: roles.MigrationIdentity) -> None:
    driver.autocommit = True
    target = driver.execute(
        "SELECT d.oid,d.datdba,current_user,session_user,"
        "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) "
        "FROM pg_catalog.pg_database d WHERE d.datname=pg_catalog.current_database()"
    ).fetchone()
    _need(target is not None and target[:4] == (
        identity.database_oid, identity.database_owner_oid, identity.observer, identity.observer,
    ) and type(target[4]) is str and sha256(target[4].encode()).hexdigest() == identity.cluster_sha256,
          "recovery_lock_target_mismatch")
    available = driver.execute(
        "SELECT CASE WHEN pg_catalog.current_setting('transaction_read_only')='on' "
        "THEN pg_catalog.pg_try_advisory_lock(%s) ELSE FALSE END",
        (retained.LOCK_ID,),
    ).fetchone()
    _need(available == (True,), "original_transaction_pending")
    driver.autocommit = False


@contextmanager
def authenticated_observer(database: scope.ContinuityDatabasePin, nonce: str, purpose: neon.Purpose,
                           *, recovery: bool = False):
    """Yield one verified read-only snapshot for the fixed private observer."""
    _need(type(database) is scope.ContinuityDatabasePin and scope._nonce(nonce)
          and type(purpose) is neon.Purpose and type(recovery) is bool, "catalog_scope_invalid")
    database.validate()
    password = scope._observer_password()
    _need(hmac.compare_digest(scope._commitment(nonce, password), database.observer_commitment_sha256),
          "observer_commitment_changed")
    identity = _identity(database)
    engine = None
    try:
        with neon._connection(purpose, password, neon.OBSERVER, neon.APPLICATION) as driver:
            if recovery:
                # Acquire before any repeatable-read snapshot. Checking the
                # lock after the marker can miss a commit before acquisition.
                _take_recovery_lock(driver, identity)
            engine = create_engine("postgresql+psycopg://", creator=lambda: driver,
                                   poolclass=StaticPool, echo=False, hide_parameters=True)
            with engine.connect() as connection, connection.begin() as transaction:
                try:
                    _need(connection.connection.driver_connection is driver, "catalog_session_changed")
                    connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, public, pg_temp")
                    connection.exec_driver_sql("SET LOCAL statement_timeout='2s'")
                    connection.exec_driver_sql("SET LOCAL lock_timeout='1s'")
                    connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                    neon._verify_catalog(driver, purpose, neon.OBSERVER, neon.OBSERVER_OID)
                    owner = connection.scalar(text(
                        "SELECT datdba FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()"
                    ))
                    _need(owner == identity.database_owner_oid, "catalog_database_owner_changed")
                    _authenticated[connection] = (purpose, identity, transaction)
                    yield connection
                finally:
                    _authenticated.pop(connection, None)
                    if transaction.is_active:
                        transaction.rollback()
    finally:
        if engine is not None:
            engine.dispose()


def _verify_migrator_session(connection, identity: roles.MigrationIdentity) -> None:
    functions = connection.execute(text(
        "SELECT p.oid,p.proowner,p.prosecdef,p.prosrc,p.proretset,p.proargtypes::text "
        "FROM pg_catalog.pg_proc p WHERE p.pronamespace=11 "
        "AND p.proname IN ('pg_stat_get_activity','pg_backend_pid') ORDER BY p.oid LIMIT 3"
    )).all()
    _need([tuple(row) for row in functions] == [
        (2022, 10, False, "pg_stat_get_activity", True, "23"),
        (2026, 10, False, "pg_backend_pid", False, ""),
    ], "native_session_catalog_changed")
    own_pid = connection.scalar(text("SELECT pg_catalog.pg_backend_pid()"))
    sessions = connection.execute(text(
        "SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_get_activity(NULL::integer) "
        "WHERE datid=:db AND (pid=:own OR usesysid=:migrator) ORDER BY pid LIMIT 3"
    ), {"db": identity.database_oid, "own": own_pid, "migrator": identity.migrator_oid}).all()
    _need(type(own_pid) is int and own_pid > 0
          and [tuple(row) for row in sessions if row[0] == own_pid]
          == [(own_pid, identity.observer_oid, identity.database_oid)], "native_observer_session_changed")
    scope.rolling.release.verify_fresh_role_phase(connection, {
        "observer": (identity.observer, identity.observer_oid),
        "replacement_runtime": (identity.runtime, identity.runtime_oid),
        "replacement_migration": (identity.migrator, identity.migrator_oid),
    }, "serving", [tuple(row) for row in sessions if row[0] != own_pid])


def read_schema26(connection, *, expected_liabilities=None) -> Schema26Observation:
    """Read protected schema26 state from an active authenticated snapshot."""
    verified = _authenticated.get(connection)
    _need(verified is not None and verified[0] is neon.Purpose.BEFORE_SCHEMA27
          and verified[2].is_active and connection.get_transaction() is verified[2], "catalog_session_unverified")
    _, identity, _ = verified
    _verify_migrator_session(connection, identity)
    financial_catalog.verify_financial_catalog(connection, native)
    browser19.verify_permissions(connection, identity.namespace, identity.runtime, identity.migrator, identity.observer)
    reset21.verify_reset_permissions(connection, identity.namespace, identity.runtime, identity.migrator, identity.observer)
    unified25.verify_new_permissions(connection, identity)
    catalog = schema26.observe(connection, namespace_oid=identity.namespace_oid)
    schema26.verify_writer_permissions(connection, runtime=identity.runtime, migrator=identity.migrator)
    liabilities = financial.liability_pins(connection)
    _need(expected_liabilities is None or liabilities == expected_liabilities, "protected_liability_changed")
    return Schema26Observation(catalog, scope.rolling.sha(retained.old_acls(connection, identity)), liabilities)


def capture_schema26(database: scope.ContinuityDatabasePin, nonce: str) -> Schema26Observation:
    """Observe protected state before a final bridge plan or journal exists."""
    with authenticated_observer(database, nonce, neon.Purpose.BEFORE_SCHEMA27) as connection:
        return read_schema26(connection)


def observe_schema26(custody: scope.BridgeCustody, journal: scope.rolling.Journal,
                     *, expected_liabilities=None) -> Schema26Observation:
    scope.verify_journal(custody, journal)
    with authenticated_observer(custody.database, custody.nonce, neon.Purpose.BEFORE_SCHEMA27) as connection:
        return read_schema26(connection, expected_liabilities=expected_liabilities)
