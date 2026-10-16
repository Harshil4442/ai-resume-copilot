"""Actual PG17 native IDs; provider transport is isolated, never claimed live.

The loopback fixture cannot satisfy the exact Neon hostname/TLS/source contract.
Only verify_transport/verify_native_target are isolated for native-session tests;
actual psycopg user/PID/version and PostgreSQL catalogs/activity stay genuine.
"""

from __future__ import annotations

import pytest
from backend.tests.test_monetary_cutover_session_ids_postgres import context as context
from backend.tests.test_monetary_cutover_session_ids_postgres import (
    minimal_context as minimal_context,
)
from backend.tests.test_monetary_cutover_session_ids_postgres import old_login
from backend.tests.test_monetary_cutover_session_ids_postgres import pg_engine as pg_engine
from backend.tests.test_nologin_role_preparation_postgres import (
    prepared_context as prepared_context,
)
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

from scripts import monetary_cutover_preflight as preflight
from scripts import neon_direct_identity as neon
from scripts import nologin_role_preparation as preparation


@pytest.fixture
def isolated_provider_policy(monkeypatch):
    """Only native-SQL semantics are under test; no driver field is spoofed."""
    calls = []
    monkeypatch.setattr(
        neon, "verify_transport", lambda connection, binding: calls.append("transport")
    )
    monkeypatch.setattr(
        neon, "verify_native_target", lambda connection, binding, database: calls.append("target")
    )
    return neon.NeonDirectProxyBinding(
        "isolated-local-native-policy", "prefer", neon.CAPTURE_OPTIONS, "capture"
    ), calls


def native(connection, context, binding):
    connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
    return preflight._native_session_ids(
        connection, context[3]["expected_database_oid"], neon_binding=binding
    )


def test_actual_genuine_local_driver_refuses_unissued_binding_before_sql(minimal_context):
    observing = minimal_context[0].cutover_readonly_observer
    calls = []

    def count(_connection, _cursor, statement, parameters, _execution, _many):
        calls.append(statement)

    event.listen(observing, "before_cursor_execute", count)
    try:
        with observing.connect() as connection:
            with pytest.raises(neon.NeonIdentityDenied, match="native_binding_unavailable"):
                native(
                    connection,
                    minimal_context,
                    neon.NeonDirectProxyBinding("invalid", "prefer", "", "capture"),
                )
        # Only the explicit SET LOCAL in this test helper occurs; no identity SQL.
        assert len(calls) == 1 and calls[0] == "SET LOCAL search_path=pg_catalog"
    finally:
        event.remove(observing, "before_cursor_execute", count)


def test_actual_native_own_row_and_old_session_preserved(minimal_context, isolated_provider_policy):
    binding, calls = isolated_provider_policy
    with old_login(minimal_context) as old_pid:
        with minimal_context[0].cutover_readonly_observer.connect() as connection:
            own = connection.execute(text("SELECT pg_catalog.pg_backend_pid()")).scalar_one()
            rows, contract = native(connection, minimal_context, binding)
            assert len([row for row in rows if row[0] == old_pid]) == 1
            assert all(row[0] != own for row in rows)
    assert calls == ["transport", "target"]
    assert contract["native_own_session_bound"] is True
    assert contract["protocol_pid_is_cancellation_key"] is True
    assert contract["transport_contract"] == "neon_direct_proxy"


@pytest.mark.parametrize("database_id", ["null", "foreign"])
def test_actual_old_session_id_not_filtered_by_database(
    minimal_context, isolated_provider_policy, database_id
):
    binding, _ = isolated_provider_policy
    observing = minimal_context[0].cutover_readonly_observer
    replacement = (
        "NULL::oid"
        if database_id == "null"
        else str(minimal_context[3]["expected_database_oid"] + 1) + "::oid"
    )
    with old_login(minimal_context) as old_pid:

        def tamper(_connection, _cursor, statement, parameters, _execution, _many):
            if "SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity" in statement:
                statement = statement.replace(
                    "pid,usesysid,datid",
                    f"pid,usesysid,CASE WHEN pid={old_pid} THEN {replacement} ELSE datid END AS datid",
                )
            return statement, parameters

        event.listen(observing, "before_cursor_execute", tamper, retval=True)
        try:
            with observing.connect() as connection:
                rows, _ = native(connection, minimal_context, binding)
                old = [row for row in rows if row[0] == old_pid]
                assert len(old) == 1 and old[0][1] is not None
                assert old[0][2] == (
                    None
                    if database_id == "null"
                    else minimal_context[3]["expected_database_oid"] + 1
                )
        finally:
            event.remove(observing, "before_cursor_execute", tamper)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing", "authenticated_session_mismatch"),
        ("duplicate", "identifier_shape_mismatch"),
        ("role", "authenticated_session_mismatch"),
        ("database", "authenticated_session_mismatch"),
    ],
)
def test_actual_missing_duplicate_wrong_own_row_refuses(
    minimal_context, isolated_provider_policy, change, reason
):
    binding, _ = isolated_provider_policy
    observing = minimal_context[0].cutover_readonly_observer
    changed = []

    def tamper(_connection, _cursor, statement, parameters, _execution, _many):
        if "SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity" in statement:
            changed.append(True)
            if change == "missing":
                statement = statement.replace(
                    "ORDER BY pid", "WHERE pid<>pg_catalog.pg_backend_pid() ORDER BY pid"
                )
            elif change == "duplicate":
                statement = "SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity UNION ALL SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity WHERE pid=pg_catalog.pg_backend_pid() ORDER BY pid LIMIT 257"
            elif change == "role":
                statement = statement.replace(
                    "pid,usesysid,datid",
                    "pid,CASE WHEN pid=pg_catalog.pg_backend_pid() THEN 1::oid ELSE usesysid END,datid",
                )
            else:
                statement = statement.replace(
                    "pid,usesysid,datid",
                    "pid,usesysid,CASE WHEN pid=pg_catalog.pg_backend_pid() THEN NULL::oid ELSE datid END",
                )
        return statement, parameters

    event.listen(observing, "before_cursor_execute", tamper, retval=True)
    try:
        with observing.connect() as connection:
            with pytest.raises(preflight.Denied, match=reason):
                native(connection, minimal_context, binding)
        assert changed == [True]
    finally:
        event.remove(observing, "before_cursor_execute", tamper)


@pytest.mark.parametrize("identity_change", ["role", "session_authorization"])
def test_actual_elevated_session_cannot_masquerade_under_provider_route(
    minimal_context, isolated_provider_policy, identity_change
):
    binding, _ = isolated_provider_policy
    admin = minimal_context[0]
    observer = admin.cutover_readonly_observer.url.username
    candidate = create_engine(admin.url, poolclass=NullPool)

    def configure(dbapi_connection, _record):
        with dbapi_connection.cursor() as cursor:
            command = "SET ROLE" if identity_change == "role" else "SET SESSION AUTHORIZATION"
            cursor.execute(f'{command} "{observer}"')

    event.listen(candidate, "connect", configure)
    try:
        with candidate.connect() as connection:
            with pytest.raises(
                preflight.Denied, match="authenticated_role_mismatch|protocol_identity_mismatch"
            ):
                native(connection, minimal_context, binding)
    finally:
        candidate.dispose()


def test_actual_preparation_adapter_retains_native_authenticated_proof(
    prepared_context, isolated_provider_policy
):
    binding, calls = isolated_provider_policy
    with prepared_context.actor.connect() as connection:
        connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
        observed = preparation._authenticated(connection, neon_binding=binding)
        assert observed["current_name"] == prepared_context.actor_name
        assert observed["session_name"] == prepared_context.actor_name
        assert observed["createrole"] is True and observed["superuser"] is False
        assert observed["pid"] > 0
    assert calls == ["transport", "transport", "target"]


def test_capture_binding_cannot_authorize_role_preparation(prepared_context):
    binding = neon.NeonDirectProxyBinding("invalid", "prefer", neon.CAPTURE_OPTIONS, "capture")
    with pytest.raises(preparation.PreparationDenied, match="preparation_neon_purpose_mismatch"):
        preparation.prepare(prepared_context.actor, prepared_context.manifest, neon_binding=binding)


@pytest.fixture
def factory_binding(monkeypatch, tmp_path):
    """Fake native acquisition only; actual loopback DBAPI below is genuine."""
    import hashlib
    import json
    from types import SimpleNamespace

    receipt = {
        "organization_id": neon.ORGANIZATION,
        "project_id": neon.NEON_PROJECT,
        "branch_id": neon.BRANCH,
        "primary_endpoint_id": neon.ENDPOINT,
    }
    file = tmp_path / "receipt.json"
    file.write_text(json.dumps(receipt))
    monkeypatch.setattr(neon, "CONTROL_RECEIPT", file)
    monkeypatch.setattr(neon, "CONTROL_RECEIPT_SHA", hashlib.sha256(file.read_bytes()).hexdigest())
    response = {
        "metadata": {"name": neon.REVISION},
        "status": {"imageDigest": neon.IMAGE},
        "spec": {
            "containers": [
                {
                    "image": neon.IMAGE,
                    "env": [
                        {
                            "name": "DATABASE_URL",
                            "value": f"postgresql://{neon.ACTOR}:synthetic-only@{neon.ENDPOINT}.ap-southeast-1.aws.neon.tech/{neon.DATABASE}?sslmode=require",
                        }
                    ],
                }
            ]
        },
    }
    monkeypatch.setattr(
        neon.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(response).encode()),
    )
    return neon.collect_neon_direct_proxy()[0]


def test_actual_raw_driver_guard_precedes_dialect_initialization(
    pg_engine, factory_binding, monkeypatch
):
    from psycopg import Connection as PsycopgConnection

    events = []

    def isolated_policy(driver, binding):
        assert isinstance(driver, PsycopgConnection) and not driver.closed
        assert binding is factory_binding and driver.info.user == pg_engine.url.username
        events.append("raw_driver_guard")

    monkeypatch.setattr(neon, "_verify_driver_transport", isolated_policy)
    # This test-specific target uses loopback. It proves actual SQLAlchemy
    # initialization order, not acceptance of the pinned Neon TLS endpoint.
    engine = neon._TransientTarget(factory_binding, pg_engine.url).engine()
    original = engine.dialect.initialize

    def initialize(connection):
        events.append("dialect_initialization")
        assert events == ["raw_driver_guard", "dialect_initialization"]
        original(connection)

    monkeypatch.setattr(engine.dialect, "initialize", initialize)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT pg_catalog.pg_backend_pid()")).scalar_one() > 0
        assert events == ["raw_driver_guard", "dialect_initialization"]
    finally:
        engine.dispose()


def test_actual_transport_failure_closes_raw_driver_before_dialect_and_never_retries(
    pg_engine, factory_binding, monkeypatch
):
    drivers = []
    initializations = []
    original_guard = neon._verify_driver_transport

    def inspect(driver, binding):
        drivers.append(driver)
        original_guard(driver, binding)

    monkeypatch.setattr(neon, "_verify_driver_transport", inspect)
    engine = neon._TransientTarget(factory_binding, pg_engine.url).engine()
    monkeypatch.setattr(
        engine.dialect, "initialize", lambda connection: initializations.append(True)
    )
    try:
        with pytest.raises(neon.NeonIdentityDenied, match="connection_transport_refused"):
            engine.connect()
        assert len(drivers) == 1 and drivers[0].closed is True
        assert initializations == []
    finally:
        engine.dispose()
