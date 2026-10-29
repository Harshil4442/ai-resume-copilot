"""Synthetic authority tests: no credential, native connection, or DDL is used."""
from __future__ import annotations

import hashlib
import importlib
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_rolling_release import FixtureBoundary

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
recovery = importlib.import_module("schema25_migration_recovery")
u = recovery.release


def context(tmp_path, monkeypatch):
    base = FixtureBoundary().plan
    value = {**asdict(base), "release": recovery.CANDIDATE,
             "schema_contract": u.contract.CONTRACT, "database_schema": u.contract.BEFORE,
             "prior_sources": []}
    old = u.load_plan(u.rolling.canonical(value))
    new = replace(old, nonce="d" * 32)
    before, after = tmp_path / "original", tmp_path / "fresh"
    before.mkdir(mode=0o700)
    after.mkdir(mode=0o700)
    original = u.rolling.Journal(before, old)
    fresh = u.rolling.Journal(after, new)
    started = {"expected": u.contract.BEFORE, "target": u.contract.SCHEMA,
               "retained_sha256": "1" * 64, "schema_acl": [[1, 2, "USAGE", False]],
               "replay_authorized": False}
    original.record("schema25-migration.started", started)
    raw = (before / "schema25-migration.started.json").read_bytes()
    monkeypatch.setattr(recovery, "ORIGINAL_STARTED_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(recovery, "ORIGINAL_JOURNAL_PATH_SHA256", recovery.path_hash(before))
    sources = {"schema25_migration_metadata.py": "2" * 64,
               "schema25_migration_recovery.py": "3" * 64,
               "unified_credit_schema25_release.py": "4" * 64}
    monkeypatch.setattr(recovery, "source_hashes", lambda expected: sources.copy())
    gates = []
    monkeypatch.setattr(recovery, "preflight", lambda *args: gates.append("preflight"))
    proof = {"schema": u.contract.BEFORE, "new_table_count": 0,
             "retained_sha256": started["retained_sha256"], "schema_acl": started["schema_acl"],
             "native_identity_verified": True, "advisory_lock_exclusive": True,
             "accepted_work_drained": True, "read_only_transaction": True}
    monkeypatch.setattr(recovery, "rollback_proof", lambda *args: proof.copy())
    boundary = SimpleNamespace(root=ROOT)
    return old, new, original, fresh, boundary, sources, proof, gates


def execute(data, phase):
    old, new, original, fresh, boundary, *_ = data
    return recovery.execute(phase, old, new, original, fresh, boundary, "2" * 64)


def test_proof_retains_original_attempt_and_never_calls_migration(tmp_path, monkeypatch):
    data = context(tmp_path, monkeypatch)
    original = data[2]
    original_bytes = (original.directory / "schema25-migration.started.json").read_bytes()
    monkeypatch.setattr(u, "migration", lambda *a, **kw: pytest.fail("proof must never perform DDL"))
    result = execute(data, "prove")
    assert result["provider_mutations"] == 0
    assert result["replay_authorized"] is False
    assert (original.directory / "schema25-migration.started.json").read_bytes() == original_bytes
    receipt = original.read("schema25-recovery.proof")
    assert receipt["binding"]["new_plan_sha256"] == data[3].plan_hash
    assert receipt["rollback_proof"] == data[6]
    assert original.read("schema25-recovery.started") is None
    assert data[3].read("schema25-migration.started") is None
    assert execute(data, "prove") == result


def test_exact_corrected_attempt_is_linked_before_only_new_migration(tmp_path, monkeypatch):
    data = context(tmp_path, monkeypatch)
    old, new, original, fresh, boundary, *_ = data
    execute(data, "prove")
    calls = []

    def migration(plan, actual_boundary, journal, **kwargs):
        assert plan is new and actual_boundary is boundary and journal is fresh
        assert original.read("schema25-recovery.started") is not None
        assert kwargs == {}
        calls.append("new_attempt")
        journal.record("schema25-migration.started", {"replay_authorized": False})
        return {"status": "schema25_committed_and_freshly_observed", "schema": u.contract.SCHEMA}

    monkeypatch.setattr(u, "migration", migration)
    result = execute(data, "upgrade")
    assert calls == ["new_attempt"]
    assert result["corrected_attempt_replay_authorized"] is False
    assert original.read("schema25-recovery.checked")["schema"] == u.contract.SCHEMA
    with pytest.raises(u.rolling.RollingDenied):
        execute(data, "upgrade")
    assert calls == ["new_attempt"]
    assert old.nonce != new.nonce


def test_unknown_corrected_outcome_spends_claim_and_allows_only_reconciliation(tmp_path, monkeypatch):
    data = context(tmp_path, monkeypatch)
    original, fresh = data[2:4]
    execute(data, "prove")
    calls = []

    def migration(plan, boundary, journal, *, reconcile=False):
        calls.append(reconcile)
        if not reconcile:
            journal.record("schema25-migration.started", {"replay_authorized": False})
            raise RuntimeError("private provider text")
        return {"status": "schema21_observed_reconcile_only", "schema": u.contract.BEFORE,
                "replay_authorized": False}

    monkeypatch.setattr(u, "migration", migration)
    with pytest.raises(RuntimeError):
        execute(data, "upgrade")
    assert original.read("schema25-recovery.started") is not None
    assert fresh.read("schema25-migration.started") is not None
    with pytest.raises(u.rolling.RollingDenied):
        execute(data, "upgrade")
    assert execute(data, "reconcile")["schema"] == u.contract.BEFORE
    assert calls == [False, True]
    assert original.read("schema25-recovery.checked") is None


@pytest.mark.parametrize("field,value", [
    ("retained_sha256", "9" * 64), ("schema_acl", []), ("new_table_count", 1),
    ("schema", u.contract.SCHEMA), ("advisory_lock_exclusive", False),
    ("accepted_work_drained", False), ("native_identity_verified", False),
    ("read_only_transaction", False),
])
def test_every_rollback_proof_fact_is_required_before_any_attempt(tmp_path, monkeypatch, field, value):
    data = context(tmp_path, monkeypatch)
    data[6][field] = value
    monkeypatch.setattr(u, "migration", lambda *a, **kw: pytest.fail("invalid proof cannot migrate"))
    with pytest.raises(u.rolling.RollingDenied, match="rollback_not_proven"):
        execute(data, "prove")
    assert data[2].read("schema25-recovery.proof") is None
    assert data[2].read("schema25-recovery.started") is None


@pytest.mark.parametrize("field,value", [
    ("release", "a" * 40), ("financial_sha256", "8" * 64),
    ("workers_release", "f" * 40), ("runtime_secret", "other-runtime:3"),
    ("nonce", "c" * 32),
])
def test_fresh_plan_cannot_change_original_authority(tmp_path, monkeypatch, field, value):
    data = list(context(tmp_path, monkeypatch))
    data[1] = replace(data[1], **{field: value})
    with pytest.raises(u.rolling.RollingDenied):
        execute(data, "prove")
    assert data[2].read("schema25-recovery.proof") is None


def test_original_started_hash_and_exact_original_journal_are_required(tmp_path, monkeypatch):
    data = context(tmp_path, monkeypatch)
    monkeypatch.setattr(recovery, "ORIGINAL_STARTED_SHA256", "f" * 64)
    with pytest.raises(u.rolling.RollingDenied, match="original_started_changed"):
        execute(data, "prove")
    monkeypatch.setattr(recovery, "ORIGINAL_JOURNAL_PATH_SHA256", "e" * 64)
    with pytest.raises(u.rolling.RollingDenied, match="exact_original_journal_required"):
        execute(data, "prove")


def test_original_checked_or_fresh_journal_activity_prevents_recovery(tmp_path, monkeypatch):
    data = context(tmp_path, monkeypatch)
    data[3].record("unexpected", {"value": "inert"})
    with pytest.raises(u.rolling.RollingDenied, match="fresh_journal_scope_changed"):
        execute(data, "prove")
    data[2].record("schema25-migration.checked", {"schema": u.contract.SCHEMA})
    with pytest.raises(u.rolling.RollingDenied, match="original_outcome_not_failed_uncommitted"):
        execute(data, "prove")


def test_operator_source_or_rollback_preimage_drift_stops_before_claim(tmp_path, monkeypatch):
    data = context(tmp_path, monkeypatch)
    execute(data, "prove")
    data[5]["schema25_migration_metadata.py"] = "a" * 64
    with pytest.raises(u.rolling.RollingDenied, match="recovery_proof_changed"):
        execute(data, "upgrade")
    assert data[2].read("schema25-recovery.started") is None


def test_reconcile_requires_exact_prior_link_and_never_starts_attempt(tmp_path, monkeypatch):
    data = context(tmp_path, monkeypatch)
    monkeypatch.setattr(u, "migration", lambda *a, **kw: pytest.fail("no linked attempt"))
    with pytest.raises(u.rolling.RollingDenied, match="corrected_attempt_not_started"):
        execute(data, "reconcile")
    assert data[2].read("schema25-recovery.started") is None


def test_unrelated_original_journal_activity_refuses_before_proof(tmp_path, monkeypatch):
    data = context(tmp_path, monkeypatch)
    (data[2].directory / "unexpected").mkdir(mode=0o700)
    with pytest.raises(u.rolling.RollingDenied, match="original_journal_scope_changed"):
        execute(data, "prove")
    assert data[2].read("schema25-recovery.proof") is None


def test_checked_recovery_without_attempt_is_not_an_authority(tmp_path, monkeypatch):
    data = context(tmp_path, monkeypatch)
    data[2].record("schema25-recovery.checked", {"schema": u.contract.SCHEMA})
    with pytest.raises(u.rolling.RollingDenied, match="recovery_checked_without_attempt"):
        execute(data, "prove")
