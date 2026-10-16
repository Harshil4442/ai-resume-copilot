"""Real PostgreSQL host authority; only the explicit disposable loopback fixture."""
from __future__ import annotations

import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from backend.app.domains.employer import host_pacing
from backend.app.domains.employer import postgres_host_pacing as pg
from backend.app.domains.employer.models import EmployerHostPacing
from backend.tests.test_employer_admissions_postgres import _migrate
from backend.tests.test_employer_admissions_postgres import pg_engine as pg_engine
from sqlalchemy import create_engine, delete, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

HOST = 'api.lever.co'
OTHER = 'api.eu.lever.co'


@pytest.fixture
def gate(pg_engine):
    EmployerHostPacing.__table__.create(pg_engine)
    return pg.PostgresCoordinator(pg_engine, lease_seconds=.3, request_seconds=.03,
                                  close_seconds=.01, cooldown_seconds=.03,
                                  recovery_seconds=.32, wait_seconds=3)


def acquire(gate):
    return gate.acquire(HOST, time.monotonic() + 3)


def record(gate):
    with gate.engine.connect() as connection:
        return connection.execute(select(EmployerHostPacing.__table__).where(
            EmployerHostPacing.host == HOST)).mappings().one()


def change(gate, **values):
    with gate.engine.begin() as connection:
        connection.execute(update(EmployerHostPacing).where(
            EmployerHostPacing.host == HOST).values(**values))


def test_production_bounds_and_nonpostgres_refusal(pg_engine):
    gate = pg.PostgresCoordinator(pg_engine)
    assert gate.lease == 30 and gate.recovery == 32 and gate.wait > 32
    sqlite = create_engine('sqlite://')
    try:
        with pytest.raises(host_pacing.PacingError, match='configuration_invalid'):
            pg.PostgresCoordinator(sqlite)
    finally:
        sqlite.dispose()


def test_actual_cold_state_quarantine_and_normal_cooldown(gate):
    started = time.monotonic()
    first = acquire(gate)
    assert time.monotonic() - started >= .32
    gate.verify(first)
    gate.release(first)
    started = time.monotonic()
    second = acquire(gate)
    assert time.monotonic() - started >= .03
    assert second.token != first.token and second.authority == first.authority
    gate.release(second)


def test_actual_independent_workers_serialize_shared_host(gate):
    gate.release(acquire(gate))
    barrier = Barrier(3)

    def worker(_):
        independent = pg.PostgresCoordinator(gate.engine, lease_seconds=.3, request_seconds=.03,
                                             close_seconds=.01, cooldown_seconds=.03,
                                             recovery_seconds=.32, wait_seconds=3)
        barrier.wait(timeout=2)
        permit = acquire(independent)
        independent.verify(permit)
        started = time.monotonic()
        time.sleep(.025)
        ended = time.monotonic()
        independent.release(permit)
        return started, ended

    with ThreadPoolExecutor(max_workers=3) as pool:
        intervals = sorted(pool.map(worker, range(3)))
    assert all(after[0] - before[1] >= .03
               for before, after in zip(intervals, intervals[1:], strict=False))


def test_other_host_does_not_wait_for_held_host(gate):
    first = acquire(gate)
    second = gate.acquire(OTHER, time.monotonic() + 3)
    # The other host's cold quarantine has elapsed, so first has expired; its
    # ownership did not prevent the other host from receiving its own permit.
    gate.verify(second)
    with gate.engine.connect() as connection:
        assert set(connection.execute(select(EmployerHostPacing.host)).scalars()) == {HOST, OTHER}
    gate.release(second)
    assert first.token != second.token


def _process_worker(url, schema, output):
    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    try:
        gate = pg.PostgresCoordinator(engine, lease_seconds=3, request_seconds=.1,
                                      close_seconds=.1, cooldown_seconds=1,
                                      recovery_seconds=3.2, wait_seconds=8)
        permit = gate.acquire(HOST, time.monotonic() + 8)
        gate.verify(permit)
        started = time.monotonic()
        time.sleep(.025)
        ended = time.monotonic()
        gate.release(permit)
        output.put((started, ended))
    finally:
        engine.dispose()


def test_actual_spawned_processes_share_production_one_second_cooldown(gate):
    gate.release(acquire(gate))
    with gate.engine.connect() as connection:
        schema = connection.execute(text('SELECT current_schema()')).scalar_one()
    context = multiprocessing.get_context('spawn')
    output = context.Queue()
    workers = [context.Process(target=_process_worker, args=(
        gate.engine.url.render_as_string(hide_password=False), schema, output)) for _ in range(3)]
    try:
        for worker in workers:
            worker.start()
        intervals = sorted(output.get(timeout=12) for _ in workers)
        for worker in workers:
            worker.join(timeout=3)
            assert worker.exitcode == 0
        assert all(after[0] - before[1] >= 1
                   for before, after in zip(intervals, intervals[1:], strict=False))
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=3)
        output.close()
        output.join_thread()


@pytest.mark.parametrize('failure', ['deleted', 'server_changed', 'expired'])
def test_actual_state_loss_restart_and_expiry_reject_old_permit(gate, failure):
    permit = acquire(gate)
    if failure == 'deleted':
        with gate.engine.begin() as connection:
            connection.execute(delete(EmployerHostPacing).where(EmployerHostPacing.host == HOST))
    elif failure == 'server_changed':
        change(gate, server_started_at=permit.server_started_at - timedelta(seconds=1))
    else:
        change(gate, lease_until=record(gate)['last_observed_db_time'] - timedelta(seconds=1))
    with pytest.raises(host_pacing.PacingError, match='ownership_lost'):
        gate.verify(permit)
    started = time.monotonic()
    replacement = acquire(gate)
    assert time.monotonic() - started >= .32
    assert replacement.authority != permit.authority
    with pytest.raises(host_pacing.PacingError, match='ownership_lost'):
        gate.release(permit)
    gate.verify(replacement)
    gate.release(replacement)


def test_db_clock_rollback_persists_invalidation_and_never_grants(gate):
    permit = acquire(gate)
    future = record(gate)['last_observed_db_time'] + timedelta(seconds=5)
    change(gate, last_observed_db_time=future)
    with pytest.raises(host_pacing.PacingError, match='ownership_lost'):
        gate.verify(permit)
    row = record(gate)
    assert row['owner_token'] is None and row['recovery_required'] is True
    assert row['last_observed_db_time'] == future and row['authority'] != permit.authority
    with pytest.raises(host_pacing.PacingError, match='clock_rollback'):
        acquire(gate)


def test_delayed_verify_response_cannot_authorize_dispatch(gate, monkeypatch):
    permit = acquire(gate)
    step = gate._step

    def delayed(*args, **kwargs):
        result = step(*args, **kwargs)
        if args[1] == 'verify':
            time.sleep(.24)
        return result

    monkeypatch.setattr(gate, '_step', delayed)
    with pytest.raises(host_pacing.PacingError, match='ownership_lost'):
        gate.verify(permit)


def test_monotonic_expiry_refuses_before_sql_even_with_live_db_lease(gate, monkeypatch):
    permit = acquire(gate)
    expired = replace(permit, latest_dispatch=time.monotonic() - 1)
    monkeypatch.setattr(gate, '_step', lambda *a, **k: pytest.fail('expired permit queried SQL'))
    with pytest.raises(host_pacing.PacingError, match='ownership_lost'):
        gate.verify(expired)


def test_expired_waiting_claim_requires_full_new_recovery(gate):
    token = uuid4().hex
    assert gate._step(HOST, 'claim', token)[0] == 'waiting'
    change(gate, pending_until=record(gate)['last_observed_db_time'] - timedelta(seconds=1))
    started = time.monotonic()
    permit = acquire(gate)
    assert time.monotonic() - started >= .32 and permit.token != token
    gate.release(permit)


def test_full_cold_wait_never_shortened_for_caller_deadline(gate):
    started = time.monotonic()
    with pytest.raises(host_pacing.PacingError, match='wait_timeout'):
        gate.acquire(HOST, started + .02)
    assert time.monotonic() - started < .2
    assert record(gate)['phase'] == 'waiting'


def test_lock_timeout_is_bounded_without_fallback(gate):
    gate.release(acquire(gate))
    with gate.engine.begin() as connection:
        connection.execute(select(EmployerHostPacing.host).where(
            EmployerHostPacing.host == HOST).with_for_update())
        started = time.monotonic()
        with pytest.raises(host_pacing.PacingError, match='unavailable'):
            acquire(gate)
        assert time.monotonic() - started < .8


def test_no_connection_is_held_by_returned_permit(gate):
    permit = acquire(gate)
    gate.verify(permit)
    assert gate.engine.pool.checkedout() == 0
    gate.release(permit)
    gate.close()
    assert gate.engine.pool.checkedout() == 0


def test_wrong_host_and_foreign_permit_never_dispatch(gate):
    with pytest.raises(host_pacing.PacingError, match='invalid_host'):
        gate.acquire('jobs.lever.co', time.monotonic() + 1)
    with pytest.raises(host_pacing.PacingError, match='ownership_lost'):
        gate.verify(host_pacing.Permit(HOST, uuid4().hex))


def test_migration18_roundtrip_and_populated_downgrade_refusal(pg_engine):
    _migrate(pg_engine, '20261015_0018')
    columns = {row['name']: row for row in inspect(pg_engine).get_columns('employer_host_pacing')}
    assert set(columns) == {column.name for column in EmployerHostPacing.__table__.columns}
    gate = pg.PostgresCoordinator(pg_engine, lease_seconds=.3, request_seconds=.03,
                                  close_seconds=.01, cooldown_seconds=.03,
                                  recovery_seconds=.32, wait_seconds=3)
    gate.release(acquire(gate))
    with pytest.raises(RuntimeError, match='populated'):
        _migrate(pg_engine, '20261014_0017', 'downgrade')
    with pg_engine.begin() as connection:
        connection.execute(delete(EmployerHostPacing))
    _migrate(pg_engine, '20261014_0017', 'downgrade')
    assert 'employer_host_pacing' not in inspect(pg_engine).get_table_names()
    _migrate(pg_engine, '20261015_0018')


def test_table_rejects_unbounded_host_and_invalid_state(gate):
    with pytest.raises(IntegrityError), gate.engine.begin() as connection:
        connection.execute(text("""INSERT INTO employer_host_pacing
            (host,authority,server_started_at,phase,last_observed_db_time,recovery_required)
            VALUES ('unreviewed.example', :token, pg_postmaster_start_time(), 'idle', clock_timestamp(),true)"""),
                           {'token': uuid4().hex})
