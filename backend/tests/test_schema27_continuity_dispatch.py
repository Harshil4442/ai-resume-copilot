"""The exact bridge journals authorize only one migration dispatch."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_continuity_migration as migration
from infra.gcp import schema27_continuity_reconcile as recovery
from test_schema26_app_successor import context as context
from test_schema27_continuity_bridge import lane as lane
from test_schema27_continuity_bridge import run
from test_schema27_continuity_bridge_plan import current as current


@pytest.fixture
def ready(lane, tmp_path, monkeypatch):
    lane.boundary.root = Path(__file__).resolve().parents[2]
    lane.database.retained_acl_sha256 = "b" * 64
    monkeypatch.setattr(migration.catalog, "observe_schema26",
                        lambda *_a, **_kw: lane.database)
    assert run(lane, "advance")["rolling_release_complete"]
    plan = migration.capture(lane.boundary, lane.plan, lane.journal)
    plan = migration.load(migration.rolling.canonical(asdict(plan)))
    path = tmp_path / "owned-migration"
    path.mkdir(mode=0o700)
    journal = migration.rolling.Journal(path, plan)
    return SimpleNamespace(lane=lane, plan=plan, journal=journal)


def dispatch(ready, mode):
    lane = ready.lane
    return migration.execute(mode, lane.boundary, lane.plan, lane.journal, ready.plan, ready.journal)


def staged_intent(ready):
    return ready.plan.intent(migration.bootstrap.migration_identity(ready.lane.plan.database),
                             migration.native.successor.native.host_auth.ACCOUNT)


def fence_binding(ready, intent):
    return {
        "migration_plan_sha256": ready.journal.plan_hash, "intent_sha256": intent.sha256(),
        "migration_journal_location": migration.scope.journal_location(ready.journal),
    }


@pytest.mark.parametrize("fence_already_saved", [False, True])
def test_migration_requires_fresh_bridge_control_and_durable_intent(ready, monkeypatch, fence_already_saved):
    events = []
    receipt = "f" * 64
    lane = ready.lane
    if fence_already_saved:
        lane.journal.record(migration.scope.MIGRATION_FENCE, fence_binding(ready, staged_intent(ready)))
        with pytest.raises(migration.rolling.RollingDenied, match="migration_started"):
            run(lane, "rollback")
        with pytest.raises(migration.rolling.RollingDenied, match="started_reconcile_only"):
            dispatch(ready, "reconcile")

    @contextmanager
    def begin():
        started = ready.journal.read(recovery.STARTED)
        assert started and started["bridge_receipts_sha256"] == ready.plan.bridge_receipts_sha256
        fence = ready.lane.journal.read(migration.scope.MIGRATION_FENCE)
        assert fence["migration_plan_sha256"] == ready.journal.plan_hash
        assert fence["intent_sha256"] == migration.rolling.sha(started)
        assert fence["migration_journal_location"] == migration.scope.journal_location(ready.journal)
        assert ready.journal.read(recovery.COMMIT_ACK) is None
        events.append("connect")
        yield object()
        events.append("commit")

    def upgrade(_db, _identity, intent, **options):
        assert options["journal_intent_sha256"] == intent.sha256()
        events.append("upgrade")
        return {"transaction_ready_to_commit": True, "committed": False, "receipt_sha256": receipt}

    def observe(custody, bridge_journal, journal, identity, intent, **options):
        recovery.validate_custody(custody, bridge_journal, journal, identity, intent,
                                  bridge_plan_sha256=options["bridge_plan_sha256"],
                                  bridge_receipts_sha256=options["bridge_receipts_sha256"])
        assert journal.read(recovery.COMMIT_ACK)["receipt_sha256"] == receipt
        events.append("readback")
        return recovery.CommitObservation("schema27_same_transaction_observed", migration.contract.SCHEMA,
                                          "a" * 64, ready.plan.retained_acl_sha256, receipt)

    monkeypatch.setattr(migration, "_metadata", lambda _root: nullcontext())
    engine = SimpleNamespace(begin=begin, dispose=lambda: events.append("dispose"))
    monkeypatch.setattr(migration.bootstrap, "engine", lambda _database: engine)
    monkeypatch.setattr(migration.step, "upgrade_transaction", upgrade)
    monkeypatch.setattr(recovery, "reconcile_locked", observe)
    assert dispatch(ready, "upgrade")["migration_committed"] is True
    assert events == ["connect", "upgrade", "commit", "dispose", "readback"]
    assert ready.journal.read(recovery.CHECKED)["receipt_sha256"] == receipt
    with pytest.raises(migration.rolling.RollingDenied, match="started_reconcile_only"):
        dispatch(ready, "upgrade")
    assert dispatch(ready, "reconcile")["migration_committed"] is True
    assert events.count("connect") == events.count("commit") == 1
    writes = list(lane.fixture.writes)
    with pytest.raises(migration.rolling.RollingDenied, match="migration_started"):
        run(lane, "rollback")
    assert lane.fixture.writes == writes
    assert lane.journal.read(migration.native.historical.ROLLBACK_INTENT) is None


def test_bridge_and_migration_share_lifecycle_lock(ready, monkeypatch):
    monkeypatch.setattr(migration.bootstrap, "engine", lambda _: pytest.fail("unexpected bootstrap"))
    lane = ready.lane
    writes = list(lane.fixture.writes)
    with migration.scope.lifecycle_lock(lane.journal):
        with pytest.raises(BlockingIOError):
            run(lane, "rollback")
        with pytest.raises(BlockingIOError):
            dispatch(ready, "upgrade")
    assert lane.fixture.writes == writes
    assert lane.journal.read(migration.native.historical.ROLLBACK_INTENT) is None
    assert lane.journal.read(migration.scope.MIGRATION_FENCE) is None
    assert ready.journal.read(recovery.STARTED) is None


def test_bridge_rollback_refuses_a_copy_of_the_original_journal(ready, tmp_path):
    lane = ready.lane
    copied = copytree(lane.journal.directory, tmp_path / "copied-bridge")
    journal = migration.bridge.bind(lane.plan, copied)
    writes = list(lane.fixture.writes)
    with pytest.raises(migration.rolling.RollingDenied, match="lifecycle_journal_location"):
        migration.native.execute("rollback", lane.plan, lane.boundary, journal)
    assert lane.fixture.writes == writes


def test_migration_rejects_wrong_plan_and_rollback_before_bootstrap(ready, monkeypatch):
    monkeypatch.setattr(migration.bootstrap, "engine", lambda _: pytest.fail("unexpected bootstrap"))
    wrong = replace(ready.plan, bridge_receipts_sha256="0" * 64)
    lane = ready.lane
    with pytest.raises(migration.rolling.RollingDenied, match="exact_owner"):
        migration.execute("upgrade", lane.boundary, lane.plan, lane.journal, wrong, ready.journal)
    lane.journal.record(migration.native.historical.ROLLBACK_INTENT, {"owned": True})
    with pytest.raises(migration.rolling.RollingDenied, match="bridge_rollback_started"):
        dispatch(ready, "upgrade")
    assert ready.journal.read(recovery.STARTED) is None


def retain_inspection_proof(ready, *, fence=True, started=True, changed=False):
    bridge_journal, migration_journal = ready.lane.journal, ready.journal
    intent = staged_intent(ready)
    with migration.scope.lifecycle_lock(bridge_journal), bridge_journal.locked(), migration_journal.locked():
        if fence:
            bridge_journal.record(migration.scope.MIGRATION_FENCE, fence_binding(ready, intent))
        if started:
            value = intent.snapshot()
            if changed:
                value["source_sha256"] = "0" * 64
            migration_journal.record(recovery.STARTED, value)
    return intent


def test_inspection_reads_exact_outcome_without_fresh_rollout_prerequisites_or_writes(ready, monkeypatch):
    intent = retain_inspection_proof(ready)
    lane, journal = ready.lane, ready.journal
    monkeypatch.setattr(migration.native.successor, "controller_source", lambda root: (
        lane.plan.controller_release, lane.plan.controller_source_sha256,
    ))
    monkeypatch.setattr(migration.bootstrap, "engine", lambda *_: pytest.fail("inspection opened bootstrap"))
    monkeypatch.setattr(migration.runtime, "observe_runtime",
                        lambda *_: pytest.fail("inspection read runtime credentials"))

    def expired(*_args):
        raise migration.rolling.RollingDenied("synthetic_scanner_or_policy_expired")

    monkeypatch.setattr(migration, "_verify_live", expired)
    with pytest.raises(migration.rolling.RollingDenied, match="synthetic_scanner_or_policy_expired"):
        dispatch(ready, "reconcile")
    paths = (lane.journal.directory, journal.directory)
    prior_files = {file: file.read_bytes() for path in paths for file in path.iterdir()}
    writes = list(lane.fixture.writes)

    def observe(*args, **options):
        assert args[4] == intent
        assert options["bridge_receipts_sha256"] == ready.plan.bridge_receipts_sha256
        for lock in (migration.scope.lifecycle_lock(lane.journal), lane.journal.locked(), journal.locked()):
            with pytest.raises(BlockingIOError), lock:
                pytest.fail("inspection does not hold the original lifecycle and journal locks")
        return recovery.CommitObservation("schema27_same_transaction_observed", migration.contract.SCHEMA,
                                          "a" * 64, ready.plan.retained_acl_sha256, "f" * 64)

    monkeypatch.setattr(recovery, "reconcile_locked", observe)
    assert dispatch(ready, migration.INSPECT) == {
        "status": "continuity_migration_outcome_inspected", "schema": migration.contract.SCHEMA,
        "migration_committed": True, "formal_reconciliation": False, "replay_authorized": False,
        "new_admission_enabled": False, "cloud_resource_mutations": 0,
    }
    assert {file: file.read_bytes() for path in paths for file in path.iterdir()} == prior_files
    assert lane.fixture.writes == writes
    assert journal.read(recovery.COMMIT_ACK) is journal.read(recovery.CHECKED) is None


@pytest.mark.parametrize(("change", "reason"), [
    ("missing_fence", "bridge_migration_intent"),
    ("missing_started", "started_reconcile_only"),
    ("changed_started", "inspection_intent_changed"),
    ("wrong_source", "inspection_source_changed"),
    ("copied_bridge", "inspection_bridge_journal_location"),
    ("copied_journal", "bridge_migration_intent"),
])
def test_missing_or_wrong_inspection_proof_stops_before_observer(ready, monkeypatch, tmp_path, change, reason):
    retain_inspection_proof(ready, fence=change != "missing_fence", started=change != "missing_started",
                            changed=change == "changed_started")
    monkeypatch.setattr(migration.native.successor, "controller_source", lambda _root: ("0" * 40, "0" * 64))
    monkeypatch.setattr(recovery, "reconcile_locked", lambda *_a, **_kw: pytest.fail("wrong proof reached SQL"))
    if change == "copied_journal":
        path = copytree(ready.journal.directory, tmp_path / "copied-migration")
        ready.journal = migration.rolling.Journal(path, ready.plan)
    if change == "copied_bridge":
        path = copytree(ready.lane.journal.directory, tmp_path / "copied-bridge")
        ready.lane.journal = migration.bridge.bind(ready.lane.plan, path)
    with pytest.raises(migration.rolling.RollingDenied, match=reason):
        dispatch(ready, migration.INSPECT)


def test_inspection_does_not_create_missing_owner_lock(ready):
    lock = ready.journal.directory / "owner.lock"
    assert not lock.exists()
    with pytest.raises(FileNotFoundError):
        dispatch(ready, migration.INSPECT)
    assert not lock.exists()


@pytest.mark.parametrize("mode", [migration.INSPECT, migration.INSPECT_FUNDING])
def test_inspector_cli_initializes_without_cloud_or_bootstrap(ready, monkeypatch, tmp_path, capsys, mode):
    retain_inspection_proof(ready)
    root, bridge_plan = ready.lane.boundary.root, ready.lane.plan
    bridge_file, migration_file = tmp_path / "bridge-plan.json", tmp_path / "migration-plan.json"
    bridge_sha = migration.native._write_plan(bridge_file, bridge_plan)
    migration_sha = migration.native._write_plan(migration_file, ready.plan)

    def forbidden(*_args, **_kwargs):
        pytest.fail("inspector CLI reached a cloud command, Cloud Run client or write bootstrap")

    monkeypatch.setattr(migration.rolling.release.NativeBoundary, "_command", forbidden)
    monkeypatch.setattr(migration.native.successor.native, "NativeRun", forbidden)
    monkeypatch.setattr(migration.bootstrap, "engine", forbidden)
    monkeypatch.setattr(migration.native.successor, "controller_source", lambda _root: (
        bridge_plan.controller_release, bridge_plan.controller_source_sha256,
    ))
    monkeypatch.setattr(recovery, "reconcile_locked", lambda *_a, **_kw: recovery.CommitObservation(
        "schema26_observed_reconcile_only", migration.contract.BEFORE,
        ready.plan.catalog26_sha256, ready.plan.retained_acl_sha256, None,
    ))
    policy_args = []
    if mode == migration.INSPECT_FUNDING:
        policy_file = tmp_path / "financial-policy.json"
        now = datetime.now(UTC)
        policy_bytes = migration.rolling.canonical({
            "version": "owned-loss-test", "valid_from": (now - timedelta(hours=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "provenance": "synthetic_test", "evidence_sha256": "a" * 64,
        })
        policy_file.write_bytes(policy_bytes)
        policy_file.chmod(0o600)
        policy_sha = sha256(policy_bytes).hexdigest()
        policy_args = ["--funding-policy", str(policy_file), "--funding-policy-sha256", policy_sha]
        monkeypatch.setattr(migration, "_metadata", lambda _root: nullcontext())

        def funding_inspect(*_args, policy, **_options):
            assert policy.version == "owned-loss-test"
            return {"status": "continuity_funding_facts_inspected", "activation_granted": False}

        monkeypatch.setattr(recovery, "funding_facts_locked", funding_inspect)
    monkeypatch.setattr(sys, "argv", [
        "schema27_continuity_migration", mode,
        "--root", str(root), "--target-root", str(root), "--prior-root", str(root),
        "--bridge-plan", str(bridge_file), "--bridge-plan-sha256", bridge_sha,
        "--bridge-journal", str(ready.lane.journal.directory),
        "--plan", str(migration_file), "--plan-sha256", migration_sha, "--journal", str(ready.journal.directory),
    ] + policy_args)
    migration.main()
    summary = json.loads(capsys.readouterr().out)
    if mode == migration.INSPECT:
        assert summary["status"] == "continuity_migration_outcome_inspected"
        assert summary["migration_committed"] is summary["formal_reconciliation"] is False
    else:
        assert summary == {"status": "continuity_funding_facts_inspected", "activation_granted": False}
