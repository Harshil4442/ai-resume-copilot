"""Opt-in only on a new owned PG17 cluster; all users/provider output are synthetic.

Set HIREWIZ_CANARY_TEST_ROOT to a private empty directory owned by the runner.
No existing service, listener, schema, invoice or customer consent is reused.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from backend.app import models
from backend.app.database import Base
from backend.app.domains.credits import canary, finance, service
from backend.app.domains.credits.models import CreditExecutionHold, CreditReservation
from backend.tests.test_canary_admission import accept, baseline, consent, prepare, source
from backend.tests.test_unified_credits import NOW
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.test_development_runner import calibration as calibration


@pytest.fixture(scope="module")
def owned_cluster():
    configured = os.getenv("HIREWIZ_CANARY_TEST_ROOT")
    if not configured:
        pytest.skip("Set HIREWIZ_CANARY_TEST_ROOT for an isolated owned PostgreSQL17 test cluster")
    parent = Path(configured)
    assert parent.is_absolute() and parent.resolve() == parent and parent.is_dir()
    assert parent.stat().st_uid == os.getuid() and stat.S_IMODE(parent.stat().st_mode) == 0o700
    root = parent / ("canary-pg-" + uuid4().hex)
    root.mkdir(mode=0o700)
    tools = Path("/opt/homebrew/opt/postgresql@17/bin")
    assert tools.is_dir()
    version = subprocess.check_output([str(tools / "pg_ctl"), "--version"], text=True)
    assert " 17." in version
    data = root / "data"
    subprocess.run([str(tools / "initdb"), "-D", str(data), "-U", "hirewiz", "-A", "trust",
        "--no-locale", "-E", "UTF8"], check=True, capture_output=True, timeout=30)
    with socket.socket() as listener:
        # Prefer an existing runner-approved local port, without borrowing a
        # listener owned by another test. Otherwise ordinary canary tests use a
        # fresh OS-assigned port and only the separate runner case skips.
        for candidate in (55434, 55433, 0):
            try:
                listener.bind(("127.0.0.1", candidate))
                break
            except OSError:
                if candidate == 0:
                    raise
        port = listener.getsockname()[1]
    subprocess.run([str(tools / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"),
        "-o", f"-h 127.0.0.1 -p {port} -k '' -c max_connections=16", "-w", "start"],
        check=True, capture_output=True, timeout=30)
    base_url = f"postgresql+psycopg://hirewiz@127.0.0.1:{port}/"
    try:
        admin = create_engine(base_url + "postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text("CREATE DATABASE hirewiz_admission_test"))
        admin.dispose()
        yield root, base_url + "hirewiz_admission_test"
    finally:
        subprocess.run([str(tools / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
            check=True, capture_output=True, timeout=30)


@pytest.fixture
def factory(request):
    # CI reuses the established exact local owned database guard and a new
    # per-test schema. Local operator tests can instead start the private PG17
    # cluster above; an existing public schema is never reset by the CI path.
    from backend.tests.test_employer_admissions_postgres import pg_engine
    if os.getenv("HIREWIZ_CANARY_TEST_ROOT"):
        _, url = request.getfixturevalue("owned_cluster")
        engine = create_engine(url, connect_args={"options": "-clock_timeout=5000 -cstatement_timeout=10000"})
        with engine.begin() as connection:
            assert connection.execute(text("SHOW server_version_num")).scalar_one().startswith("17")
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engines = iter((engine,))
    else:
        engines = pg_engine.__wrapped__()
    try:
        for engine in engines:
            Base.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
                connection.execute(text("INSERT INTO alembic_version VALUES ('20261028_0025')"))
            maker = sessionmaker(engine, autoflush=False)
            with maker() as db:
                db.add_all([models.User(id=1, email="canary-fixture-1@example.com", ai_credits=17),
                    models.User(id=2, email="canary-fixture-2@example.com", ai_credits=17)])
                db.commit()
            try:
                yield maker
            finally:
                engine.dispose()
    finally:
        close = getattr(engines, "close", None)
        if close is not None:
            close()


def test_two_owners_racing_for_one_literal_traffic_slot_accept_only_one(factory, monkeypatch):
    with factory() as db:
        config, review, digest = prepare(db, monkeypatch)
        consent(db, config, review, digest, expected_sequence=0, owner=1)
        consent(db, config, review, digest, expected_sequence=0, owner=2)
        baseline(db)
        db.commit()
    ready = Barrier(2)
    def action(owner):
        ready.wait(timeout=10)
        with factory() as db:
            try:
                row = source(db, identity=f"racing-candidate-{owner}", owner=owner)
                proof = accept(db, config, row)
                db.commit()
                return proof
            except finance.FundingUnavailable:
                db.rollback()
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(action, (1, 2)))
    assert sum(value is not None for value in results) == 1
    with factory() as db:
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).count() == 1
        assert db.query(models.AnalysisRun).count() == 20


def test_same_owner_consent_race_forms_one_valid_monotonic_chain(factory, monkeypatch):
    with factory() as db:
        config, review, digest = prepare(db, monkeypatch)
        db.commit()
    ready = Barrier(2)
    def action(choice):
        ready.wait(timeout=10)
        with factory() as db:
            try:
                value = consent(db, config, review, digest, expected_sequence=0, choice=choice, key="racing-" + choice)
                db.commit()
                return value
            except finance.FundingUnavailable:
                db.rollback()
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(action, ("opt_in", "revoke")))
    assert [item["sequence"] for item in results if item is not None] == [1]
    with factory() as db:
        values = canary._consents(db, review, digest, 1)
        assert len(values) == 1
        assert canary.enrollment(db, user_id=1, runtime=config, now=NOW)["status"] == (
            "OPTED_IN" if values[-1]["choice"] == "opt_in" else "NOT_OPTED_IN")


@pytest.mark.parametrize("invalid_output", [False, True])
def test_actual_canary_endpoint_settlement_refund_and_saved_reads_survive_revocation(factory, monkeypatch, invalid_output):
    from backend.tests import test_unified_synchronous as checks
    checks.setup.__wrapped__(factory, monkeypatch)
    config = checks.configure(monkeypatch, "enhanced_qa")
    with factory() as db:
        _, review, digest = prepare(db, monkeypatch, config=config)
        consent(db, config, review, digest, expected_sequence=0)
        baseline(db)
        db.commit()
    def revoke_after_request_started():
        with factory() as db:
            consent(db, config, review, digest, expected_sequence=1, choice="revoke", key="revoke-after-provider-start")
            db.commit()
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    calls = checks.provider(monkeypatch, "enhanced_qa", raw={} if invalid_output else None,
        callback=revoke_after_request_started)
    from fastapi import HTTPException
    with factory() as db:
        request = checks.payload("enhanced_qa")
        quote_id = checks.quote(db, request)
        if invalid_output:
            with pytest.raises(HTTPException):
                checks.invoke(db, request, quote_id)
        else:
            result = checks.invoke(db, request, quote_id)
            assert result["credit_execution"]["charged_this_request"] == 2
        assert len(calls) == 1
        hold = db.query(CreditExecutionHold).one()
        assert hold.snapshot["canary"]["canary_acceptances"] == 1
        assert db.query(CreditReservation).one().committed_credits == (0 if invalid_output else 2)
        assert service.wallet(db, user_id=1).available_credits == (40 if invalid_output else 38)
        assert db.query(models.ModelCostLiability).one().settled_cost_micros > 0
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).count() == 1
        if invalid_output:
            with pytest.raises(HTTPException):
                checks.invoke(db, request, quote_id)
        else:
            assert checks.invoke(db, request, quote_id)["credit_execution"]["reused"]
        assert len(calls) == 1
        assert db.get(models.User, 1).ai_credits == 17


def test_cli_capture_approve_observe_exact_native_database_and_private_review_artifact(factory, monkeypatch, owned_cluster):
    root, url = owned_cluster
    with factory() as db:
        config, _, _ = prepare(db, monkeypatch)
        db.commit()
    folder = root / "operator-review"
    folder.mkdir(mode=0o700)
    evidence = folder / "review.txt"
    evidence.write_text("Synthetic local accounts/exclusions reviewed for this isolated test only.\n")
    evidence.chmod(0o600)
    current = datetime.now(UTC)
    spec = {"id": "fixture-cli-canary-v1", "notice": "Voluntary synthetic canary participation; inspect each quote and revoke new work at any time.",
        "reviewed_by": "fixture-operator@example.com", "operator_native_identity_sha256": "b" * 64,
        "review_evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "real_business_accounts_and_exclusions_reviewed": True,
        "expires_at": (current + timedelta(days=1)).isoformat(),
        "traffic_started_at": (current - timedelta(days=3)).isoformat(),
        "selected_owner_ids": [1, 2], "excluded_owner_ids": [], "families": sorted(canary.FAMILIES)}
    spec_path, plan = folder / "spec.json", folder / "plan.json"
    spec_path.write_text(json.dumps(spec))
    spec_path.chmod(0o600)
    env = os.environ | {"APP_ENV": "test", "DATABASE_URL": url,
        "UNIFIED_CREDIT_RUNTIME_REVIEW_JSON": config.model_dump_json()}
    def run(phase, *extra):
        process = subprocess.run([sys.executable, "scripts/review_canary_audience.py", phase,
            "--plan", str(plan), "--review-evidence", str(evidence), *extra],
            env=env, capture_output=True, text=True, timeout=20)
        return process.returncode, json.loads(process.stdout)
    code, captured = run("capture", "--spec", str(spec_path))
    assert code == 0, captured
    assert (captured["real_census_count"], captured["selected_count"]) == (40, 2)
    digest = captured["plan_sha256"]
    with factory() as db:
        assert db.query(models.AdminAuditEvent).count() == 1  # original synthetic review only
    code, approved = run("approve", "--plan-sha256", digest, "--accept-reviewed-audience")
    assert code == 0, approved
    assert approved["consent_granted"] is False and approved["credits_granted"] == 0
    assert run("observe", "--plan-sha256", digest)[0] == 0
    assert run("approve", "--plan-sha256", digest, "--accept-reviewed-audience")[0] == 0
    env.pop("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON")
    assert run("observe", "--plan-sha256", digest)[0] == 0  # Pausing cannot strand commit readback.
    evidence.write_text("changed artifact\n")
    assert run("observe", "--plan-sha256", digest)[0] == 2
    with factory() as db:
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.REVIEW_ACTION).count() == 2
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.CONSENT_ACTION).count() == 0
        assert db.query(CreditReservation).count() == 0


def test_locked_census_identity_cannot_leave_stale_acceptance_or_partial_wallet_state(factory, monkeypatch):
    from backend.tests import test_unified_synchronous as checks
    from fastapi import HTTPException

    checks.setup.__wrapped__(factory, monkeypatch)
    config = checks.configure(monkeypatch, "enhanced_qa")
    with factory() as db:
        _, review, digest = prepare(db, monkeypatch, config=config)
        consent(db, config, review, digest, expected_sequence=0)
        baseline(db)
        db.commit()
        request = checks.payload("enhanced_qa")
        quote_id = checks.quote(db, request)
    # Simulate an ordinary concurrent identity change. Acceptance must refuse
    # immediately instead of waiting under the global financial locks.
    with factory() as updater:
        owner = updater.query(models.User).filter_by(id=40).with_for_update().one()
        owner.privacy_version = "concurrent-change"
        updater.flush()
        with factory() as consumer:
            with pytest.raises(HTTPException) as caught:
                checks.invoke(consumer, request, quote_id)
            assert caught.value.detail["run_created"] is False
            consumer.rollback()
        updater.commit()
    with factory() as db:
        assert db.query(CreditReservation).count() == db.query(CreditExecutionHold).count() == 0
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).count() == 0
        assert db.query(models.ModelCallEvent).count() == db.query(models.ModelCostLiability).count() == 0
        assert service.wallet(db, user_id=1).available_credits == 40
        with pytest.raises(HTTPException):
            checks.invoke(db, request, quote_id)
        assert db.query(CreditReservation).count() == 0


def test_exact_validated_local_internal_runner_remains_separate_from_public_canary(owned_cluster, calibration, monkeypatch):
    from sqlalchemy.engine import make_url

    from app.domains.analysis import development_runner as runner
    from app.domains.credits import canary as app_canary
    from app.domains.credits.finance import FundingUnavailable
    from tests.test_development_runner_postgres import provider

    _, url = owned_cluster
    if make_url(url).port not in {55433, 55434}:
        pytest.skip("Both existing runner-approved local ports are busy; no external listener is borrowed")
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary")
    local = runner.local_factory(url, calibration["authorization"])
    try:
        runner.prepare(local, **calibration)
        calls = provider(monkeypatch)
        outcome = runner.execute(local, **calibration, side="candidate", maximum_operations=1)
        assert list(outcome["outcomes"].values()) == ["succeeded"] and len(calls) == 1
        from app import models as app_models
        with local() as db:
            run = db.query(app_models.AnalysisRun).filter_by(status="succeeded").one()
            with pytest.raises(FundingUnavailable, match="validated local runner"):
                app_canary.require_accepted_provider_run(db, run)  # run label after scope exit is insufficient
            assert db.query(app_models.User).one().ai_credits == 0
            assert db.query(app_models.ModelCostLiability).one().settled_cost_micros > 0
    finally:
        with local.kw["bind"].begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{runner._schema(calibration["authorization"])}" CASCADE'))
        local.kw["bind"].dispose()



def test_traffic_numerator_is_utc_stable_across_postgres_session_timezones(factory, monkeypatch):
    with factory() as db:
        db.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
        config, review, digest = prepare(db, monkeypatch)
        consent(db, config, review, digest, expected_sequence=0)
        baseline(db)
        proof = accept(db, config, source(db))
        db.commit()
        saved = db.get(models.AdminAuditEvent, proof["acceptance_id"])
        assert saved.created_at == NOW.replace(tzinfo=None)
    with factory() as db:
        db.execute(text("SET LOCAL TIME ZONE 'America/Los_Angeles'"))
        candidate = source(db, identity="timezone-second-candidate")
        with pytest.raises(finance.FundingUnavailable):
            accept(db, config, candidate)
        db.rollback()
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).count() == 1



@pytest.mark.parametrize("delayed,newer", [("opt_in", "revoke"), ("revoke", "opt_in")])
def test_delayed_choice_cannot_overwrite_newer_committed_owner_revision(factory, monkeypatch, delayed, newer):
    with factory() as db:
        config, review, digest = prepare(db, monkeypatch)
        db.commit()
    observed, deliver = Event(), Event()
    def delayed_request():
        with factory() as db:
            seen = canary.enrollment(db, user_id=1, runtime=config, now=NOW)
            assert seen["sequence"] == 0 and seen["status"] == "NOT_OPTED_IN"
        observed.set()
        assert deliver.wait(10)
        with factory() as db:
            with pytest.raises(finance.FundingUnavailable):
                consent(db, config, review, digest, expected_sequence=seen["sequence"],
                    choice=delayed, key="delayed-original-choice")
            db.rollback()
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(delayed_request)
        assert observed.wait(10)
        with factory() as db:
            latest = consent(db, config, review, digest, expected_sequence=0, choice=newer, key="newer-owner-choice")
            assert latest["sequence"] == 1
            db.commit()
        deliver.set()
        old.result(timeout=10)
    with factory() as db:
        actual = canary.enrollment(db, user_id=1, runtime=config, now=NOW)
        assert actual["sequence"] == 1
        assert actual["status"] == ("NOT_OPTED_IN" if newer == "revoke" else "OPTED_IN")
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.CONSENT_ACTION).count() == 1
        assert db.query(models.ModelCallEvent).count() == db.query(models.ModelCostLiability).count() == 0
        assert db.query(CreditReservation).count() == 0


def test_identical_concurrent_choice_replay_keeps_receipt_after_later_revocation(factory, monkeypatch):
    with factory() as db:
        config, review, digest = prepare(db, monkeypatch)
        db.commit()
    ready = Barrier(2)
    def same_request(_):
        ready.wait(timeout=10)
        with factory() as db:
            value = consent(db, config, review, digest, expected_sequence=0, key="identical-owner-intent")
            db.commit()
            return value
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(same_request, (1, 2)))
    assert results[0] == results[1] and results[0]["sequence"] == 1
    with factory() as db:
        consent(db, config, review, digest, expected_sequence=1, choice="revoke", key="later-explicit-stop")
        db.commit()
        assert consent(db, config, review, digest, expected_sequence=0, key="identical-owner-intent") == results[0]
        assert canary.enrollment(db, user_id=1, runtime=config, now=NOW)["sequence"] == 2
        assert canary.enrollment(db, user_id=1, runtime=config, now=NOW)["status"] == "NOT_OPTED_IN"
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.CONSENT_ACTION).count() == 2
