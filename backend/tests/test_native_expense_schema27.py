"""Exact plan scope and settlement semantics with inert synthetic records."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from backend.app.models import AdminAuditEvent
from backend.tests.test_native_expense_reconciliation import (
    ACTOR,
    STAMP,
    free_analysis,
    receipt,
    terminal,
)
from backend.tests.test_unified_credits import factory

from scripts import native_expense_schema27 as native
from scripts import notification_schema27_contract as catalog

assert factory
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/reconcile_native_expenses_schema27.py"


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(native, "database_identity", lambda db: "3" * 64)


def _capture(db):
    row, request, run = free_analysis(db)
    bill = native.expense.NativeExpenseReceipt.model_validate(
        receipt(row.id, kind="free_core", work_state="failed").model_dump())
    evidence = native.expense.TerminalWorkEvidence.model_validate(terminal(db, bill).model_dump())
    plan = native.capture(db, receipt=bill, actor=ACTOR, terminal=evidence, nonce="e" * 32, now=STAMP)
    return row, request, run, plan


@pytest.mark.parametrize("version", [None, "native-expense-plan-v1", "native-expense-plan-schema26-v1"])
def test_historical_plan_refused_in_cli_before_opening_database(tmp_path, version):
    tmp_path.chmod(0o700)
    plan = tmp_path / "plan.json"
    raw = json.dumps({"plan": {"version": version}, "source_path": "/private/source",
        "completion_path": "/private/terminal"}).encode()
    plan.write_bytes(raw)
    plan.chmod(0o600)
    env = dict(os.environ, APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://synthetic@127.0.0.1:1/unused?sslmode=verify-full")
    result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "dry-run", "--plan", str(plan),
        "--plan-sha256", hashlib.sha256(raw).hexdigest()], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1 and result.stderr == ""
    assert json.loads(result.stdout)["code"] == "schema27_exact_plan_required"


def test_module_help_and_startup_errors_keep_configuration_private(tmp_path):
    tmp_path.chmod(0o700)
    secret = "synthetic-startup-do-not-echo"
    env = dict(os.environ, APP_ENV="production", DATABASE_URL=f"unknown-driver://{secret}")
    help_run = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--help"], env=env,
        capture_output=True, text=True, timeout=15)
    assert help_run.returncode == 0 and secret not in help_run.stdout + help_run.stderr
    result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "capture", "--plan",
        str(tmp_path / "plan.json")], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1 and result.stderr == ""
    assert json.loads(result.stdout)["code"] == "native_expense_unavailable"
    assert secret not in result.stdout


def test_failed_free_expense_reconciles_once_and_keeps_historical_result(factory, identity):
    with factory() as db:
        row, request, run, plan = _capture(db)
        assert native.dry_run(db, plan=plan, now=STAMP)["status"] == "ready_for_operator_review"
        assert row.settled_minor is None and db.query(AdminAuditEvent).count() == 0
        assert native.apply(db, plan=plan, now=STAMP)["status"] == "native_expense_reconciled"
        db.commit()
        assert native.apply(db, plan=plan, now=STAMP)["status"] == "already_reconciled"
        assert native.dry_run(db, plan=plan, now=STAMP + timedelta(days=2))["status"] == "already_reconciled"
        assert (row.state, row.settled_minor) == ("settled", 9)
        assert (request.state, request.result_id, run.status) == ("admitted", None, "failed")
        audit = db.query(AdminAuditEvent).one()
        assert audit.id == native.expense._audit_id(plan.receipt)
        assert audit.action == native.ACTION
        assert audit.after_state["schema"] == catalog.SCHEMA


@pytest.mark.parametrize("historical_action", [
    "native_expense_reconciled_v1", "native_expense_reconciled_schema26_v1",
])
def test_prior_settlement_cannot_be_relabelled_or_applied_again(factory, identity, historical_action):
    with factory() as db:
        row, _, _, plan = _capture(db)
        row.state, row.settled_minor = "settled", 9
        row.settlement_receipt_sha256 = plan.receipt.source_sha256
        audit_id = native.expense._audit_id(plan.receipt)
        db.add(AdminAuditEvent(id=audit_id, action=historical_action, target_type=plan.receipt.kind,
            target_id=row.id, actor_email=ACTOR.email, reason="Synthetic prior receipt", after_state={"prior": True}))
        db.commit()
        for operation in (native.dry_run, native.apply):
            with pytest.raises(native.expense.ReconciliationDenied, match="receipt_conflict"):
                operation(db, plan=plan, now=STAMP)
            db.rollback()
        with pytest.raises(native.expense.ReconciliationDenied, match="receipt_conflict"):
            native.capture(db, receipt=plan.receipt, terminal=plan.terminal, actor=ACTOR, nonce="f" * 32, now=STAMP)
        assert db.get(AdminAuditEvent, audit_id).action == historical_action
        assert db.query(AdminAuditEvent).count() == 1


def test_foreign_plan_and_copied_malformed_amount_stop_before_any_database_access(factory, identity):
    from pydantic import ValidationError

    from app.domains.credits import reconciliation_schema26 as old

    with factory() as db:
        _, _, _, plan = _capture(db)
    foreign = old.ReconciliationPlan.model_validate(plan.model_dump() | {"version": old.PLAN_VERSION})
    malformed = plan.model_copy(update={"receipt": plan.receipt.model_copy(update={"actual_minor": True})})
    with pytest.raises(native.expense.ReconciliationDenied, match="schema27_exact_plan_required"):
        native.apply(None, plan=foreign, now=STAMP)
    with pytest.raises(ValidationError):
        native.apply(None, plan=malformed, now=STAMP)
