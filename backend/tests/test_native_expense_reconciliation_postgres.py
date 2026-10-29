"""Real locking against an owned disposable local PG cluster, never live bills."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from backend.app.database import Base
from backend.app.domains.credits import admission, reconciliation
from backend.app.domains.credits.models import CreditExecutionHold
from backend.app.domains.credits.privacy import erase_owner_credit_links
from backend.app.domains.free_core.models import FreeCoreRequest, FreeCoreReservation
from backend.app.models import AdminAuditEvent, ModelCostLiability, User
from backend.tests.test_employer_admissions_postgres import pg_engine as ci_pg_engine
from backend.tests.test_native_expense_reconciliation import (
    STAMP,
    free,
    paid,
    plan,
    receipt,
    terminal,
)
from backend.tests.test_unified_credits_postgres import parallel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def pg_engine():
    root = os.getenv("HIREWIZ_NATIVE_EXPENSE_TEST_ROOT")
    if not root:
        yield from ci_pg_engine.__wrapped__()
        return
    directory = Path(root)
    assert directory.is_absolute() and directory.resolve() == directory
    assert directory.name.startswith("hirewiz-native-expense-pg-")
    assert directory.stat().st_uid == os.getuid()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    marker = json.loads((directory / "identity.json").read_text())
    assert Path(marker["data"]).resolve() == directory / "data"
    assert marker["port"] not in {55433, 55434} and 1024 < marker["port"] < 65536
    url = f"postgresql+psycopg://native_expense_test@127.0.0.1:{marker['port']}/native_expense_test"
    admin = create_engine(url)
    schema = "native_expense_" + uuid4().hex
    with admin.begin() as db:
        assert Path(db.execute(text("SHOW data_directory")).scalar_one()).resolve() == directory / "data"
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=4000 -cstatement_timeout=10000"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def factory(pg_engine):
    Base.metadata.create_all(pg_engine)
    maker = sessionmaker(pg_engine, autoflush=False)
    with pg_engine.begin() as db:
        db.execute(text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
        db.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": reconciliation.SCHEMA})
    with maker() as db:
        db.add(User(id=1, email="native-expense@example.invalid", ai_credits=0, job_service_credits=0))
        db.commit()
    return maker


def _run(factory, reviewed):
    with factory() as db:
        try:
            result = reconciliation.apply(db, plan=reviewed, now=STAMP)
            db.commit()
            return result["status"]
        except reconciliation.ReconciliationDenied:
            db.rollback()
            return "conflict"


def test_parallel_identical_receipts_settle_once(factory):
    with factory() as db:
        hold, _ = paid(db)
        reviewed = plan(db, receipt(hold.id))
    assert sorted(parallel(lambda: _run(factory, reviewed), lambda: _run(factory, reviewed))) == [
        "already_reconciled", "native_expense_reconciled"]
    with factory() as db:
        assert db.query(AdminAuditEvent).count() == 1
        assert db.query(CreditExecutionHold).one().native_settled_minor == 9


@pytest.mark.parametrize("amount", [9, 11])
def test_parallel_conflicting_receipts_never_overwrite_first_provenance(factory, amount):
    with factory() as db:
        hold, _ = paid(db)
        first = plan(db, receipt(hold.id))
        second = plan(db, receipt(hold.id, amount=amount, source_sha256="e" * 64), nonce="d" * 32)
    assert sorted(parallel(lambda: _run(factory, first), lambda: _run(factory, second))) == [
        "conflict", "native_expense_reconciled"]
    with factory() as db:
        audit = db.query(AdminAuditEvent).one()
        hold = db.query(CreditExecutionHold).one()
        assert audit.after_state["actual_minor"] == hold.native_settled_minor
        assert hold.native_settled_minor in {9, 11}


def test_native_cost_and_receipt_rollback_together_in_postgres(factory):
    with factory() as db:
        hold, _ = paid(db)
        reviewed = plan(db, receipt(hold.id))
        identity = hold.id
        reconciliation.apply(db, plan=reviewed, now=STAMP)
        db.rollback()
    with factory() as db:
        assert db.get(CreditExecutionHold, identity).native_settled_minor is None
        assert db.query(AdminAuditEvent).count() == 0


@pytest.mark.parametrize("kind", ["paid_native", "free_core"])
def test_erasure_racing_native_reconciliation_preserves_exact_cost(factory, kind):
    with factory() as db:
        row = paid(db)[0] if kind == "paid_native" else free(db)
        reviewed = plan(db, receipt(row.id, kind=kind))

    def erase():
        with factory() as db:
            erase_owner_credit_links(db, user_id=1)
            db.delete(db.get(User, 1))
            db.commit()
        return "erased"

    assert sorted(parallel(lambda: _run(factory, reviewed), erase)) == [
        "erased", "native_expense_reconciled"]
    with factory() as db:
        assert db.get(User, 1) is None and db.query(AdminAuditEvent).count() == 1
        row = db.query(CreditExecutionHold if kind == "paid_native" else FreeCoreReservation).one()
        assert row.user_id is None
        assert (row.native_settled_minor if kind == "paid_native" else row.settled_minor) == 9
        assert db.query(FreeCoreRequest).count() == 0


def test_detached_unknown_provider_liability_stays_funded_after_native_receipt(factory):
    with factory() as db:
        hold, binding = paid(db, native=False)
        reviewed = plan(db, receipt(hold.id, work_state="failed"))
        erase_owner_credit_links(db, user_id=1)
        db.delete(db.get(User, 1))
        db.commit()
    assert _run(factory, reviewed) == "native_expense_reconciled"
    with factory() as db:
        hold = db.query(CreditExecutionHold).one()
        assert hold.state == "outcome_unknown"
        assert db.get(ModelCostLiability, binding.liability_id).cost_state == "outcome_unknown"
        assert admission._hold_usage(db, start=STAMP.replace(year=2027)) == hold.reserved_minor


def test_cli_private_artifact_custody_and_explicit_apply(factory, tmp_path):
    tmp_path.chmod(0o700)
    with factory() as db:
        hold, _ = paid(db)
        hold_id = hold.id
        terminal_record = terminal(db, receipt(hold_id)).model_dump(mode="json")
        engine = db.get_bind()
        schema = db.execute(text("SELECT current_schema()")).scalar_one()
    paths = [tmp_path / name for name in ("usage.json", "terminal.json", "review.json", "plan.json")]
    usage, terminal_file, review, output = paths
    usage.write_text('{"synthetic_only":true,"native_minor":9}')
    terminal_file.write_text(json.dumps(terminal_record))
    usage.chmod(0o600)
    terminal_file.chmod(0o600)
    native_receipt = receipt(hold_id, source_sha256=hashlib.sha256(usage.read_bytes()).hexdigest(),
                            completion_sha256=hashlib.sha256(terminal_file.read_bytes()).hexdigest())
    review.write_text(json.dumps({"receipt": native_receipt.model_dump(mode="json"),
        "operator_email": "operator@example.invalid", "operator_native_identity_sha256": "f" * 64,
        "source_path": str(usage), "completion_path": str(terminal_file)}))
    review.chmod(0o600)
    url = engine.url.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = dict(os.environ, DATABASE_URL=url.render_as_string(hide_password=False), APP_ENV="test")
    script = Path(__file__).resolve().parents[1] / "scripts/reconcile_native_expenses.py"

    def run(*arguments):
        result = subprocess.run([sys.executable, str(script), *arguments, "--plan", str(output)],
                                env=environment, capture_output=True, text=True, timeout=20)
        return result.returncode, json.loads(result.stdout)

    # Matching hashes alone cannot turn a still-running execution into evidence.
    original_review = review.read_text()
    original_terminal = terminal_file.read_text()
    terminal_file.write_text(json.dumps(terminal_record | {"all_native_attempts_terminal": False}))
    false_review = json.loads(original_review)
    false_review["receipt"]["completion_sha256"] = hashlib.sha256(terminal_file.read_bytes()).hexdigest()
    review.write_text(json.dumps(false_review))
    assert run("capture", "--receipt-review", str(review))[0] == 1
    assert not output.exists()
    terminal_file.write_text(original_terminal)
    review.write_text(original_review)
    code, result = run("capture", "--receipt-review", str(review))
    assert code == 0 and result["database_mutations"] == 0
    sha = result["plan_sha256"]
    assert run("dry-run", "--plan-sha256", sha)[1]["status"] == "ready_for_operator_review"
    assert run("apply", "--plan-sha256", sha)[0] == 1
    usage.chmod(0o644)
    assert run("apply", "--plan-sha256", sha, "--accepted-reviewed-native-expense")[0] == 1
    usage.chmod(0o600)
    usage.write_text("Changed synthetic artifact")
    assert run("apply", "--plan-sha256", sha, "--accepted-reviewed-native-expense")[0] == 1
    with factory() as db:
        assert db.query(AdminAuditEvent).count() == 0
        assert db.query(CreditExecutionHold).one().native_settled_minor is None
    usage.write_text('{"synthetic_only":true,"native_minor":9}')
    assert run("apply", "--plan-sha256", sha, "--accepted-reviewed-native-expense")[1]["status"] == "native_expense_reconciled"
    assert run("dry-run", "--plan-sha256", sha)[1]["status"] == "already_reconciled"
