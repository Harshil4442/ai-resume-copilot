"""Schema27-only empty-wallet batching on an owned local PostgreSQL17 cluster."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import NullPool
from test_mail_budget_postgres import owned_pg_url as owned_pg_url

from scripts import notification_schema27_contract as contract

TABLES = contract.before.previous.TABLES
LEGACY = contract.before.previous.verify_empty_new_tables
BATCHED = contract._verify_empty_new_tables
REFUSAL = "schema25_new_financial_activity_refused"


@pytest.fixture
def owned_tables(owned_pg_url):
    admin = create_engine(owned_pg_url, poolclass=NullPool, hide_parameters=True)
    suffix = uuid4().hex
    namespace, actor = "empty27_" + suffix, "empty27_reader_" + suffix
    reader = None
    try:
        with admin.begin() as connection:
            assert connection.engine.url.host == "127.0.0.1"
            assert int(connection.scalar(text("SHOW server_version_num"))) // 10000 == 17
            connection.exec_driver_sql(f'CREATE SCHEMA "{namespace}"')
            connection.exec_driver_sql(f'CREATE ROLE "{actor}" LOGIN NOINHERIT NOSUPERUSER '
                'NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
            for table in TABLES:
                connection.exec_driver_sql(f'CREATE TABLE "{namespace}"."{table}" (marker integer)')
            connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{actor}"')
            connection.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{namespace}" TO "{actor}"')
        reader = create_engine(admin.url.set(username=actor), poolclass=NullPool, hide_parameters=True,
            isolation_level="REPEATABLE READ", connect_args={
                "options": "-c default_transaction_read_only=on -c search_path=pg_catalog"})
        yield SimpleNamespace(admin=admin, reader=reader, namespace=namespace, actor=actor)
    finally:
        if reader is not None:
            reader.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
            connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{actor}"')
        admin.dispose()


def session_state(connection):
    return tuple(connection.execute(text("SELECT pg_backend_pid(),"
        "current_setting('transaction_read_only'),current_setting('transaction_isolation'),"
        "current_setting('search_path'),txid_current_snapshot()::text")).one())


def observe(case, function):
    """Compare real results, refusal diagnostics and connection state, not SQL text."""
    statements = []
    with case.reader.connect() as connection:
        transaction = connection.begin()
        before = session_state(connection)
        failure, after, locks = None, None, None

        def record(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(connection, "before_cursor_execute", record)
        try:
            function(connection, case.namespace)
        except Exception as error:
            failure = error
        finally:
            event.remove(connection, "before_cursor_execute", record)
        try:
            assert connection.get_transaction() is transaction and transaction.is_active
            assert connection.get_nested_transaction() is None
            if not isinstance(failure, DBAPIError):
                after = session_state(connection)
                assert after == before
                locks = [tuple(row) for row in connection.execute(text(
                    "SELECT c.relname,l.mode,l.granted FROM pg_catalog.pg_locks l "
                    "JOIN pg_catalog.pg_class c ON c.oid=l.relation "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE l.pid=pg_backend_pid() AND n.nspname=:namespace "
                    "ORDER BY c.relname,l.mode"), {"namespace": case.namespace})]
            error_state = connection.connection.driver_connection.info.transaction_status.name
            signature = None if failure is None else (
                type(failure), str(failure), getattr(getattr(failure, "orig", None), "sqlstate", None))
            return SimpleNamespace(failure=signature, before=before, after=after,
                server_state=error_state, statements=statements, locks=locks)
        finally:
            transaction.rollback()


def test_empty_snapshot_matches_legacy_with_three_round_trips_and_unchanged_outer_session(owned_tables):
    old, new = observe(owned_tables, LEGACY), observe(owned_tables, BATCHED)
    assert old.failure is new.failure is None
    assert len(old.statements) == 14
    assert len(new.statements) == 3
    assert sum(statement.startswith("SELECT ") for statement in new.statements) == 1
    assert new.statements[0].startswith("SAVEPOINT ")
    assert new.statements[-1].startswith("RELEASE SAVEPOINT ")
    assert new.before[1:4] == ("on", "repeatable read", "pg_catalog")
    assert old.server_state == new.server_state == "INTRANS"
    assert old.locks == new.locks and len(new.locks) == len(TABLES)


@pytest.mark.parametrize("table", TABLES)
def test_each_of_all_fourteen_populated_tables_retains_the_exact_legacy_refusal(owned_tables, table):
    with owned_tables.admin.begin() as connection:
        connection.exec_driver_sql(f'INSERT INTO "{owned_tables.namespace}"."{table}" VALUES (1)')
    old, new = observe(owned_tables, LEGACY), observe(owned_tables, BATCHED)
    assert new.failure == old.failure == (contract.before.previous.Schema25Denied, REFUSAL, None)
    assert old.server_state == new.server_state == "INTRANS"
    assert old.locks == new.locks
    with owned_tables.admin.connect() as connection:
        assert connection.scalar(text(f'SELECT count(*) FROM "{owned_tables.namespace}"."{table}"')) == 1


@pytest.mark.parametrize("structural_failure", ["missing", "denied"])
@pytest.mark.parametrize("earlier_populated", [False, True])
def test_later_missing_or_inaccessible_table_keeps_legacy_exception_precedence(
        owned_tables, structural_failure, earlier_populated):
    namespace = owned_tables.namespace
    with owned_tables.admin.begin() as connection:
        if earlier_populated:
            connection.exec_driver_sql(f'INSERT INTO "{namespace}"."{TABLES[0]}" VALUES (1)')
        if structural_failure == "missing":
            connection.exec_driver_sql(f'DROP TABLE "{namespace}"."{TABLES[-1]}"')
        else:
            connection.exec_driver_sql(f'REVOKE SELECT ON "{namespace}"."{TABLES[-1]}" FROM "{owned_tables.actor}"')
    old, new = observe(owned_tables, LEGACY), observe(owned_tables, BATCHED)
    assert new.failure == old.failure
    if earlier_populated:
        assert new.failure == (contract.before.previous.Schema25Denied, REFUSAL, None)
        assert old.server_state == new.server_state == "INTRANS"
        assert old.locks == new.locks
    else:
        assert new.failure[2] == ("42P01" if structural_failure == "missing" else "42501")
        assert old.server_state == new.server_state == "INERROR"


class NoSQL:
    def begin_nested(self):
        pytest.fail("Invalid namespace must be refused before opening a savepoint")

    def execute(self, *_args):
        pytest.fail("Invalid namespace must be refused before SQL")


def test_invalid_namespace_retains_historical_refusal_before_sql():
    failures = []
    for function in (LEGACY, BATCHED):
        with pytest.raises(RuntimeError) as error:
            function(NoSQL(), 'public"; SELECT 1; --')
        failures.append((type(error.value), str(error.value)))
    assert failures[0] == failures[1]


class DriverFailure(Exception):
    def __init__(self, sqlstate):
        super().__init__("Synthetic local driver failure")
        self.sqlstate = sqlstate


class FailurePort:
    def __init__(self, failure):
        self.failure, self.requests, self.rollbacks, self.commits = failure, 0, 0, 0

    def begin_nested(self):
        return self

    def execute(self, *_args):
        self.requests += 1
        raise self.failure

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1


@pytest.mark.parametrize("invalidated,sqlstate", [(True, None), (False, None), (True, "42P01"), (False, "57014")])
def test_lost_or_unknown_connection_and_timeout_never_reconnect_or_retry(monkeypatch, invalidated, sqlstate):
    failure = OperationalError("synthetic observation", {}, DriverFailure(sqlstate),
                               connection_invalidated=invalidated)
    port = FailurePort(failure)
    monkeypatch.setattr(contract.before.previous, "verify_empty_new_tables",
        lambda *_args: pytest.fail("Unknown observation must not invoke a second reader"))
    with pytest.raises(OperationalError) as error:
        BATCHED(port, "public")
    assert error.value is failure
    assert port.requests == 1 and port.rollbacks == port.commits == 0


def test_structural_failure_cannot_become_acceptance_after_a_later_repair(monkeypatch):
    failure = OperationalError("synthetic observation", {}, DriverFailure("42P01"))
    port = FailurePort(failure)
    fallback = []
    monkeypatch.setattr(contract.before.previous, "verify_empty_new_tables", lambda *_args: fallback.append(True))
    with pytest.raises(OperationalError) as error:
        BATCHED(port, "public")
    assert error.value is failure
    assert fallback == [True] and port.requests == port.rollbacks == 1 and port.commits == 0
