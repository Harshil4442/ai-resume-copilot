"""Owned synthetic operator protocol; no native qualification or real funding."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
import payment_funding_operator as operator  # noqa: E402

STAMP = datetime(2026, 11, 5, 12, tzinfo=UTC)
HASH = "a" * 64
COMPONENT = "b" * 64
FACT = "c" * 64


@dataclass(frozen=True)
class NativeContextFixture:
    synthetic_only: str = "not-native-authority"


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    from app.domains.credits import admission, finance, payment_expenses, payment_funding
    from app.models import AdminAuditEvent

    engine = create_engine("sqlite://", poolclass=StaticPool)
    AdminAuditEvent.__table__.create(engine)
    state = {"verify": 0, "write_sessions": 0, "locks": 0, "commit": "normal"}
    snapshot = payment_expenses.ExpenseSnapshot(facts_sha256=HASH, components=(
        payment_expenses.LossComponent(key=COMPONENT, kind="payment_fee", required_minor=100,
            facts_sha256=FACT, basis="known", review_required=True),), unresolved=())
    monkeypatch.setattr(payment_expenses, "collect_expenses", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(admission, "lock_money", lambda _db: state.__setitem__("locks", state["locks"] + 1))
    monkeypatch.setattr(operator, "source_hash", lambda _root: HASH)
    boundary = object.__new__(operator.NativeFundingBoundary)
    boundary.root, boundary.plan = ROOT, NativeContextFixture()
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    original.joinpath("owner.lock").touch(mode=0o600)
    boundary.native = SimpleNamespace(bridge_journal=SimpleNamespace(directory=original))

    def verify():
        state["verify"] += 1
        if state.get("native_denied"):
            raise RuntimeError("synthetic native refusal")
        return HASH

    @contextmanager
    def session(*, readonly, consistent=False):
        if not readonly:
            state["write_sessions"] += 1
        with Session(engine) as db:
            actual_commit = db.commit

            def commit():
                if state["commit"] == "before":
                    raise RuntimeError("synthetic unknown before commit")
                actual_commit()
                if state["commit"] == "after":
                    raise RuntimeError("synthetic lost committed ACK")

            monkeypatch.setattr(db, "commit", commit)
            yield db
            if readonly:
                db.rollback()

    boundary.verify, boundary.session = verify, session
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b'{"fixture":"synthetic-no-bank-funding"}\n')
    evidence_path.chmod(0o600)
    evidence_sha = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    policy = finance.FinancialPolicy(version="synthetic-operator-policy", valid_from=STAMP - timedelta(days=1),
        expires_at=STAMP + timedelta(days=1), provenance="invoice_reconciled", evidence_sha256=evidence_sha)
    declaration = payment_funding.FundingDeclaration(external_reference_sha256="d" * 64, amount_minor=500,
        funded_at=STAMP - timedelta(hours=1), availability_basis="available_after_existing_obligations",
        evidence_sha256=evidence_sha, reviewer_reference_sha256="e" * 64,
        reviewed_at=STAMP - timedelta(minutes=5), review_valid_until=STAMP + timedelta(days=1))
    allocation = payment_funding.AllocationRequest(component_key=COMPONENT, expected_facts_sha256=FACT,
        funding_reference_sha256=declaration.external_reference_sha256, amount_minor=100,
        expense_evidence_sha256=evidence_sha, reviewer_reference_sha256="e" * 64,
        reviewed_at=STAMP - timedelta(minutes=5), review_valid_until=STAMP + timedelta(days=1),
        basis="reviewed_invoice_declaration")
    request = {"policy": policy.model_dump(mode="json"), "declaration": declaration.model_dump(mode="json"),
        "allocations": [allocation.model_dump(mode="json")]}
    plan = operator.prepare(boundary, request, {evidence_sha: str(evidence_path)}, nonce="f" * 32, instant=STAMP)
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(mode=0o700)
    journal = operator.native.rolling.Journal(journal_dir, plan)
    yield SimpleNamespace(boundary=boundary, state=state, plan=plan, journal=journal, engine=engine,
        audit=AdminAuditEvent, expenses=payment_expenses, funding=payment_funding, snapshot=snapshot,
        finance=finance, evidence=evidence_path)
    engine.dispose()


def execute(owned, **kwargs):
    return operator.execute(owned.plan, owned.boundary, owned.journal, instant=STAMP, **kwargs)


def test_prepare_is_readonly_and_reviewable(owned):
    assert owned.state["write_sessions"] == 0
    assert owned.state["verify"] == 2
    assert owned.plan.expense_review["components"][0]["required_minor"] == 100
    with Session(owned.engine) as db:
        assert db.scalars(select(owned.audit)).all() == []


def test_apply_and_reconcile_join_real_detached_audit_rows(owned):
    result = execute(owned)
    assert result["transaction_observed"] is True
    assert result["bank_cash_independently_verified"] is result["activation_granted"] is False
    assert execute(owned, reconcile=True) == result
    assert owned.state["write_sessions"] == 1
    with Session(owned.engine) as db:
        assert len(db.scalars(select(owned.audit)).all()) == 4


def test_lost_commit_ack_reconciles_without_replay(owned):
    owned.state["commit"] = "after"
    with pytest.raises(RuntimeError, match="lost committed"):
        execute(owned)
    with pytest.raises(Exception, match="started_reconcile_only"):
        execute(owned)
    assert owned.journal.read(operator.MARKER + ".commit-acknowledged") is None
    owned.state["commit"] = "normal"
    assert execute(owned, reconcile=True)["transaction_observed"] is True
    assert owned.state["write_sessions"] == 1


def test_unknown_uncommitted_transaction_stays_reconcile_only(owned):
    owned.state["commit"] = "before"
    with pytest.raises(RuntimeError, match="unknown before"):
        execute(owned)
    assert execute(owned, reconcile=True)["transaction_observed"] is False
    with pytest.raises(Exception, match="started_reconcile_only"):
        execute(owned)
    assert owned.state["write_sessions"] == 1


def test_known_commit_then_restored_missing_audit_is_denied(owned):
    execute(owned)
    with Session(owned.engine) as db:
        db.delete(db.get(owned.audit, operator.audit_id(owned.plan)))
        db.commit()
    with pytest.raises(Exception, match="known_commit_missing_or_changed"):
        execute(owned, reconcile=True)


def test_domain_audit_alteration_is_not_adopted(owned):
    execute(owned)
    with Session(owned.engine) as db:
        row = db.query(owned.audit).filter_by(action=owned.funding.FUND_ACTION).one()
        row.actor_email = "synthetic-invalid-actor"
        db.commit()
    with pytest.raises(Exception, match="audit_shape_changed"):
        execute(owned, reconcile=True)


def test_existing_intent_without_apply_has_no_reconcile_authority(owned):
    with pytest.raises(Exception, match="started_reconcile_only"):
        execute(owned, reconcile=True)
    assert owned.state["write_sessions"] == 0


def test_expired_plan_stops_before_intent_or_writer(owned):
    with pytest.raises(Exception, match="plan_expired"):
        operator.execute(owned.plan, owned.boundary, owned.journal, instant=STAMP + timedelta(hours=2))
    assert owned.journal.read(operator.MARKER + ".started") is None
    assert owned.state["write_sessions"] == 0


def test_clock_refresh_after_native_gate_stops_expired_admission(owned, monkeypatch):
    clock = [STAMP]
    monkeypatch.setattr(operator, "now", lambda: clock[0])
    original = owned.boundary.verify

    def verify():
        result = original()
        clock[0] += timedelta(hours=2)
        return result

    owned.boundary.verify = verify
    with pytest.raises(Exception, match="plan_expired"):
        operator.execute(owned.plan, owned.boundary, owned.journal)
    assert owned.journal.read(operator.MARKER + ".started") is None
    assert owned.state["write_sessions"] == 0


def test_clock_refresh_after_money_lock_stops_before_first_mutation(owned, monkeypatch):
    from app.domains.credits import admission

    clock = [STAMP]
    monkeypatch.setattr(operator, "now", lambda: clock[0])
    monkeypatch.setattr(admission, "lock_money", lambda _db: clock.__setitem__(0, STAMP + timedelta(hours=2)))
    with pytest.raises(Exception, match="plan_expired"):
        operator.execute(owned.plan, owned.boundary, owned.journal)
    assert owned.journal.read(operator.MARKER + ".started") is not None
    assert owned.journal.read(operator.MARKER + ".commit-acknowledged") is None
    with Session(owned.engine) as db:
        assert db.scalars(select(owned.audit)).all() == []


def test_prepare_uses_post_gate_creation_clock(owned, monkeypatch):
    clock = [STAMP]
    monkeypatch.setattr(operator, "now", lambda: clock[0])
    original = owned.boundary.verify

    def verify():
        result = original()
        clock[0] += timedelta(minutes=1)
        return result

    owned.boundary.verify = verify
    plan = operator.prepare(owned.boundary, owned.plan.request, owned.plan.evidence, nonce="1" * 32)
    assert datetime.fromisoformat(plan.created_at) == STAMP + timedelta(minutes=2)
    assert owned.state["write_sessions"] == 0


@pytest.mark.parametrize("provenance", ["estimated", "synthetic_test"])
def test_noninvoice_policy_never_grants_apply(owned, provenance):
    request = dict(owned.plan.request)
    request["policy"] = dict(request["policy"], provenance=provenance)
    plan = replace(owned.plan, request=request)
    journal = operator.native.rolling.Journal(owned.journal.directory, plan)
    with pytest.raises(owned.finance.FundingUnavailable, match="reconciled"):
        operator.execute(plan, owned.boundary, journal, instant=STAMP)
    assert journal.read(operator.MARKER + ".started") is None
    assert owned.state["write_sessions"] == 0


def test_native_gate_refusal_precedes_writer(owned):
    owned.state["native_denied"] = True
    with pytest.raises(RuntimeError, match="native refusal"):
        execute(owned)
    assert owned.state["write_sessions"] == 0
    assert owned.journal.read(operator.MARKER + ".started") is None


def test_changed_retained_evidence_refuses_without_native_or_sql(owned):
    owned.evidence.write_bytes(b'changed')
    before = owned.state["verify"]
    with pytest.raises(Exception, match="evidence_changed"):
        execute(owned)
    assert owned.state["write_sessions"] == 0
    assert owned.state["verify"] == before


def test_new_facts_after_prepare_roll_back_and_never_replay(owned, monkeypatch):
    monkeypatch.setattr(owned.expenses, "collect_expenses", lambda *_args, **_kwargs:
        owned.snapshot.model_copy(update={"facts_sha256": "9" * 64}))
    with pytest.raises(Exception, match="expense_preimage_changed"):
        execute(owned)
    with Session(owned.engine) as db:
        assert db.scalars(select(owned.audit)).all() == []
    with pytest.raises(Exception, match="started_reconcile_only"):
        execute(owned)


def test_exact_plan_and_source_binding_is_required(owned):
    altered = replace(owned.plan, source_sha256="9" * 64)
    journal = operator.native.rolling.Journal(owned.journal.directory, altered)
    with pytest.raises(Exception, match="exact_plan_required"):
        operator.execute(altered, owned.boundary, journal, instant=STAMP)
    assert owned.state["write_sessions"] == 0


def test_checked_receipt_tampering_refuses(owned):
    execute(owned)
    path = owned.journal.directory / (operator.MARKER + ".checked.json")
    value = json.loads(path.read_text())
    value["transaction_observed"] = False
    path.write_text(json.dumps(value))
    with pytest.raises(Exception, match="checked_observation_changed"):
        execute(owned, reconcile=True)


@pytest.mark.parametrize("marker,committed,allowed", [("26", True, False), ("27", False, False), ("27", True, True)])
def test_actual_boundary_requires_native_gate_distinct27_and_original_receipt(monkeypatch, marker, committed, allowed):
    events = []
    boundary = object.__new__(operator.NativeFundingBoundary)
    bridge = object.__new__(operator.native.NativeMigration27Boundary)
    boundary.native = bridge
    acls = [("synthetic", "catalog-only")]

    @dataclass(frozen=True)
    class Plan:
        retained_acl_sha256: str = operator.native.rolling.sha(acls)

    boundary.plan = Plan()
    bridge.verify_live = lambda _plan: events.append("full-native-gates")

    @contextmanager
    def readonly(schema):
        events.append(schema)
        assert schema == operator.native.contract.SCHEMA
        if marker == "26":
            raise RuntimeError("synthetic exact schema mismatch")
        yield object()

    bridge.readonly = readonly
    monkeypatch.setattr(operator.native, "reconcile_sql", lambda *_args:
        {"migration_committed": committed, "receipt_sha256": HASH})
    monkeypatch.setattr(operator.native.contract, "observe", lambda *_args: HASH)
    monkeypatch.setattr(operator.native.step, "old_acls", lambda *_args: acls)
    if allowed:
        assert len(boundary.verify()) == 64
    else:
        with pytest.raises(Exception, match="schema mismatch|migration_receipt_required"):
            boundary.verify()
    assert events == ["full-native-gates", operator.native.contract.SCHEMA]


def test_help_does_not_load_app_database_or_open_network():
    code = """
import runpy,socket,sys
def forbidden(*args,**kwargs):
    raise AssertionError('network forbidden')
socket.socket.connect=forbidden
sys.argv=[sys.argv[1],'--help']
try:
    runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit as error:
    assert error.code == 0
assert 'app.database' not in sys.modules and 'app.models' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code,
        str(ROOT / "infra/gcp/payment_funding_operator.py")], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert b"--dsn" not in result.stdout and b"--plugin" not in result.stdout
