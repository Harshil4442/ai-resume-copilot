"""Owned native-shaped metadata and real localPG, never cloud qualification."""
from __future__ import annotations

import hashlib
import importlib
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from test_mail_budget_postgres import owned_pg_url as owned_pg_url
from test_notification_schema27_step_postgres import owned26 as owned26
from test_schema26_app_successor import context as context

from scripts import neon_direct_identity as neon

m = importlib.import_module("schema27_native_migration")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def prepared(context, tmp_path):
    """The existing public owned fixture runs the REAL stock journal algorithm.

    Its native ports are synthetic. These cases are protocol/SQL evidence, not
    authentication, source qualification, real GCP readiness or release grants.
    """
    c = context
    m.successor.execute("advance", c.plan, c.boundary, c.journal)
    source = m.successor.SourcePin(c.plan.release, c.plan.image_digest, c.plan.build_id, c.plan.ci_run_id)
    pins = tuple(c.boundary._pin(name, source) for name in m.rolling.release.SERVICES)
    plan = m.Migration27Plan("d" * 32, c.plan, m.bridge_receipts(c.plan, c.journal),
                             "e" * 64, pins, "f" * 64, "a" * 64)
    plan.validate()
    boundary = object.__new__(m.NativeMigration27Boundary)
    boundary.root, boundary.bridge, boundary.bridge_plan = ROOT, c.boundary, c.plan
    boundary.bridge_journal = c.journal
    events = []
    boundary.sources = lambda _: events.append("sources")
    directory = tmp_path / "migration"
    directory.mkdir(mode=0o700)
    journal = m.rolling.Journal(directory, plan)
    return SimpleNamespace(c=c, plan=plan, boundary=boundary, journal=journal, events=events)


def test_native_factory_keeps_closed_stock_types_and_exact_target_observer(monkeypatch):
    # Constructors build ports only; all requests remain forbidden here.
    old = m.successor.NativeSuccessorBoundary(ROOT, ROOT, ROOT)
    monkeypatch.setattr(m.successor, "NativeSuccessorBoundary", lambda *_: old)
    result = m.native_bridge(ROOT, ROOT, ROOT)
    assert result is old and type(result.observer) is m.Source27BridgeBoundary
    assert type(result.prior_observer) is m.app26.Source26Boundary


@pytest.mark.parametrize("chain", [
    (*m.app26.initial.CHAIN, m.contract.BEFORE),
    (*m.app26.initial.CHAIN, m.contract.BEFORE, m.contract.SCHEMA, "future"),
    (*m.app26.initial.CHAIN, m.contract.SCHEMA),
])
def test_new_bridge_source_never_accepts_old_or_unknown_chain(monkeypatch, tmp_path, chain):
    source = m.Source27BridgeBoundary(tmp_path)
    monkeypatch.setattr(m.rolling.release.source_guard, "checked_files", lambda *_: {})
    monkeypatch.setattr(m.rolling.release.source_guard, "migration_chain", lambda *_a, **_k: chain)
    source._verify_source_image_provenance = lambda **_: pytest.fail("unqualified image provenance")
    with pytest.raises(m.rolling.RollingDenied, match="exact_bridge_chain"):
        source.verify_source_image(release="a" * 40, image_digest="sha256:" + "b" * 64, build_id="c", ci_run_id=1)


def test_exact_bridge_chain_requires_pinned_baked_off_image_then_original_provenance(monkeypatch, tmp_path):
    source = m.Source27BridgeBoundary(tmp_path)
    dockerfile = ROOT / "Dockerfile"
    assert hashlib.sha256(dockerfile.read_bytes()).hexdigest() == m.BRIDGE_DOCKERFILE_SHA256
    (tmp_path / "Dockerfile").write_bytes(dockerfile.read_bytes())
    monkeypatch.setattr(m.rolling.release.source_guard, "checked_files", lambda *_: {})
    monkeypatch.setattr(m.rolling.release.source_guard, "migration_chain",
                        lambda *_a, **_k: (*m.app26.initial.CHAIN, m.contract.BEFORE, m.contract.SCHEMA))
    checked = []
    monkeypatch.setattr(m.contract, "verify_source", lambda root: checked.append(root))
    source._verify_source_image_provenance = lambda **kwargs: checked.append(kwargs)
    args = dict(release="a" * 40, image_digest="sha256:" + "b" * 64, build_id="c", ci_run_id=1)
    source.verify_source_image(**args)
    assert checked == [tmp_path, args]
    (tmp_path / "Dockerfile").write_bytes(dockerfile.read_bytes() + b"\nENV NOTIFICATION_DIGESTS_ENABLED=true\n")
    with pytest.raises(m.rolling.RollingDenied, match="baked_off_image_source_changed"):
        source.verify_source_image(**args)
    assert len(checked) == 3  # source checked again; provenance never reached


def test_live_recheck_uses_metadata_health_not_old_schema26_database(prepared):
    p = prepared
    p.boundary.bridge._database = lambda *_a, **_k: pytest.fail("old26 SQL verifier called afterDDL")
    p.boundary.verify_live(p.plan)
    assert p.events == ["sources"]
    assert p.c.fixture.health_checks  # stock native readback includes exact HTTP health


@pytest.mark.parametrize("kind", ["flag", "secret_flag", "new_alias", "generation", "not_done", "iam"])
def test_live_native_drift_or_unknown_operation_denies(prepared, kind):
    p = prepared
    service = p.plan.services[0].service
    current = p.c.fixture.states[service]
    if kind == "flag":
        current["template"]["containers"][0]["env"].append({"name": m.F07_FLAGS[0], "value": "true"})
    elif kind == "secret_flag":
        current["template"]["containers"][0]["env"].append({"name": m.F07_FLAGS[1], "valueSource": {
            "secretKeyRef": {"secret": "not-authority", "version": "1"}}})
    elif kind == "new_alias":
        current["trafficStatuses"].append({"revision": p.c.plan.services[0].prior_revision, "tag": "old-reachable"})
    elif kind == "generation":
        current["generation"] = int(current["generation"]) + 1
        current["observedGeneration"] = current["generation"]
    elif kind == "not_done":
        original = p.c.boundary.operation
        p.c.boundary.operation = lambda name: original(name) | {"done": False}
    else:
        p.c.boundary.iam = lambda _: {"bindings": [], "etag": "changed"}
    with pytest.raises(m.rolling.RollingDenied):
        p.boundary.verify_live(p.plan)


def test_plan_roundtrip_exact_nested_types_and_wrong_journal_owner_refused(prepared, tmp_path):
    p = prepared
    assert m.load(m.rolling.canonical(asdict(p.plan))) == p.plan
    p.boundary.bridge_journal = p.journal
    with pytest.raises(m.rolling.RollingDenied, match="bridge_custody"):
        p.boundary.verify_live(p.plan)


def test_orphan_commit_record_never_admits_a_new_transaction(prepared):
    p = prepared
    p.journal.record(m.MARKER + ".commit-acknowledged", {"receipt_sha256": "a" * 64})
    p.boundary.verify_live = lambda _: pytest.fail("orphan must fail before admission")
    with pytest.raises(m.rolling.RollingDenied, match="orphan_transaction_record"):
        m.migration(p.plan, p.boundary, p.journal)


@pytest.mark.parametrize("drift", ["missing_both_request_hashes", "corrupt_checked"])
def test_bridge_custody_requires_exact_original_action_ack_and_checked_shapes(prepared, drift):
    p = prepared
    prefix = "stage." + p.plan.bridge.services[0].service
    journal = p.boundary.bridge_journal
    if drift == "missing_both_request_hashes":
        for suffix in ("started", "ack"):
            row = journal.read(prefix + "." + suffix)
            del row["request_sha256"]
            (journal.directory / (prefix + "." + suffix + ".json")).write_bytes(m.rolling.canonical(row) + b"\n")
        expected = "bridge_started_shape"
    else:
        row = journal.read(prefix + ".checked")
        row["operation_ack_retained"] = False
        (journal.directory / (prefix + ".checked.json")).write_bytes(m.rolling.canonical(row) + b"\n")
        expected = "bridge_checked_shape"
    with pytest.raises(m.rolling.RollingDenied, match=expected):
        m.bridge_receipts(p.plan.bridge, journal)


def test_bridge_capture_config_contains_only_typed_source_and_numeric_refs(prepared):
    p = prepared.plan.bridge
    args = {"source": asdict(m.successor.SourcePin(p.release, p.image_digest, p.build_id, p.ci_run_id)),
            "prior": asdict(p.prior_source), "options": {"controller_ci_run_id": p.controller_ci_run_id,
            "nonce": "a" * 32, "observer_secret": p.observer_secret,
            "retired_migration_secret": p.retired_migration_secret, "workers_release": p.workers_release}}
    source, prior, options = m.load_capture_config(m.rolling.canonical(args))
    assert source.release == p.release and prior == p.prior_source
    assert options == args["options"]
    args["options"]["transport"] = "caller-selected-plugin"
    with pytest.raises(m.rolling.RollingDenied, match="capture_options"):
        m.load_capture_config(m.rolling.canonical(args))
    del args["options"]["transport"]
    args["options"]["observer_secret"] = "postgresql://not-a-reference"
    with pytest.raises(m.rolling.RollingDenied, match="numeric_capture_reference"):
        m.load_capture_config(m.rolling.canonical(args))


@pytest.fixture
def sql_owned(prepared, owned26, monkeypatch):
    p, c = prepared, owned26
    monkeypatch.setattr(m.app26, "bootstrap_identity", lambda: c.identity)
    with c.engine.connect() as db:
        signature = m.contract.before.observe(db)
        acls = m.rolling.sha(m.step.old_acls(db, c.identity))
    p.plan = replace(p.plan, source_sha256=m.contract.verify_source(ROOT),
                     catalog26_sha256=signature, retained_acl_sha256=acls)
    p.journal = m.rolling.Journal(p.journal.directory, p.plan)
    p.boundary.verify_live = lambda _: p.events.append("live")
    p.boundary.observe = lambda schema: (signature, acls)
    p.boundary.metadata = nullcontext
    p.boundary.bootstrap_engine = lambda: c.engine
    p.boundary._neon = lambda: neon

    @contextmanager
    def readonly(schema):
        with c.engine.connect() as db, db.begin():
            db.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            marker = db.scalar(text("SELECT version_num FROM public.alembic_version"))
            if marker != schema:
                raise neon.NeonIdentityDenied("neon_release_exact_schema_mismatch")
            yield db
    p.boundary.readonly = readonly
    p.sql = c
    return p


def test_real_pg_one_shot_ddl_detached_receipt_and_readonly_reconciliation(sql_owned):
    p = sql_owned
    result = m.migration(p.plan, p.boundary, p.journal)
    assert result["migration_committed"] and result["new_admission_enabled"] is False
    assert result["migration_replayed"] is result["replay_authorized"] is False
    with p.sql.engine.connect() as db:
        assert db.scalar(text("SELECT actor_user_id FROM admin_audit_events WHERE id=:id"),
            {"id": m.MARKER + "-" + p.plan.nonce}) is None
        assert db.scalar(text("SELECT count(*) FROM notification_preferences")) == 0
        assert db.scalar(text("SELECT ai_credits FROM users WHERE id=1")) == 17
    again = m.migration(p.plan, p.boundary, p.journal, reconcile=True)
    assert again == result
    with pytest.raises(m.rolling.RollingDenied, match="started_reconcile_only"):
        m.migration(p.plan, p.boundary, p.journal)


class LostCommitEngine:
    """Actual PG commit, then lost client acknowledgement, never a fake row."""
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def connect(self):
        with self.engine.connect() as db:
            original_begin = db.begin
            @contextmanager
            def lost_begin():
                with original_begin():
                    yield
                raise OSError("owned lost commit acknowledgement")
            # Preserve the actual SQLAlchemy Connection and dialect identity;
            # only the caller's post-commit ACK is lost after real PG commit.
            db.begin = lost_begin
            yield db

    def dispose(self):
        self.engine.dispose()


def test_real_pg_lost_commit_ack_joins_only_exact_same_transaction_never_ddl_again(sql_owned, monkeypatch):
    p = sql_owned
    p.boundary.bootstrap_engine = lambda: LostCommitEngine(p.sql.engine)
    with pytest.raises(OSError, match="owned lost commit"):
        m.migration(p.plan, p.boundary, p.journal)
    assert p.journal.read(m.MARKER + ".started") and p.journal.read(m.MARKER + ".commit-acknowledged") is None
    monkeypatch.setattr(m.step, "upgrade_transaction", lambda *_a, **_k: pytest.fail("DDL replay"))
    result = m.migration(p.plan, p.boundary, p.journal, reconcile=True)
    assert result["migration_committed"] and not result["migration_replayed"]


def test_real_pg_before_commit_failure_stays26_and_intent_never_reauthorizes(sql_owned, monkeypatch):
    p = sql_owned
    def refuse(*_):
        raise RuntimeError("owned receipt failure before commit")
    monkeypatch.setattr(m, "write_receipt", refuse)
    with pytest.raises(RuntimeError, match="owned receipt failure"):
        m.migration(p.plan, p.boundary, p.journal)
    with p.sql.engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == m.contract.BEFORE
        assert db.scalar(text("SELECT to_regclass('public.notification_preferences')")) is None
        assert db.scalar(text("SELECT count(*) FROM admin_audit_events")) == 0
    monkeypatch.setattr(m.step, "upgrade_transaction", lambda *_a, **_k: pytest.fail("DDL replay"))
    result = m.migration(p.plan, p.boundary, p.journal, reconcile=True)
    assert result["status"] == "schema26_observed_reconcile_only" and not result["migration_committed"]


@pytest.mark.parametrize("mutation", ["missing", "wrong_intent", "wrong_plan", "wrong_catalog", "wrong_actor"])
def test_real_pg27_without_exact_detached_receipt_cannot_adopt_other_commit(sql_owned, mutation):
    p = sql_owned
    m.migration(p.plan, p.boundary, p.journal)
    with p.sql.engine.connect() as db, db.begin():
        if mutation == "missing":
            db.execute(text("DELETE FROM admin_audit_events"))
        elif mutation == "wrong_actor":
            db.execute(text("UPDATE admin_audit_events SET actor_email='other@example.invalid'"))
        else:
            key = {"wrong_intent": "intent_sha256", "wrong_plan": "plan_sha256", "wrong_catalog": "catalog_sha256"}[mutation]
            db.execute(text("UPDATE admin_audit_events SET after_state=jsonb_set(after_state::jsonb,"
                "ARRAY[CAST(:key AS text)],CAST(:value AS jsonb))::json"), {"key": key, "value": '"' + "0" * 64 + '"'})
    with pytest.raises(m.rolling.RollingDenied, match="same_transaction_receipt_required"):
        m.migration(p.plan, p.boundary, p.journal, reconcile=True)


def test_unacknowledged_durable_intent_never_opens_sql_write_connection(sql_owned, monkeypatch):
    p = sql_owned
    original = p.journal.record
    def lost(name, value):
        original(name, value)
        raise OSError("owned durable intent ack lost")
    monkeypatch.setattr(p.journal, "record", lost)
    # Initialization is before intent, but the first SQL write connection must
    # only be entered after acknowledged durable record.
    class NoSql:
        def connect(self):
            pytest.fail("SQL opened after unknown journal ACK")
        def dispose(self):
            pass
    p.boundary.bootstrap_engine = NoSql
    with pytest.raises(OSError, match="durable intent ack lost"):
        m.migration(p.plan, p.boundary, p.journal)
    assert p.journal.read(m.MARKER + ".started")


def test_native_preflight_failure_is_before_intent_or_sql(sql_owned):
    p = sql_owned
    p.boundary.verify_live = lambda _: m.need(False, "owned_native_off_refusal")
    p.boundary.bootstrap_engine = lambda: pytest.fail("unattested SQL engine")
    with pytest.raises(m.rolling.RollingDenied, match="owned_native_off_refusal"):
        m.migration(p.plan, p.boundary, p.journal)
    assert p.journal.read(m.MARKER + ".started") is None


def restore_owned26(p):
    """Actual owned fixture restoration, not merely a forged marker label."""
    with p.sql.engine.connect() as db, db.begin():
        db.exec_driver_sql("DROP TABLE public.notification_preferences")
        db.execute(text("UPDATE public.alembic_version SET version_num=:schema"), {"schema": m.contract.BEFORE})
        db.execute(text("DELETE FROM public.admin_audit_events WHERE id=:id"),
                   {"id": m.MARKER + "-" + p.plan.nonce})
        assert m.contract.before.observe(db) == p.plan.catalog26_sha256


@pytest.mark.parametrize("drift", ["ack_receipt", "known_ack_restored26", "checked_record"])
def test_real_pg_journal_ack_checked_or_restored_history_cannot_be_rewritten(sql_owned, drift):
    p = sql_owned
    m.migration(p.plan, p.boundary, p.journal)
    expected = "commit_ack_changed"
    if drift == "known_ack_restored26":
        restore_owned26(p)
    else:
        suffix = ".commit-acknowledged" if drift == "ack_receipt" else ".checked"
        row = p.journal.read(m.MARKER + suffix)
        if drift == "ack_receipt":
            row["receipt_sha256"] = "0" * 64
        else:
            row["status"] = "owned-altered-checked-record"
            expected = "checked_observation_changed"
        (p.journal.directory / (m.MARKER + suffix + ".json")).write_bytes(m.rolling.canonical(row) + b"\n")
    with pytest.raises(m.rolling.RollingDenied, match=expected):
        m.migration(p.plan, p.boundary, p.journal, reconcile=True)


def test_real_pg_current_invocation_known_commit_ack_then26_restore_is_contradiction(sql_owned, monkeypatch):
    p = sql_owned
    original_record = p.journal.record
    def restored_after_ack(name, value):
        original_record(name, value)
        if name == m.MARKER + ".commit-acknowledged":
            restore_owned26(p)
    monkeypatch.setattr(p.journal, "record", restored_after_ack)
    with pytest.raises(m.rolling.RollingDenied, match="commit_ack_changed"):
        m.migration(p.plan, p.boundary, p.journal)
    assert p.journal.read(m.MARKER + ".commit-acknowledged")
    assert p.journal.read(m.MARKER + ".checked") is None
