"""Pure synthetic ports: no engine connections, SQLite, PostgreSQL or network.

Actual receipt/preimage/terminal/census/audit checks execute against finite ports.
Settlement port replacements below prove delegation/order, not native accounting
or transaction acceptance. Existing native accounting remains a separate gate.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url
from test_evidence_writer_adapters import Port as FencePort
from test_evidence_writer_adapters import Result as FenceResult

from app.domains.career.evidence_write_fence import EvidenceWriteFenceDenied
from app.domains.common import payload_fingerprint
from app.domains.credits import canary
from app.domains.credits import reconciliation as old
from app.domains.credits import reconciliation_schema26 as native
from app.domains.employer.testing_promotion import Operator
from app.domains.free_core.models import FreeCoreRequest
from app.models import AdminAuditEvent
from scripts import reconcile_native_expenses_schema26 as expense_cli
from scripts import review_canary_audience_schema26 as audience_cli

NOW = datetime(2026, 10, 31, 6, tzinfo=UTC)
STAMP = NOW + timedelta(minutes=3)
ACTOR = Operator("synthetic-operator@example.invalid", "f" * 64)


class Rows(FenceResult):
    def one(self):
        return self.value

    def one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self.value)


class Query:
    def __init__(self, db, *, census=False, request=False):
        self.db, self.census, self.request = db, census, request

    def filter_by(self, **values):
        return self

    def filter(self, *values):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        self.db.trace.append("target_lock")
        return self

    def one_or_none(self):
        return SimpleNamespace(state="completed") if self.request else self.db.target

    def order_by(self, *values):
        return self

    def limit(self, value):
        return self

    def all(self):
        assert self.census
        return self.db.members


class Port(FencePort):
    def __init__(self, kind="paid_native"):
        super().__init__()
        self.url = make_url("postgresql+psycopg://synthetic_operator@native.example.invalid/synthetic?sslmode=verify-full&channel_binding=require")
        self.version = native.SCHEMA
        self.tls = (True, "TLSv1.3", 256)
        self.control = [(3441, 10, False, "pg_control_system", "internal")]
        self.identity = ("synthetic", 12, "owned_schema", 42, 43, "synthetic_operator", "synthetic_operator", "170006", "synthetic-cluster-identity")
        self.target = SimpleNamespace(id="synthetic-expense", route_id="synthetic-route", created_at=NOW,
            reserved_minor=20, native_reserved_minor=12, financial_group_id="synthetic-group",
            reservation_id="synthetic-reservation", snapshot={"synthetic": True}, funding_kind="prepaid",
            delivery_state="delivered", native_settled_minor=None, settled_minor=None, user_id=1,
            state="reserved", policy_version="synthetic-policy", funding_period_id="synthetic-period",
            operation="resume_parse", settlement_receipt_sha256=None)
        self.kind, self.audits, self.trace, self.settlements = kind, {}, [], 0
        self.customer_balance, self.unknown_provider_reserve = 17, 12345
        self.members = []

    def execute(self, statement, params=None):
        sql = str(statement)
        if "pg_stat_ssl" in sql:
            self.calls.append((sql, params))
            return Rows(self.tls)
        if "p.proname='pg_control_system'" in sql:
            self.calls.append((sql, params))
            return Rows(self.control)
        if "FROM pg_catalog.pg_database d" in sql:
            self.calls.append((sql, params))
            return Rows(self.identity)
        if "SELECT version_num" in sql:
            self.calls.append((sql, params))
            return Rows([self.version])
        if "pg_advisory_xact_lock" in sql:
            self.trace.append(sql)
            return Rows(None)
        return super().execute(statement, params)

    def query(self, *models):
        return Query(self, census=len(models) > 1, request=len(models) == 1 and models[0] is FreeCoreRequest)

    def get(self, model, identity):
        assert model is AdminAuditEvent
        return self.audits.get(identity)

    def add(self, row):
        assert isinstance(row, AdminAuditEvent) and row.id not in self.audits
        self.audits[row.id] = row
        self.trace.append("audit_add")

    def flush(self, items=None):
        self.trace.append("audit_flush")


def receipt(kind="paid_native"):
    return old.NativeExpenseReceipt(kind=kind, target_id="synthetic-expense", provenance="measured_usage",
        actual_minor=9, source_sha256="a" * 64, completion_sha256="b" * 64,
        allocation="direct_operation_metering", work_state="completed", window_start=NOW-timedelta(minutes=1),
        terminal_at=NOW+timedelta(seconds=30), window_end=NOW+timedelta(minutes=1),
        reviewed_at=NOW+timedelta(minutes=2))


def terminal(kind="paid_native"):
    return old.TerminalWorkEvidence(provenance="operator_reviewed_native_execution", kind=kind,
        target_id="synthetic-expense", financial_group_id="synthetic-group" if kind == "paid_native" else None,
        reservation_id="synthetic-reservation" if kind == "paid_native" else "synthetic-expense",
        execution_readback_sha256=("d" * 64,), work_state="completed", all_native_attempts_terminal=True,
        no_future_native_work=True, terminal_at=NOW+timedelta(seconds=30), observed_at=NOW+timedelta(minutes=2))


def plan(db):
    return native.capture(db, receipt=receipt(db.kind), terminal=terminal(db.kind), actor=ACTOR,
        nonce="c" * 32, now=STAMP)


@pytest.fixture(autouse=True)
def no_tls_key_logging(monkeypatch):
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)


def test_identity_runs_real_read_only_native_and_exact_fence_checks():
    db = Port()
    observed = native.database_identity(db)
    assert len(observed) == 64 and all(sql.startswith("SELECT ") for sql, _ in db.calls)
    changed = Port()
    changed.identity = (*changed.identity[:-1], "other-synthetic-cluster")
    assert native.database_identity(changed) != observed
    with pytest.raises(old.ReconciliationDenied, match="database_schema_mismatch"):
        old.database_identity(db)
    db.version = old.SCHEMA
    with pytest.raises(native.ReconciliationDenied, match="exact_marker_required"):
        native.database_identity(db)


@pytest.mark.parametrize("change", ["sqlite", "unverified_url", "tls", "control", "missing_fence", "changed_fence"])
def test_identity_refuses_wrong_dialect_tls_native_control_or_actual_trigger(change):
    db = Port()
    if change == "sqlite":
        db.dialect = SimpleNamespace(name="sqlite")
    elif change == "unverified_url":
        db.url = db.url.set(query={"sslmode": "require"})
    elif change == "tls":
        db.tls = (False, "TLSv1.3", 256)
    elif change == "control":
        db.control = [(9999, 10, False, "pg_control_system", "internal")]
    elif change == "missing_fence":
        db.rows = []
    else:
        db.rows[0]["tgenabled"] = "O"
    with pytest.raises((native.ReconciliationDenied, EvidenceWriteFenceDenied)):
        native.database_identity(db)
    assert not db.trace and not db.audits
    if change in {"sqlite", "unverified_url"}:
        assert not db.calls


@pytest.mark.parametrize("operation", [native.dry_run, native.apply])
@pytest.mark.parametrize("old_type", ["old_model", "wrong_version_copy"])
def test_old_or_version_tampered_plan_refuses_before_any_port_access(operation, old_type):
    current = plan(Port())
    invalid = (old.ReconciliationPlan.model_validate({**current.model_dump(mode="json"), "version": "native-expense-plan-v1"})
        if old_type == "old_model" else current.model_copy(update={"version": "native-expense-plan-v1"}))

    class NoIO:
        def __getattr__(self, name):
            raise AssertionError("Cross-version plans must refuse before IO")

    with pytest.raises(native.ReconciliationDenied, match="exact_plan_required"):
        operation(NoIO(), plan=invalid, now=STAMP)


@pytest.mark.parametrize("kind", ["paid_native", "free_core"])
def test_capture_dry_run_and_exact_replay_keep_original_lock_order_and_no_customer_grant(monkeypatch, kind):
    db = Port(kind)
    current = plan(db)
    assert type(current) is native.ReconciliationPlan and current.version == native.PLAN_VERSION
    assert native.dry_run(db, plan=current, now=STAMP)["status"] == "ready_for_operator_review"
    assert not db.trace and not db.audits

    def settle_paid(port, row, **kwargs):
        assert port is db and row.native_settled_minor == 9
        db.settlements += 1

    def settle_free(port, **kwargs):
        assert port is db and kwargs["reservation_id"] == db.target.id
        db.target.settled_minor, db.target.state = kwargs["actual_minor"], "settled"
        db.target.settlement_receipt_sha256 = kwargs["receipt_sha256"]
        db.settlements += 1

    # Only new-module delegation seams are replaced; old identities/financial
    # globals and business validators remain the actual original functions.
    monkeypatch.setattr(native, "_refresh_hold", settle_paid)
    monkeypatch.setattr(native, "_settle_measured_expense", settle_free)
    assert native.apply(db, plan=current, now=STAMP)["status"] == "native_expense_reconciled"
    money = ["SELECT pg_advisory_xact_lock(7349186502109)", "SELECT pg_advisory_xact_lock(7349186502110)"]
    assert db.trace[:3] == (["target_lock", *money] if kind == "paid_native" else [*money, "target_lock"])
    assert db.settlements == 1 and len(db.audits) == 1
    assert next(iter(db.audits)) == old._audit_id(current.receipt)
    assert next(iter(db.audits.values())).action == native.ACTION
    assert native.apply(db, plan=current, now=STAMP)["status"] == "already_reconciled"
    assert db.settlements == 1 and db.customer_balance == 17 and db.unknown_provider_reserve == 12345
    assert native.dry_run(db, plan=current, now=STAMP+timedelta(days=2))["status"] == "already_reconciled"
    with pytest.raises(native.ReconciliationDenied, match="expired"):
        native.apply(db, plan=current, now=STAMP+timedelta(days=2))
    with pytest.raises(native.ReconciliationDenied, match="conflict"):
        native.apply(db, plan=current.model_copy(update={"nonce": "e" * 32}), now=STAMP)
    assert db.settlements == 1


def test_historical_settlement_audit_never_becomes_new_settlement_or_label():
    db = Port()
    current = plan(db)
    historical = AdminAuditEvent(id=old._audit_id(current.receipt), action=old.ACTION,
        target_type="paid_native", target_id=db.target.id, after_state={"historical_schema25": True})
    db.audits[historical.id] = historical
    db.target.native_settled_minor = 9
    before = copy.deepcopy(historical.after_state)
    for operation in (native.dry_run, native.apply):
        with pytest.raises(native.ReconciliationDenied, match="conflict"):
            operation(db, plan=current, now=STAMP)
    with pytest.raises(native.ReconciliationDenied, match="conflict"):
        plan(db)
    assert historical.action == old.ACTION and historical.after_state == before and db.settlements == 0


@pytest.mark.parametrize("change", ["identity", "preimage", "terminal"])
def test_changed_database_target_or_terminal_work_refuses_before_settlement(change):
    db = Port()
    current = plan(db)
    if change == "identity":
        db.identity = (*db.identity[:-1], "other-cluster")
    elif change == "preimage":
        db.target.reserved_minor += 1
    else:
        db.target.delivery_state = "running"
    with pytest.raises(native.ReconciliationDenied):
        native.apply(db, plan=current, now=STAMP)
    assert not db.audits and db.target.native_settled_minor is None


@pytest.mark.parametrize("version", [None, "native-expense-plan-v1", "native-expense-plan-schema26-v2"])
def test_expense_cli_decode_requires_an_explicit_new_version(version):
    value = plan(Port()).model_dump(mode="json")
    if version is None:
        value.pop("version")
    else:
        value["version"] = version
    raw = json.dumps({"plan": value, "source_path": "/synthetic/source", "completion_path": "/synthetic/completion"}).encode()
    with pytest.raises(native.ReconciliationDenied, match="exact_plan_required"):
        expense_cli.decode_plan(raw)


def audience(db):
    now = canary.stamp()
    accepted = now-timedelta(days=1)
    db.members = [(1, "synthetic-owner@business.example.com", accepted, "synthetic-terms", "synthetic-privacy", accepted)]
    runtime = SimpleNamespace(policy=SimpleNamespace(expires_at=now+timedelta(days=2)),
        budgets=SimpleNamespace(expires_at=now+timedelta(days=2)), model_dump=lambda **kwargs: {"synthetic_runtime": 26})
    notice = "Synthetic operator-reviewed business audience; participation still requires separate owner consent."
    review = canary.CanaryReview(id="synthetic26-audience", runtime_sha256=payload_fingerprint(runtime.model_dump()),
        notice=notice, notice_sha256=hashlib.sha256(notice.encode()).hexdigest(), reviewed_by=ACTOR.email,
        review_evidence_sha256="b" * 64, real_business_accounts_and_exclusions_reviewed=True,
        census_at=now, valid_from=now, expires_at=now+timedelta(hours=1), traffic_started_at=accepted,
        members=canary.census(db, census_at=now, excluded_owner_ids=()), selected_owner_ids=(1,),
        excluded_owner_ids=(), families=("matching",))
    value = {"version": audience_cli.PLAN_VERSION, "database_sha256": native.database_identity(db),
        "review": review.model_dump(mode="json"), "operator_native_identity_sha256": ACTOR.native_identity_sha256}
    return value, review, runtime


def test_canary_successor_records_original_review_plus_new_atomic_companion_and_observes_same_plan(monkeypatch):
    db = Port()
    value, review, runtime = audience(db)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    digest = audience_cli.record_review(db, value, runtime=runtime, evidence_sha="b" * 64)
    assert digest == payload_fingerprint(review.model_dump(mode="json")) and len(db.audits) == 2
    assert {row.action for row in db.audits.values()} == {canary.REVIEW_ACTION, audience_cli.ACTION}
    assert audience_cli.observe_review(db, value, evidence_sha="b" * 64) == digest
    assert audience_cli.record_review(db, value, runtime=runtime, evidence_sha="b" * 64) == digest
    assert len(db.audits) == 2 and db.customer_balance == 17
    import os
    assert os.environ["UNIFIED_CREDIT_EXECUTION_ENABLED"] == "false"


def test_canary_historical_review_without_new_companion_is_not_adopted():
    db = Port()
    value, review, runtime = audience(db)
    canary.save_review(db, review, runtime=runtime)  # Historical runtime-compatible audit only.
    before = {key: copy.deepcopy(row.after_state) for key, row in db.audits.items()}
    for operation in (lambda: audience_cli.record_review(db, value, runtime=runtime, evidence_sha="b" * 64),
                      lambda: audience_cli.observe_review(db, value, evidence_sha="b" * 64)):
        with pytest.raises(ValueError):
            operation()
    assert {key: row.after_state for key, row in db.audits.items()} == before


@pytest.mark.parametrize("version", [None, "canary-audience-plan-v1", "canary-audience-plan-schema26-v2"])
def test_canary_cross_version_plan_refuses_before_db_access(version):
    value, _, _ = audience(Port())
    if version is None:
        value.pop("version")
    else:
        value["version"] = version

    class NoIO:
        def __getattr__(self, name):
            raise AssertionError("Old canary plan must refuse before IO")

    with pytest.raises(ValueError):
        audience_cli.record_review(NoIO(), value, runtime=None, evidence_sha="b" * 64)
    with pytest.raises(ValueError):
        audience_cli.observe_review(NoIO(), value, evidence_sha="b" * 64)


def test_canary_changed_review_database_evidence_or_plan_never_uses_previous_companion():
    db = Port()
    value, _, runtime = audience(db)
    audience_cli.record_review(db, value, runtime=runtime, evidence_sha="b" * 64)
    for changed in ({**value, "database_sha256": "e" * 64},
                    {**value, "operator_native_identity_sha256": "e" * 64}):
        with pytest.raises(ValueError):
            audience_cli.observe_review(db, changed, evidence_sha="b" * 64)
    with pytest.raises(ValueError):
        audience_cli.observe_review(db, value, evidence_sha="e" * 64)
    assert len(db.audits) == 2


def test_model_copy_cannot_bypass_nested_receipt_validation():
    current = plan(Port())
    malformed = current.model_copy(update={"receipt": current.receipt.model_copy(update={"actual_minor": True})})
    with pytest.raises(ValidationError):
        native.dry_run(SimpleNamespace(), plan=malformed, now=STAMP)
