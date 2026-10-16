"""Least-privilege native IDs; negative metadata tests never change pg_catalog."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from backend.tests.test_monetary_cutover_deleted_role_postgres import deleted_retired
from backend.tests.test_monetary_cutover_preflight_postgres import assert_refused, report
from backend.tests.test_monetary_cutover_preflight_postgres import context as context
from backend.tests.test_monetary_cutover_preflight_postgres import pg_engine as pg_engine
from psycopg import ConnectionInfo
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

from scripts import monetary_cutover_preflight as preflight


@pytest.fixture
def minimal_context(context):
    admin = context[0]
    observer = admin.cutover_readonly_observer.url.username
    with admin.begin() as connection:
        connection.execute(text(f'REVOKE pg_read_all_stats FROM "{observer}"'))
    with admin.cutover_readonly_observer.connect() as connection:
        assert not connection.execute(
            text(
                "SELECT pg_catalog.pg_has_role(current_user,'pg_read_all_stats','USAGE') OR pg_catalog.pg_has_role(current_user,'pg_monitor','USAGE')"
            )
        ).scalar_one()
    return context


@contextmanager
def old_login(context):
    admin, roles, _, _ = context
    with admin.begin() as connection:
        connection.execute(
            text(f"ALTER ROLE \"{roles['retired']}\" LOGIN PASSWORD 'synthetic-local-old'")
        )
    old = create_engine(
        admin.url.set(username=roles["retired"], password="synthetic-local-old"), poolclass=NullPool
    )
    try:
        with old.connect() as connection:
            yield connection.execute(text("SELECT pg_catalog.pg_backend_pid()")).scalar_one()
    finally:
        old.dispose()


def test_actual_minimal_login_without_monitor_has_complete_public_id_contract(minimal_context):
    result = report(minimal_context)
    native = result["observed_database"]["session_inventory"]
    assert native["contract"] == "pg17_native_public_pid_role_database_ids"
    assert 170000 <= native["server_version_num"] < 180000
    assert native["authenticated_login_bound"] is True
    assert native["protocol_identity_bound"] is True
    assert native["requires_all_session_activity_details"] is False
    assert result["database_gate_passed"] is True and result["cutover_ready"] is False


def test_foreign_ids_equal_admin_while_private_state_is_redacted(minimal_context):
    admin = minimal_context[0]
    with old_login(minimal_context) as pid:
        with admin.connect() as connection:
            expected = connection.execute(
                text("SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity WHERE pid=:pid"),
                {"pid": pid},
            ).one()
        with admin.cutover_readonly_observer.connect() as connection:
            actual = connection.execute(
                text(
                    "SELECT pid,usesysid,datid,state FROM pg_catalog.pg_stat_activity WHERE pid=:pid"
                ),
                {"pid": pid},
            ).one()
        assert tuple(actual[:3]) == tuple(expected) and actual[3] is None
        assert_refused(report(minimal_context), "retired_database_sessions_present")


def test_minimal_login_detects_actual_orphan_old_oid_after_deletion(minimal_context):
    with old_login(minimal_context):
        with deleted_retired(minimal_context) as oid:
            result = report(minimal_context, retired_role_mode="deleted", retired_role_oid=oid)
            assert result["observed_database"]["roles"]["retired"]["active_session_count"] == 1
            assert_refused(result, "retired_database_sessions_present")
            assert_refused(result, "unclassified_database_writers_or_sessions")


@pytest.mark.parametrize("identity_change", ["role", "session_authorization"])
def test_privileged_login_cannot_masquerade_as_observer(minimal_context, identity_change):
    admin, roles, operator, data = minimal_context
    observer = admin.cutover_readonly_observer.url.username
    candidate = create_engine(admin.url, poolclass=NullPool)

    def configure(dbapi_connection, _record):
        with dbapi_connection.cursor() as cursor:
            command = "SET ROLE" if identity_change == "role" else "SET SESSION AUTHORIZATION"
            cursor.execute(f'{command} "{observer}"')

    event.listen(candidate, "connect", configure)
    reason = (
        "database_observer_authenticated_role_mismatch"
        if identity_change == "role"
        else "database_observer_protocol_identity_mismatch"
    )
    try:
        with pytest.raises(preflight.Denied, match=reason):
            preflight.observe(
                candidate, preflight.Inventory.model_validate(data), roles, (operator,)
            )
    finally:
        candidate.dispose()


@pytest.mark.parametrize("channel", ["url", "connect_args", "environment"])
def test_privileged_startup_role_cannot_masquerade_as_observer(
    minimal_context, monkeypatch, channel
):
    admin, roles, operator, data = minimal_context
    observer = admin.cutover_readonly_observer.url.username
    options = f"-c role={observer}"
    url = admin.url
    connect_args = {}
    if channel == "url":
        url = url.update_query_dict({"options": options})
    elif channel == "connect_args":
        connect_args["options"] = options
    else:
        monkeypatch.setenv("PGOPTIONS", options)
    candidate = create_engine(url, connect_args=connect_args, poolclass=NullPool)
    try:
        with pytest.raises(preflight.Denied, match="database_observer_authenticated_role_mismatch"):
            preflight.observe(
                candidate, preflight.Inventory.model_validate(data), roles, (operator,)
            )
    finally:
        candidate.dispose()
        monkeypatch.undo()


@pytest.mark.parametrize(
    "attribute,replacement,reason",
    [
        ("user", None, "database_observer_protocol_identity_unavailable"),
        ("user", "", "database_observer_protocol_identity_unavailable"),
        ("user", "invalid\x00login", "database_observer_protocol_identity_unavailable"),
        ("user", "x" * 64, "database_observer_protocol_identity_unavailable"),
        ("user", "other-synthetic-login", "database_observer_protocol_identity_mismatch"),
        ("backend_pid", True, "database_observer_protocol_identity_unavailable"),
        ("backend_pid", 0, "database_observer_protocol_identity_unavailable"),
        ("backend_pid", 1, "database_observer_protocol_identity_mismatch"),
        ("server_version", "170011", "database_observer_protocol_identity_unavailable"),
        ("server_version", 170000, "database_observer_protocol_identity_mismatch"),
    ],
)
def test_protocol_identity_missing_malformed_or_disagreeing_refuses(
    minimal_context, monkeypatch, attribute, replacement, reason
):
    observing = minimal_context[0].cutover_readonly_observer
    with observing.connect() as connection:
        # Alter only the local driver's returned identity, never server catalog
        # or authentication. This challenges native/SQL disagreement in a real tx.
        monkeypatch.setattr(ConnectionInfo, attribute, property(lambda self: replacement))
        with pytest.raises(preflight.Denied, match=reason):
            preflight._native_session_ids(connection, minimal_context[3]["expected_database_oid"])
        monkeypatch.undo()


def test_unavailable_protocol_information_refuses_without_reading_secrets(minimal_context, monkeypatch):
    def unavailable(_self):
        raise AttributeError("synthetic missing login metadata")

    with minimal_context[0].cutover_readonly_observer.connect() as connection:
        monkeypatch.setattr(ConnectionInfo, "user", property(unavailable))
        with pytest.raises(preflight.Denied, match="database_observer_protocol_identity_unavailable"):
            preflight._native_session_ids(connection, minimal_context[3]["expected_database_oid"])
        monkeypatch.undo()


def test_unsupported_driver_cannot_supply_asserted_protocol_identity():
    asserted = SimpleNamespace(info=SimpleNamespace(user="synthetic", backend_pid=1, server_version=170011))
    connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=asserted))
    with pytest.raises(preflight.Denied, match="database_observer_protocol_identity_unavailable"):
        preflight._native_session_ids(connection, 1)


def test_owned_shadow_views_and_functions_cannot_supply_native_observation(minimal_context):
    admin, _, _, data = minimal_context
    namespace = data["database_namespace"]
    with admin.begin() as connection:
        connection.execute(
            text(
                f'CREATE VIEW "{namespace}".pg_stat_activity AS SELECT * FROM pg_catalog.pg_stat_activity WHERE false'
            )
        )
        connection.execute(
            text(
                f'CREATE VIEW "{namespace}".pg_roles AS SELECT * FROM pg_catalog.pg_roles WHERE false'
            )
        )
        connection.execute(
            text(
                f'CREATE FUNCTION "{namespace}".pg_backend_pid() RETURNS integer LANGUAGE sql AS $$ SELECT -1 $$'
            )
        )
        connection.execute(
            text(
                f'CREATE FUNCTION "{namespace}".pg_control_system() RETURNS TABLE(system_identifier bigint) LANGUAGE sql AS $$ SELECT 0::bigint $$'
            )
        )
        connection.execute(
            text(
                f'GRANT SELECT ON "{namespace}".pg_stat_activity,"{namespace}".pg_roles TO "{admin.cutover_readonly_observer.url.username}"'
            )
        )
    result = report(minimal_context)
    assert result["database_gate_passed"] is True
    assert result["observed_database"]["session_inventory"]["native_function_oid"] == 2022


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("version", "database_public_session_ids_version_unsupported"),
        ("function_owner", "database_native_session_function_identity_mismatch"),
        ("function_source", "database_native_session_function_identity_mismatch"),
        ("function_language", "database_native_session_function_identity_mismatch"),
        ("function_definer", "database_native_session_function_identity_mismatch"),
        ("function_output", "database_native_session_function_identity_mismatch"),
        ("view_owner", "database_native_session_view_identity_mismatch"),
        ("view_filter", "database_native_session_view_identity_mismatch"),
        ("view_columns", "database_native_session_view_shape_mismatch"),
        ("missing_own", "database_observer_authenticated_session_mismatch"),
        ("duplicate_pid", "database_native_session_identifier_shape_mismatch"),
        ("wrong_own_role", "database_observer_authenticated_session_mismatch"),
    ],
)
def test_tampered_native_responses_refuse_without_catalog_mutations(
    minimal_context, mutation, reason
):
    observing = minimal_context[0].cutover_readonly_observer
    changed = []

    def tamper(_connection, _cursor, statement, parameters, _execution, _many):
        original = statement
        if mutation == "version" and statement == "SHOW server_version_num":
            statement = "SELECT '180000'"
        elif "SELECT p.oid,p.proname,p.pronamespace" in statement:
            edits = {
                "function_owner": ("p.proowner", "0::oid"),
                "function_source": ("p.prosrc", "'synthetic_non_native'::text"),
                "function_language": ("p.prolang", "0::oid"),
                "function_definer": ("p.prosecdef", "true"),
                "function_output": ("p.proallargtypes", "ARRAY[23]::oid[]"),
            }
            if mutation in edits:
                statement = statement.replace(*edits[mutation])
        elif "AND c.relname='pg_stat_activity'" in statement:
            if mutation == "view_owner":
                statement = statement.replace("c.relowner", "0::oid")
            elif mutation == "view_filter":
                statement = statement.replace(
                    "pg_catalog.pg_get_viewdef(c.oid,false)",
                    "pg_catalog.pg_get_viewdef(c.oid,false) || ' WHERE false'",
                )
        elif "SELECT a.attname,a.atttypid" in statement and mutation == "view_columns":
            statement = statement.replace("a.atttypid", "0::oid")
        elif "SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity" in statement:
            if mutation == "missing_own":
                statement = statement.replace(
                    " ORDER BY pid", " WHERE pid<>pg_catalog.pg_backend_pid() ORDER BY pid"
                )
            elif mutation == "duplicate_pid":
                statement = "SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity WHERE pid=pg_catalog.pg_backend_pid() UNION ALL SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity WHERE pid=pg_catalog.pg_backend_pid()"
        elif (
            "SELECT pg_catalog.pg_backend_pid(),r.oid" in statement and mutation == "wrong_own_role"
        ):
            statement = statement.replace("r.oid", "0::oid")
        if statement != original:
            changed.append(mutation)
        return statement, parameters

    event.listen(observing, "before_cursor_execute", tamper, retval=True)
    try:
        with pytest.raises(preflight.Denied, match=reason):
            report(minimal_context)
        assert changed
    finally:
        event.remove(observing, "before_cursor_execute", tamper)


@pytest.mark.parametrize("database_id", ["null", "foreign"])
def test_old_role_not_dropped_for_null_or_foreign_database_id(minimal_context, database_id):
    admin = minimal_context[0]
    with admin.connect() as connection:
        oid = connection.execute(
            text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:name"),
            {"name": minimal_context[1]["retired"]},
        ).scalar_one()
    observing = admin.cutover_readonly_observer
    replacement = (
        "NULL::oid"
        if database_id == "null"
        else f"{minimal_context[3]['expected_database_oid'] + 1}::oid"
    )

    def tamper(_connection, _cursor, statement, parameters, _execution, _many):
        if "SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity" in statement:
            statement = statement.replace(
                "pid,usesysid,datid",
                f"pid,usesysid,CASE WHEN usesysid={oid} THEN {replacement} ELSE datid END AS datid",
            )
        return statement, parameters

    with old_login(minimal_context):
        event.listen(observing, "before_cursor_execute", tamper, retval=True)
        try:
            result = report(minimal_context)
            assert result["observed_database"]["roles"]["retired"]["active_session_count"] == 1
            assert_refused(result, "retired_database_sessions_present")
            assert_refused(result, "unclassified_database_writers_or_sessions")
        finally:
            event.remove(observing, "before_cursor_execute", tamper)
