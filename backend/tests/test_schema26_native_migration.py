"""Owned journal + explicit inert SQL/native ports; never native authorization."""

import importlib
from contextlib import contextmanager
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from test_schema26_maintenance_guard import baseline

m = importlib.import_module("schema26_native_migration")


def plan():
    resources = {
        (kind, name) for kind, names in m.app26.maintenance.RESOURCE_NAMES.items() for name in names
    }
    resources |= {("service", name) for name in m.app26.historical.EXTERNAL_SERVICES}
    return m.Migration26Plan(
        "a" * 32,
        m.app26.PriorSource(
            "b" * 40, "sha256:" + "b" * 64, "00000000-0000-0000-0000-000000000001", 1
        ),
        "c" * 64,
        tuple((name, "d" * 64) for name in m.PATHS),
        baseline(),
        "runtime:1",
        "observer:2",
        "retired:3",
        "e" * 40,
        tuple(m.app26.ResourcePin(kind, name, "a" * 64) for kind, name in sorted(resources)),
        "f" * 64,
        "a" * 64,
        (("synthetic-unknown", "b" * 64, 12345),),
        "c" * 64,
        "d" * 64,
        "e" * 64,
    )


class Result:
    def __init__(self, *, value=None, rows=()):
        self.value, self.rows = value, rows

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class Engine:
    def __init__(self, events):
        self.events, self.marker = events, m.contract.BEFORE
        self.commit_outcome = "known"
        self.disposed = False

    @contextmanager
    def connect(self):
        self.events.append("connect")
        yield self

    @contextmanager
    def begin(self):
        self.events.append("begin")
        try:
            yield self
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")
            if self.commit_outcome == "unknown":
                self.marker = m.contract.SCHEMA
                self.commit_outcome = "known"
                raise OSError("synthetic connection lost after commit")

    def exec_driver_sql(self, statement):
        self.events.append(statement)

    def execute(self, statement, params=None):
        sql = str(statement)
        self.events.append(sql)
        if "pg_try_advisory_xact_lock" in sql:
            return Result(value=True)
        if sql == "SELECT version_num FROM public.alembic_version LIMIT 2":
            return Result(rows=[self.marker])
        raise AssertionError("Unexpected SQL in inert source-only port")

    def dispose(self):
        self.disposed = True
        self.events.append("dispose")


@pytest.fixture
def owned(tmp_path, monkeypatch):
    captured = plan()
    captured.validate()
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(mode=0o700)
    journal = m.rolling.Journal(journal_dir, captured)
    events, ddls = [], []
    engine = Engine(events)
    final = SimpleNamespace(close=lambda: events.append("guard-close"))
    boundary = object.__new__(m.NativeMigration26Boundary)
    boundary.root = tmp_path
    boundary.verify = lambda _plan, *, schema: events.append("verify:" + schema)
    boundary.prepare_guard = lambda _plan: events.append("prepare-guard") or final
    boundary.bootstrap_engine = lambda: events.append("bootstrap-outside-sql") or engine
    boundary.source = lambda *_: events.append("verify-source")
    boundary.runtime_reference = lambda: captured.runtime_secret
    boundary.app = SimpleNamespace(
        observer=object(),
        _verify_maintenance_baseline=lambda b: events.append("closed-lineage"),
        _capture_maintenance_baseline=lambda: captured.maintenance_baseline,
    )
    monkeypatch.setattr(m, "operator_sources", lambda _: captured.operator_sources)
    monkeypatch.setattr(
        m.guard, "readonly_producer_drain", lambda _: events.append("producer-read")
    )

    class Reader:
        def observe(self):
            events.append("retired-read")
            return object()

        def close(self):
            events.append("retired-close")

    monkeypatch.setattr(m.guard.metadata, "ReadOnlyMaintenanceMetadata", Reader)
    monkeypatch.setattr(m.guard, "retirement_fingerprint", lambda *_: captured.retired_sha256)

    @contextmanager
    def metadata(_root, _observer, _runtime):
        events.append("metadata-enter")
        assert "connect" not in events
        yield
        events.append("metadata-exit")

    monkeypatch.setattr(m.metadata, "initialized_metadata", metadata)

    def upgrade(db, _identity, *, root, flags, maintenance_guard):
        assert db is engine and root == tmp_path and maintenance_guard is final
        assert all(v == "false" for v in flags.values())
        assert journal.read(m.MARKER + ".started") is not None
        ddls.append(True)
        events.append("supplied-step")
        engine.marker = m.contract.SCHEMA
        return {
            "committed": False,
            "transaction_ready_to_commit": True,
            "affected_sha256": "a" * 64,
            "schema_acl": [["synthetic", "acl"]],
            "source_sha256": "b" * 64,
            "acl_sha256": captured.acl_sha256,
        }

    monkeypatch.setattr(m.step, "upgrade_transaction", upgrade)
    monkeypatch.setattr(
        m.step.identity_step, "verify_identity", lambda *_: events.append("sql-identity")
    )
    monkeypatch.setattr(m.step.identity_step, "schema_acl", lambda *_: [("synthetic", "acl")])
    monkeypatch.setattr(m.step, "affected_acl", lambda *_: [("synthetic", "acl")])
    monkeypatch.setattr(m.step, "affected_digest", lambda *_a, **_k: "a" * 64)
    monkeypatch.setattr(
        m.step.financial,
        "verify_liability_pins",
        lambda _db, p: (
            events.append("liabilities-unchanged")
            if p == captured.protected_liabilities
            else pytest.fail("held pins lost")
        ),
    )
    monkeypatch.setattr(m.step.previous, "verify_shape", lambda *_: events.append("observed25"))
    monkeypatch.setattr(m.contract, "verify_source", lambda _: "b" * 64)
    monkeypatch.setattr(m.contract, "observe", lambda *_a, **_k: events.append("observed26"))
    original_sha = m.rolling.sha
    # The captured ACL is synthetic; retain every other real canonical digest.
    monkeypatch.setattr(
        m.rolling,
        "sha",
        lambda value: (
            captured.acl_sha256
            if type(value) is dict and set(value) == {"schema", "tables"}
            else original_sha(value)
        ),
    )
    return SimpleNamespace(
        plan=captured,
        journal=journal,
        boundary=boundary,
        engine=engine,
        events=events,
        ddls=ddls,
        final=final,
    )


def run(owned, **kwargs):
    return m.migration(owned.plan, owned.boundary, owned.journal, **kwargs)


def test_upgrade_initializes_metadata_outside_sql_then_one_ready_commit_and_readback(owned):
    result = run(owned)
    assert result["status"] == "schema26_committed_and_freshly_observed"
    assert result["api_admission"] == "closed"
    assert (
        result["replay_authorized"]
        is result["migration_replayed"]
        is result["goal_complete"]
        is False
    )
    assert owned.ddls == [True]
    assert owned.events.index("prepare-guard") < owned.events.index("bootstrap-outside-sql")
    assert owned.events.index("metadata-enter") < owned.events.index("connect")
    assert owned.events.index("supplied-step") < owned.events.index("commit")
    assert owned.journal.read(m.MARKER + ".transaction-ready")
    assert owned.journal.read(m.MARKER + ".commit-acknowledged")
    assert owned.journal.read(m.MARKER + ".checked")
    assert owned.engine.disposed and "guard-close" in owned.events
    assert any("SET LOCAL ROLE" in event for event in owned.events)
    assert owned.events.index("observed26") > next(
        i for i, v in enumerate(owned.events) if "SET LOCAL ROLE" in v
    )
    with pytest.raises(m.rolling.RollingDenied, match="already_observed_never_replay"):
        run(owned, reconcile=True)
    assert owned.ddls == [True]


def test_unknown_commit_retains_ready_then_reconcile_reads_only_and_never_replays(owned):
    owned.engine.commit_outcome = "unknown"
    with pytest.raises(OSError, match="lost after commit"):
        run(owned)
    assert owned.ddls == [True] and owned.journal.read(m.MARKER + ".transaction-ready")
    assert owned.journal.read(m.MARKER + ".commit-acknowledged") is None
    assert owned.journal.read(m.MARKER + ".checked") is None
    with pytest.raises(m.rolling.RollingDenied, match="started_reconcile_only"):
        run(owned)
    result = run(owned, reconcile=True)
    assert result["status"] == "schema26_committed_and_freshly_observed"
    assert (
        owned.ddls == [True] and "retired-read" in owned.events and "retired-close" in owned.events
    )


def test_unknown_rollback_observed25_is_only_a_read_and_grants_no_replay(owned):
    def refused(*_a, **_k):
        raise OSError("synthetic unknown before migration")

    owned.boundary.prepare_guard = lambda _: owned.final
    original = m.step.upgrade_transaction
    m.step.upgrade_transaction = refused
    try:
        with pytest.raises(OSError):
            run(owned)
    finally:
        m.step.upgrade_transaction = original
    assert owned.ddls == [] and owned.engine.marker == m.contract.BEFORE
    result = run(owned, reconcile=True)
    assert result["status"] == "schema25_observed_reconcile_only"
    assert result["replay_authorized"] is result["migration_replayed"] is False
    assert owned.ddls == [] and owned.journal.read(m.MARKER + ".checked") is None
    with pytest.raises(m.rolling.RollingDenied, match="started_reconcile_only"):
        run(owned)


@pytest.mark.parametrize("record", ["started", "transaction-ready", "commit-acknowledged"])
def test_durable_record_ack_loss_is_never_a_second_migration(owned, record):
    original = owned.journal.record

    def record_then_unknown(name, value):
        original(name, value)
        if name == m.MARKER + "." + record:
            raise OSError("synthetic local acknowledgement loss")

    owned.journal.record = record_then_unknown
    with pytest.raises(OSError):
        run(owned)
    owned.journal.record = original
    before = len(owned.ddls)
    with pytest.raises(m.rolling.RollingDenied, match="started_reconcile_only"):
        run(owned)
    result = run(owned, reconcile=True)
    assert len(owned.ddls) == before
    assert result["replay_authorized"] is False
    assert before == (0 if record == "started" else 1)


def test_final_guard_failure_closes_without_transaction_ready_or_alembic(owned, monkeypatch):
    def final_refusal(*_a, **_k):
        owned.events.append("actual-final-fence-refused")
        raise m.guard.MaintenanceDenied("synthetic_final_fence_refused")

    monkeypatch.setattr(m.step, "upgrade_transaction", final_refusal)
    with pytest.raises(m.guard.MaintenanceDenied):
        run(owned)
    assert owned.ddls == [] and "rollback" in owned.events
    assert owned.journal.read(m.MARKER + ".transaction-ready") is None
    assert owned.journal.read(m.MARKER + ".commit-acknowledged") is None
    assert owned.engine.disposed


def test_catalog26_without_same_transaction_ready_cannot_be_adopted(owned):
    original = owned.journal.record

    def lose_before_ready(name, value):
        if name.endswith("transaction-ready"):
            raise OSError("synthetic before local ready")
        original(name, value)

    owned.journal.record = lose_before_ready
    with pytest.raises(OSError):
        run(owned)
    owned.journal.record = original
    # Explicit hostile inert-port drift; not a qualified native schema marker.
    owned.engine.marker = m.contract.SCHEMA
    with pytest.raises(m.rolling.RollingDenied, match="same_transaction_ready_required"):
        run(owned, reconcile=True)
    assert owned.ddls == [True] and owned.journal.read(m.MARKER + ".checked") is None


def test_fresh_reconcile_and_foreign_types_refuse_before_any_bootstrap(owned):
    with pytest.raises(m.rolling.RollingDenied, match="started_reconcile_only"):
        run(owned, reconcile=True)
    assert owned.events == []
    with pytest.raises(m.rolling.RollingDenied, match="exact_owned_plan_required"):
        m.migration(owned.plan, SimpleNamespace(), owned.journal)
    assert owned.events == []


def test_plan_roundtrip_changed_legacy_and_noncanonical_inputs_refuse():
    value = plan()
    raw = m.rolling.canonical(asdict(value)) + b"\n"
    assert m.load_plan(raw) == value
    for changed in (
        replace(value, database_schema=m.contract.SCHEMA),
        replace(value, schema_contract="hirewiz_schema25"),
        replace(value, candidate=m.app26.PRIOR),
        replace(value, operator_sources=value.operator_sources[:-1]),
    ):
        with pytest.raises(m.rolling.RollingDenied):
            changed.validate()
    with pytest.raises(m.rolling.RollingDenied):
        m.load_plan(raw + b" ")


def test_plan_save_is_create_only_private_and_pinned(owned, tmp_path):
    tmp_path.chmod(0o700)
    target = tmp_path / "plan.json"
    digest = m.save_plan(target, owned.plan)
    assert digest == m.hashlib.sha256(target.read_bytes()).hexdigest()
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        m.save_plan(target, owned.plan)
    target.parent.chmod(0o755)
    with pytest.raises(m.rolling.RollingDenied, match="owned_private_plan_directory_required"):
        m.save_plan(tmp_path / "other.json", owned.plan)


def test_normal_sdk_bootstrap_precedes_boundary_and_failure_constructs_no_native_client(
    monkeypatch, tmp_path
):
    events = []
    monkeypatch.setattr(m.guard, "_native_client_types", lambda: events.append("application-sdk"))

    def boundary(root, **custody):
        assert root == tmp_path.resolve()
        assert custody == {
            "maintenance_plan_path": tmp_path / "maintenance.json",
            "maintenance_plan_sha256": "a" * 64,
            "maintenance_journal": tmp_path / "original-journal",
        }
        events.append("native-boundary")
        return object()

    monkeypatch.setattr(m.app26, "NativeApp26Boundary", boundary)
    options = {
        "maintenance_plan_path": tmp_path / "maintenance.json",
        "maintenance_plan_sha256": "a" * 64,
        "maintenance_journal": tmp_path / "original-journal",
    }
    m.NativeMigration26Boundary(tmp_path, **options)
    assert events == ["application-sdk", "native-boundary"]

    def unavailable():
        events.append("application-sdk-refused")
        raise ImportError("synthetic unavailable locked SDK")

    monkeypatch.setattr(m.guard, "_native_client_types", unavailable)
    with pytest.raises(ImportError):
        m.NativeMigration26Boundary(tmp_path, **options)
    assert events == ["application-sdk", "native-boundary", "application-sdk-refused"]
