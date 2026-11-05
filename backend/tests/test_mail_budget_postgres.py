"""Owned local PG17 admission races; no provider, live account or native authority.

Opt in with HIREWIZ_MAIL_BUDGET_TEST_ROOT pointing to a private owned directory.
Only a fresh cluster and two canonical ledger-related fixture tables are used.
"""
from __future__ import annotations

import os
import socket
import stat
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, text
from sqlalchemy.orm import sessionmaker

from app.domains.notifications import mail_budget as budget
from app.models import AdminAuditEvent

NOW = datetime(2026, 10, 31, 6, tzinfo=UTC)


@pytest.fixture(scope="module")
def owned_pg_url():
    configured = os.getenv("HIREWIZ_MAIL_BUDGET_TEST_ROOT")
    if not configured:
        pytest.skip("Set HIREWIZ_MAIL_BUDGET_TEST_ROOT for owned local PostgreSQL17 admission tests")
    parent = Path(configured)
    assert parent.is_absolute() and parent.resolve() == parent and parent.is_dir()
    assert parent.stat().st_uid == os.getuid() and stat.S_IMODE(parent.stat().st_mode) == 0o700
    root = parent / ("mail-budget-pg-" + uuid4().hex)
    root.mkdir(mode=0o700)
    tools = Path("/opt/homebrew/opt/postgresql@17/bin")
    assert " 17." in subprocess.check_output([str(tools / "pg_ctl"), "--version"], text=True)
    data = root / "data"
    subprocess.run([str(tools / "initdb"), "-D", str(data), "-U", "mail_budget_test", "-A", "trust",
        "--no-locale", "-E", "UTF8"], check=True, capture_output=True, timeout=30)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    subprocess.run([str(tools / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"),
        "-o", f"-h 127.0.0.1 -p {port} -k '' -c max_connections=12", "-w", "start"],
        check=True, capture_output=True, timeout=30)
    try:
        yield f"postgresql+psycopg://mail_budget_test@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([str(tools / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
            check=True, capture_output=True, timeout=30)


@pytest.fixture
def factory(owned_pg_url):
    schema = "mail_budget_" + uuid4().hex
    admin = create_engine(owned_pg_url)
    with admin.begin() as connection:
        assert connection.execute(text("SHOW server_version_num")).scalar_one().startswith("17")
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(owned_pg_url, connect_args={"application_name": "mail-budget-owned",
        "options": f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=10000 -ctimezone=UTC"})
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    AdminAuditEvent.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    try:
        # READ COMMITTED gives each post-lock ledger SELECT the winner's commit.
        with engine.connect() as connection:
            assert connection.execute(text("SHOW transaction_isolation")).scalar_one() == "read committed"
        yield sessionmaker(engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _fund(factory, count, *, category, per_day):
    with factory() as db:
        for index in range(count):
            budget.reserve_messages(db, [f"seed-{index}"], category=category,
                now=NOW.replace(day=1)+timedelta(days=index//per_day))
        db.commit()


@pytest.mark.parametrize("boundary,count,category,per_day,expected_units", [
    ("optional_day", 19, "lifecycle", 20, 60),
    ("optional_month", 199, "lifecycle", 20, 600),
    ("shared_day_headroom", 22, "password_reset", 30, 69),
    ("shared_month_headroom", 232, "password_reset", 30, 699),
])
def test_postgres_competing_optional_admissions_cannot_overspend_either_ceiling_or_headroom(
        factory, boundary, count, category, per_day, expected_units):
    # Daily cases fund today's row, monthly cases distribute bounded daily work.
    if "month" in boundary:
        _fund(factory, count, category=category, per_day=per_day)
    else:
        with factory() as db:
            for index in range(count):
                budget.reserve_messages(db, [f"seed-{index}"], category=category, now=NOW)
            db.commit()

    arrivals = [Event(), Event()]

    def compete(index):
        with factory() as db:
            arrivals[index].set()
            try:
                budget.reserve_messages(db, [f"contender-{index}"], category="lifecycle", now=NOW)
                db.commit()
                return "admitted"
            except budget.MailBudgetError as error:
                db.rollback()
                return error.mail_outcome

    with ThreadPoolExecutor(max_workers=2) as pool, factory() as blocker:
        budget._lock(blocker)
        jobs = [pool.submit(compete, index) for index in range(2)]
        try:
            assert all(event.wait(2) for event in arrivals)
            deadline = time.monotonic()+3
            while True:
                with factory() as observer:
                    waiters = observer.execute(text("SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name='mail-budget-owned' AND wait_event_type='Lock' "
                        "AND wait_event='advisory'")).scalar_one()
                if waiters == 2:
                    break
                assert time.monotonic() < deadline, "Both contenders must wait on the real advisory lock"
                time.sleep(0.01)
            assert not any(job.done() for job in jobs)
        finally:
            blocker.rollback()
        assert sorted(job.result(timeout=10) for job in jobs) == ["admitted", "deferred"]
    with factory() as db:
        rows = db.query(AdminAuditEvent).filter(AdminAuditEvent.action == budget.RESERVATION).all()
        assert len(rows) == count+1
        assert sum(row.after_state["units"] for row in rows) == expected_units
        assert len([row for row in rows if row.target_id in {
            budget._digest("contender-0"), budget._digest("contender-1")}]) == 1


def test_postgres_pending_rows_count_and_mixed_batch_failure_leaves_no_partial_new_envelope(factory):
    with factory() as db:
        for index in range(19):
            budget.reserve_messages(db, [f"optional-{index}"], category="lifecycle", now=NOW)
        # Simulate one exact v1 reservation added by the current transaction but
        # not yet flushed; autoflush is disabled so the pending path is exercised.
        message = budget._digest("pending-optional")
        budget._record(db, identity=budget._id("reservation", message), action=budget.RESERVATION,
            message=message, value={"version": budget.VERSION, "message_sha256": message,
                "category": "lifecycle", "units": 3, "funding_day": NOW.date().isoformat(),
                "funding_month": NOW.strftime("%Y-%m"), "expires_at": (NOW+timedelta(hours=24)).isoformat()}, now=NOW)
        assert len(db.new) == 1
        with pytest.raises(budget.MailBudgetError) as error:
            budget.reserve_messages(db, ["optional-0", "over-cap-1", "over-cap-2"], category="lifecycle", now=NOW)
        assert error.value.mail_outcome == "deferred" and len(db.new) == 1
        assert budget.reserve_messages(db, ["pending-optional"], category="lifecycle", now=NOW)[0].message_sha256 == message
        db.commit()
    with factory() as db:
        rows = db.query(AdminAuditEvent).filter(AdminAuditEvent.action == budget.RESERVATION).all()
        assert len(rows) == 20 and sum(row.after_state["units"] for row in rows) == 60
        assert not any(row.target_id in {budget._digest("over-cap-1"), budget._digest("over-cap-2")} for row in rows)
