"""Owned journal and PostgreSQL coverage for the read-only migration recovery."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from secrets import token_urlsafe
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from test_mail_budget_postgres import owned_pg_url as owned_pg_url
from test_neon_continuity_migration_permissions_postgres import owned26 as owned26
from test_neon_continuity_migration_permissions_postgres import (
    set_only_bootstrap as set_only_bootstrap,
)
from test_notification_schema27_step_postgres import FLAGS, ROOT

from infra.gcp import schema27_continuity_reconcile as recovery

NONCE = "a" * 32
RECEIPTS = "b" * 64
CATALOG = "c" * 64
ACL = "d" * 64
RECEIPT = "e" * 64


@dataclass(frozen=True)
class SyntheticMigrationPlan:
    nonce: str
    target: str


@pytest.fixture
def custody(tmp_path):
    scope = recovery.scope
    binding = scope.BridgeCustody(NONCE, "f" * 64, scope.ContinuityDatabasePin("1" * 64))
    for name in ("bridge", "migration"):
        (tmp_path / name).mkdir(mode=0o700)
    bridge = scope.rolling.Journal(tmp_path / "bridge", binding)
    bridge.record(scope.JOURNAL_BINDING, binding.journal_binding())
    migration = scope.rolling.Journal(tmp_path / "migration", SyntheticMigrationPlan(NONCE, scope.neon.HOST))
    pin = binding.database
    identity = recovery.identity_step.MigrationIdentity(
        pin.cluster_sha256, pin.database_oid, recovery.catalog.BOOTSTRAP_OID, "public", 2200, scope.neon.NAMESPACE_OWNER_OID,
        scope.neon.DATABASE_OWNER, recovery.catalog.BOOTSTRAP_OID, *pin.migrator_role, *pin.runtime_role, *pin.observer_role,
    )
    intent = recovery.step.MigrationIntent(
        NONCE, "owned@example.invalid", migration.plan_hash, scope.rolling.sha(asdict(identity)),
        RECEIPTS, "2" * 64, CATALOG, ACL,
    )
    return SimpleNamespace(binding=binding, bridge=bridge, migration=migration, identity=identity, intent=intent)


def observe(case, *, bridge_plan_sha256=None, bridge_receipts_sha256=RECEIPTS):
    with case.bridge.locked(), case.migration.locked():
        return recovery.reconcile_locked(
            case.binding, case.bridge, case.migration, case.identity, case.intent,
            bridge_plan_sha256=bridge_plan_sha256 or case.bridge.plan_hash, bridge_receipts_sha256=bridge_receipts_sha256,
            expected_liabilities=(),
        )


@pytest.fixture
def started(custody):
    custody.migration.record(recovery.STARTED, custody.intent.snapshot())
    return custody


@pytest.mark.parametrize("change", ["no_start", "wrong_start", "start_false_numeric", "wrong_identity",
                                     "wrong_bridge", "wrong_receipts", "rollback", "ack_shape"])
def test_wrong_journal_intent_or_control_phase_has_no_observer(custody, monkeypatch, change):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid recovery opened an observer")

    monkeypatch.setattr(recovery.catalog, "authenticated_observer", forbidden)
    if change != "no_start":
        value = custody.intent.snapshot()
        if change == "wrong_start":
            value["catalog26_sha256"] = "0" * 64
        if change == "start_false_numeric":
            value["replay_authorized"] = 0
        custody.migration.record(recovery.STARTED, value)
    if change == "wrong_identity":
        custody.identity = replace(custody.identity, observer_oid=1234)
    if change == "rollback":
        custody.bridge.record(recovery.bridge.historical.ROLLBACK_INTENT, {"marker": "owned"})
    if change == "ack_shape":
        custody.migration.record(recovery.COMMIT_ACK, {"receipt_sha256": "not-a-digest"})
    with pytest.raises(recovery.rolling.RollingDenied):
        observe(custody, bridge_plan_sha256=CATALOG if change == "wrong_bridge" else None,
                bridge_receipts_sha256=ACL if change == "wrong_receipts" else RECEIPTS)


def install_observer(monkeypatch, case, *, before_error=None, after_error=None, before_body_error=False):
    seen = []
    sql = object()

    @contextmanager
    def native(pin, nonce, purpose, **options):
        assert options == {"recovery": True}
        assert pin is case.binding.database and nonce == case.binding.nonce
        seen.append(purpose)
        if purpose is recovery.Purpose.BEFORE_SCHEMA27 and before_error:
            raise recovery.NeonIdentityDenied(before_error)
        if purpose is recovery.Purpose.AFTER_SCHEMA27 and after_error:
            raise recovery.NeonIdentityDenied(after_error)
        yield sql

    before = recovery.CommitObservation("schema26_observed_reconcile_only", recovery.contract.BEFORE, CATALOG, ACL, None)
    after = recovery.CommitObservation("schema27_same_transaction_observed", recovery.contract.SCHEMA, CATALOG, ACL, RECEIPT)

    def before_view(db, identity, intent, expected_liabilities):
        assert db is sql and identity is case.identity and intent is case.intent and expected_liabilities == ()
        if before_body_error:
            raise recovery.NeonIdentityDenied(recovery.SCHEMA_MISMATCH)
        return before

    def after_view(db, identity, intent, expected_liabilities):
        assert db is sql and identity is case.identity and intent is case.intent and expected_liabilities == ()
        return after

    monkeypatch.setattr(recovery.catalog, "authenticated_observer", native)
    monkeypatch.setattr(recovery, "_observe_before", before_view)
    monkeypatch.setattr(recovery, "_observe_after", after_view)
    return seen


def test_verified_26_never_creates_an_ack_or_enables_replay(started, monkeypatch):
    seen = install_observer(monkeypatch, started)
    result = observe(started)
    assert seen == [recovery.Purpose.BEFORE_SCHEMA27]
    assert result.schema == recovery.contract.BEFORE and not result.migration_committed
    assert result.summary()["replay_authorized"] is result.summary()["migration_replayed"] is False
    assert started.migration.read(recovery.COMMIT_ACK) is started.migration.read(recovery.CHECKED) is None
    started.migration.record(recovery.COMMIT_ACK, {"receipt_sha256": RECEIPT})
    with pytest.raises(recovery.rolling.RollingDenied, match="commit_ack_changed"):
        observe(started)


@pytest.mark.parametrize(("reason", "expected"), [
    ("neon_continuity_observation_unavailable", "observer_unavailable"),
    ("neon_continuity_catalog_target_mismatch", "observer_unavailable"),
    (recovery.TRANSACTION_PENDING, "original_transaction_pending"),
    (None, "observer_unavailable"),
])
def test_unauthenticated_inconclusive_or_in_body_errors_never_probe_schema27(started, monkeypatch, reason, expected):
    seen = install_observer(monkeypatch, started, before_error=reason, before_body_error=reason is None)
    with pytest.raises(recovery.rolling.RollingDenied, match=expected):
        observe(started)
    assert seen == [recovery.Purpose.BEFORE_SCHEMA27]


def test_exact_marker_refusal_uses_new_27_connection_and_matches_durable_ack(started, monkeypatch):
    seen = install_observer(monkeypatch, started, before_error=recovery.SCHEMA_MISMATCH)
    result = observe(started)
    assert seen == [recovery.Purpose.BEFORE_SCHEMA27, recovery.Purpose.AFTER_SCHEMA27]
    assert result.schema == recovery.contract.SCHEMA and result.migration_committed
    assert started.migration.read(recovery.CHECKED) is None
    started.migration.record(recovery.COMMIT_ACK, {"receipt_sha256": RECEIPT})
    started.migration.record(recovery.CHECKED, result.journal_payload())
    assert observe(started) == result


def test_after_marker_refusal_stays_unavailable(started, monkeypatch):
    seen = install_observer(monkeypatch, started, before_error=recovery.SCHEMA_MISMATCH, after_error=recovery.SCHEMA_MISMATCH)
    with pytest.raises(recovery.rolling.RollingDenied, match="observer_unavailable"):
        observe(started)
    assert seen == [recovery.Purpose.BEFORE_SCHEMA27, recovery.Purpose.AFTER_SCHEMA27]


def test_exact_27_cannot_replace_known_ack_or_mismatched_checked(started, monkeypatch):
    install_observer(monkeypatch, started, before_error=recovery.SCHEMA_MISMATCH)
    started.migration.record(recovery.COMMIT_ACK, {"receipt_sha256": RECEIPT})
    started.migration.record(recovery.CHECKED, observe(started).journal_payload() | {"replay_authorized": 0})
    with pytest.raises(recovery.rolling.RollingDenied, match="checked_observation_changed"):
        observe(started)


def test_sql_receipt_json_requires_exact_types_and_unique_keys():
    assert recovery._same_json('{"replay_authorized":false}', {"replay_authorized": False})
    assert not recovery._same_json('{"replay_authorized":0}', {"replay_authorized": False})
    assert not recovery._same_json('{"replay_authorized":true,"replay_authorized":false}', {"replay_authorized": False})


def test_owned_postgres_readback_requires_same_transaction_detached_audit(owned26, set_only_bootstrap):
    identity = set_only_bootstrap
    engine, step, contract = owned26.engine, recovery.step, recovery.contract
    password = token_urlsafe(24)
    with engine.begin() as admin:
        admin.exec_driver_sql(f'ALTER ROLE "{identity.observer}" PASSWORD \'{password}\'')
        admin.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{identity.observer}"')
        intent = step.MigrationIntent(
            NONCE, "owned@example.invalid", "1" * 64, recovery.rolling.sha(asdict(identity)),
            RECEIPTS, contract.verify_source(ROOT), contract.before.observe(admin),
            recovery.rolling.sha(recovery.retained_step.old_acls(admin, identity)),
        )
        liabilities = contract.before.financial.liability_pins(admin)
    with engine.connect() as writer:
        writer.exec_driver_sql(f'SET SESSION AUTHORIZATION "{identity.bootstrap}"')
        writer.commit()
        with writer.begin():
            result = step.upgrade_transaction(writer, identity, intent, root=ROOT, flags=FLAGS,
                                              journal_intent_sha256=intent.sha256())

    def read_after():
        observer_engine = create_engine(engine.url.set(username=identity.observer, password=password), poolclass=NullPool, hide_parameters=True)
        try:
            with observer_engine.begin() as observer:
                observer.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return recovery._observe_after(observer, identity, intent, liabilities)
        finally:
            observer_engine.dispose()

    observed = read_after()
    assert observed.migration_committed and observed.receipt_sha256 == result["receipt_sha256"]
    with engine.begin() as admin:
        admin.execute(text("UPDATE public.admin_audit_events SET after_state=jsonb_set(after_state::jsonb, "
                           "'{replay_authorized}', '0'::jsonb)::json WHERE id=:id"),
                      {"id": step.AUDIT_PREFIX + intent.nonce})
    with pytest.raises(recovery.rolling.RollingDenied, match="same_transaction_receipt_required"):
        read_after()
