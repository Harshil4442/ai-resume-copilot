"""The exact bridge journals authorize only one migration dispatch."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import asdict, replace
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


@pytest.mark.parametrize("fence_already_saved", [False, True])
def test_migration_requires_fresh_bridge_control_and_durable_intent(ready, monkeypatch, fence_already_saved):
    events = []
    receipt = "f" * 64
    lane = ready.lane
    if fence_already_saved:
        intent = ready.plan.intent(migration.bootstrap.migration_identity(lane.plan.database),
                                   migration.native.successor.native.host_auth.ACCOUNT)
        lane.journal.record(migration.scope.MIGRATION_FENCE, {
            "migration_plan_sha256": ready.journal.plan_hash, "intent_sha256": intent.sha256(),
        })
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
