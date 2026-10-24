"""Private candidate SQL views must not reconfigure an actively connecting pool."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine.base import OptionEngine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import QueuePool

from app.domains.browser_assistance.native_pairing import PairingProjection, activation_port
from app.domains.candidate_accounts.production import _private_engine
from app.domains.recovery.store import GuardUnavailable


def test_private_view_does_not_mutate_listeners_during_a_real_pool_connection(tmp_path):
    source = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-candidate.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=2,
        max_overflow=0,
        hide_parameters=False,
        echo=False,
    )
    entered, release = Event(), Event()

    def pause_connect(connection, record):
        entered.set()
        assert release.wait(5), "The owned connection was not released"

    def read_from_second_connection():
        with source.connect() as connection:
            return connection.scalar(text("SELECT 1"))

    try:
        # Initialize the dialect first, then occupy its original connection so
        # the concurrent checkout must execute the real connect-event iterator.
        with source.connect() as first:
            assert first.scalar(text("SELECT 1")) == 1
            event.listen(source.pool, "connect", pause_connect)
            listeners = tuple(source.pool.dispatch.connect)
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(read_from_second_connection)
                try:
                    assert entered.wait(5), "The owned pool checkout never reached connect"
                    private = _private_engine(source)
                finally:
                    release.set()
                assert pending.result(timeout=5) == 1
            assert tuple(source.pool.dispatch.connect) == listeners
            assert private.pool is source.pool
            assert private.dialect is source.dialect
            assert private.hide_parameters is True and private.echo is False
            assert source.hide_parameters is False and source.echo is False
    finally:
        release.set()
        source.dispose()


def test_private_views_preserve_trusted_connection_policy_options_and_engine_events(tmp_path):
    source = create_engine(
        f"sqlite:///{tmp_path / 'candidate-policy.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 0.25},
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        hide_parameters=False,
        echo=False,
    ).execution_options(logging_token="owned-candidate-policy")
    observed = []

    def connection_policy(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    def observe_execution(connection, cursor, statement, parameters, context, executemany):
        observed.append(connection.get_execution_options()["logging_token"])

    event.listen(source.pool, "connect", connection_policy)
    event.listen(source, "before_cursor_execute", observe_execution)
    listeners = tuple(source.pool.dispatch.connect)
    try:
        views = [_private_engine(source) for _ in range(8)]
        assert source.pool.checkedout() == 0  # Constructing a view acquires no SQL connection.
        for private in views:
            assert private.pool is source.pool and private.dialect is source.dialect
            assert private.get_execution_options() == source.get_execution_options()
            with private.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 250
        assert observed == ["owned-candidate-policy"] * 16
        assert tuple(source.pool.dispatch.connect) == listeners
        assert source.hide_parameters is False and source.echo is False
    finally:
        source.dispose()


@pytest.mark.parametrize("source_echo", [False, True])
def test_private_view_redacts_real_sql_exception_and_logs_without_mutating_source(
    tmp_path, caplog, source_echo
):
    source = create_engine(
        f"sqlite:///{tmp_path / 'candidate-redaction.sqlite'}",
        hide_parameters=False,
        echo=source_echo,
    )
    try:
        private = _private_engine(source)
        caplog.set_level(logging.INFO, logger="sqlalchemy.engine.Engine")
        value = "synthetic-private-parameter"
        with private.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE redaction_guard (value TEXT UNIQUE)")
            connection.execute(text("INSERT INTO redaction_guard VALUES (:value)"), {"value": value})
        with pytest.raises(IntegrityError) as failure, private.begin() as connection:
            connection.execute(text("INSERT INTO redaction_guard VALUES (:value)"), {"value": value})
        assert "SQL parameters hidden due to hide_parameters=True" in str(failure.value)
        assert value not in str(failure.value)
        assert "SQL parameters hidden due to hide_parameters=True" in caplog.text
        assert value not in caplog.text
        assert private.hide_parameters is True and private.echo is False
        assert source.hide_parameters is False and source.echo is source_echo
    finally:
        source.dispose()


def test_already_private_engine_is_reused_without_pool_initialization():
    source = create_engine("sqlite://", hide_parameters=True, echo=False)
    try:
        listeners = tuple(source.pool.dispatch.connect)
        assert _private_engine(source) is source
        assert activation_port(source).sessions.kw["bind"] is source
        assert tuple(source.pool.dispatch.connect) == listeners
    finally:
        source.dispose()


def test_untrusted_non_engine_is_refused():
    with pytest.raises(GuardUnavailable):
        _private_engine(object())


def test_real_private_engine_view_composes_with_the_pairing_projection(tmp_path):
    source = create_engine(
        f"sqlite:///{tmp_path / 'candidate-pairing-projection.sqlite'}",
        hide_parameters=False,
        echo=False,
    )
    try:
        listeners = tuple(source.pool.dispatch.connect)
        private = _private_engine(source)
        assert type(private) is OptionEngine
        projection = activation_port(private)
        assert type(projection) is PairingProjection
        assert projection.sessions.kw["bind"] is private
        with projection.sessions() as session:
            assert session.scalar(text("SELECT 1")) == 1
        assert tuple(source.pool.dispatch.connect) == listeners
        assert private.pool is source.pool and private.dialect is source.dialect
        assert private.hide_parameters is True and source.hide_parameters is False
    finally:
        source.dispose()


def test_stock_engine_view_without_redaction_cannot_compose_a_pairing_projection():
    source = create_engine("sqlite://", hide_parameters=True, echo=False)
    try:
        view = source.execution_options()
        view.hide_parameters = False
        assert type(view) is OptionEngine and source.hide_parameters is True
        with pytest.raises(GuardUnavailable, match="Private pairing projection is unavailable"):
            activation_port(view)
    finally:
        source.dispose()


@pytest.mark.parametrize("kind", ["engine_subclass", "view_subclass", "duck_type"])
def test_pairing_projection_rejects_custom_engine_types_even_with_redaction(kind):
    class CustomEngine(Engine):
        pass

    class CustomOptionEngine(OptionEngine):
        pass

    source = create_engine("sqlite://", hide_parameters=True, echo=False)
    connections = []
    event.listen(source.pool, "connect", lambda *args: connections.append(True))
    try:
        if kind == "engine_subclass":
            candidate = CustomEngine(source.pool, source.dialect, source.url, hide_parameters=True)
        elif kind == "view_subclass":
            candidate = CustomOptionEngine(source, {})
        else:
            candidate = SimpleNamespace(
                hide_parameters=True, pool=source.pool, dialect=source.dialect, echo=False
            )
        assert candidate.hide_parameters is True
        with pytest.raises(GuardUnavailable, match="Private pairing projection is unavailable"):
            activation_port(candidate)
        assert connections == []
    finally:
        source.dispose()
