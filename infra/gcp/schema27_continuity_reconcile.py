"""Read-only, exact-plan observation after the one-shot continuity migration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from infra.gcp import schema27_continuity_bridge_plan as bridge
from infra.gcp import schema27_continuity_catalog as catalog
from infra.gcp import schema27_continuity_scope as scope
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

# isort: split
from scripts import browser_schema19_step as identity_step
from scripts import notification_schema27_continuity_step as step
from scripts import notification_schema27_contract as contract
from scripts import notification_schema27_step as retained_step

rolling = scope.rolling
Purpose = scope.neon.Purpose
NeonIdentityDenied = scope.neon.retained.NeonIdentityDenied
STARTED = "schema27-continuity-migration.started"
COMMIT_ACK = "schema27-continuity-migration.commit-acknowledged"
CHECKED = "schema27-continuity-migration.checked"
SCHEMA_MISMATCH = "neon_continuity_exact_schema_mismatch"
TRANSACTION_PENDING = "neon_continuity_original_transaction_pending"
LiabilityPins = tuple[tuple[str, str, int], ...]


def _need(value, code: str) -> None:
    rolling.need(value, "schema27_continuity_reconcile_" + code)


def validate_custody(
    custody: scope.BridgeCustody, bridge_journal: rolling.Journal, migration_journal: rolling.Journal,
    identity: identity_step.MigrationIdentity, intent: step.MigrationIntent, *,
    bridge_plan_sha256: str, bridge_receipts_sha256: str,
) -> None:
    """Refuse a substituted plan, phase or observer target before credential access."""
    scope.verify_journal(custody, bridge_journal)
    _need(type(migration_journal) is rolling.Journal and migration_journal.directory != bridge_journal.directory
          and migration_journal.plan_hash != bridge_journal.plan_hash
          and bridge_plan_sha256 == bridge_journal.plan_hash, "journal_custody_mismatch")
    _need(bridge_journal.read(bridge.historical.ROLLBACK_INTENT) is None and
          all(bridge_journal.read("rollback." + service + ".started") is None
              for service in rolling.release.SERVICES), "bridge_rollback_started")
    _need(type(identity) is identity_step.MigrationIdentity
          and identity == catalog._identity(custody.database), "migration_identity_changed")
    identity.validate()
    _need(type(intent) is step.MigrationIntent and intent.plan_sha256 == migration_journal.plan_hash
          and intent.nonce == custody.nonce and intent.bridge_receipts_sha256 == bridge_receipts_sha256
          and intent.target_identity_sha256 == rolling.sha(asdict(identity)), "migration_intent_custody_mismatch")
    intent.validate(identity, intent.sha256())


@dataclass(frozen=True, slots=True)
class CommitObservation:
    status: Literal["schema26_observed_reconcile_only", "schema27_same_transaction_observed"]
    schema: str
    catalog_sha256: str
    retained_acl_sha256: str
    receipt_sha256: str | None

    @property
    def migration_committed(self) -> bool:
        return self.receipt_sha256 is not None

    def journal_payload(self) -> dict:
        return asdict(self) | {"migration_committed": self.migration_committed, "replay_authorized": False}

    def summary(self) -> dict:
        return self.journal_payload() | {
            "migration_replayed": False, "new_admission_enabled": False, "cloud_resource_mutations": 0,
        }


def _verify_database_owner(db: Connection, identity: identity_step.MigrationIdentity) -> None:
    row = db.execute(text(
        "SELECT d.oid,d.datdba,r.rolname FROM pg_catalog.pg_database d "
        "JOIN pg_catalog.pg_roles r ON r.oid=d.datdba WHERE d.datname=pg_catalog.current_database()"
    )).one()
    _need(tuple(row) == (identity.database_oid, identity.database_owner_oid, identity.bootstrap),
          "native_database_owner_changed")


def _audit(db: Connection, intent: step.MigrationIntent) -> list:
    return list(db.execute(text(
        "SELECT id,actor_user_id,actor_email,action,target_type,target_id,reason,"
        "CASE WHEN pg_catalog.octet_length(before_state::text) <= 4096 THEN before_state::text END,"
        "CASE WHEN pg_catalog.octet_length(after_state::text) <= 4096 THEN after_state::text END,correlation_id "
        "FROM public.admin_audit_events WHERE id=:id LIMIT 2"
    ), {"id": step.AUDIT_PREFIX + intent.nonce}).all())


def _same_json(raw: str, expected: dict) -> bool:
    if type(raw) is not str:
        return False
    try:
        return rolling.canonical(rolling.release._json(raw.encode())) == rolling.canonical(expected)
    except (rolling.release.ReleaseDenied, ValueError, RecursionError):
        return False


def _observe_before(
    db: Connection, identity: identity_step.MigrationIdentity, intent: step.MigrationIntent,
    expected_liabilities: LiabilityPins,
) -> CommitObservation:
    _verify_database_owner(db, identity)
    native = catalog.read_schema26(db, expected_liabilities=expected_liabilities)
    _need(native.catalog_sha256 == intent.catalog26_sha256
          and native.retained_acl_sha256 == intent.retained_acl_sha256, "before_catalog_or_acl_changed")
    _need(db.scalar(text("SELECT pg_catalog.to_regclass('public.notification_preferences')")) is None,
          "unexpected_projection")
    _need(not _audit(db, intent), "receipt_without_migration")
    return CommitObservation("schema26_observed_reconcile_only", contract.BEFORE, native.catalog_sha256,
                             native.retained_acl_sha256, None)


def _observe_after(
    db: Connection, identity: identity_step.MigrationIdentity, intent: step.MigrationIntent,
    expected_liabilities: LiabilityPins,
) -> CommitObservation:
    _verify_database_owner(db, identity)
    catalog._verify_migrator_session(db, identity)
    signature = contract.observe(db, identity)
    contract.before.financial.verify_liability_pins(db, expected_liabilities)
    acls = rolling.sha(retained_step.old_acls(db, identity))
    _need(acls == intent.retained_acl_sha256, "retained_acl_changed")
    expected = step.receipt(intent, signature)
    events = _audit(db, intent)
    _need(len(events) == 1, "same_transaction_receipt_required")
    event = events[0]
    _need(tuple(event[:7]) == (
        step.AUDIT_PREFIX + intent.nonce, None, intent.actor_email, step.ACTION, "database_schema",
        contract.SCHEMA, step.AUDIT_REASON,
    ) and _same_json(event[7], intent.snapshot()) and _same_json(event[8], expected)
        and event[9] == intent.nonce, "same_transaction_receipt_required")
    return CommitObservation("schema27_same_transaction_observed", contract.SCHEMA, signature, acls,
                             rolling.sha(expected))


def _started_records(migration_journal, intent):
    started = migration_journal.read(STARTED)
    ack = migration_journal.read(COMMIT_ACK)
    checked = migration_journal.read(CHECKED)
    _need(started is not None, "started_required")
    _need(rolling.canonical(started) == rolling.canonical(intent.snapshot()), "started_intent_changed")
    _need(ack is None or (set(ack) == {"plan_sha256", "receipt_sha256"} and scope._hash(ack["receipt_sha256"])),
          "commit_ack_changed")
    return ack, checked


def _verify_observation(migration_journal, observed, ack, checked):
    _need(ack is None or (observed.migration_committed and ack["receipt_sha256"] == observed.receipt_sha256),
          "commit_ack_changed")
    _need(checked is None or (observed.migration_committed and rolling.canonical(checked) == rolling.canonical(
        observed.journal_payload() | {"plan_sha256": migration_journal.plan_hash})), "checked_observation_changed")


def reconcile_locked(
    custody: scope.BridgeCustody, bridge_journal: rolling.Journal, migration_journal: rolling.Journal,
    identity: identity_step.MigrationIdentity, intent: step.MigrationIntent, *,
    bridge_plan_sha256: str, bridge_receipts_sha256: str, expected_liabilities: LiabilityPins,
) -> CommitObservation:
    """Called under the bridge then migration journal locks. Never replay SQL or write a journal."""
    validate_custody(custody, bridge_journal, migration_journal, identity, intent,
                     bridge_plan_sha256=bridge_plan_sha256, bridge_receipts_sha256=bridge_receipts_sha256)
    ack, checked = _started_records(migration_journal, intent)
    _need(type(expected_liabilities) is tuple, "liability_pins_invalid")
    entered_before = False
    try:
        with catalog.authenticated_observer(custody.database, custody.nonce, Purpose.BEFORE_SCHEMA27, recovery=True) as db:
            entered_before = True
            observed = _observe_before(db, identity, intent, expected_liabilities)
    except NeonIdentityDenied as error:
        if not entered_before and str(error) == TRANSACTION_PENDING:
            _need(False, "original_transaction_pending")
        _need(not entered_before and str(error) == SCHEMA_MISMATCH, "observer_unavailable")
        try:
            with catalog.authenticated_observer(
                custody.database, custody.nonce, Purpose.AFTER_SCHEMA27, recovery=True,
            ) as db:
                observed = _observe_after(db, identity, intent, expected_liabilities)
        except NeonIdentityDenied as after_error:
            if str(after_error) == TRANSACTION_PENDING:
                _need(False, "original_transaction_pending")
            _need(False, "observer_unavailable")
    _verify_observation(migration_journal, observed, ack, checked)
    return observed


def funding_facts_locked(
    custody: scope.BridgeCustody, bridge_journal: rolling.Journal, migration_journal: rolling.Journal,
    identity: identity_step.MigrationIdentity, intent: step.MigrationIntent, *,
    bridge_plan_sha256: str, bridge_receipts_sha256: str, expected_liabilities: LiabilityPins, policy,
) -> dict:
    """Inspect complete expense facts on the same authenticated snapshot as the migration receipt."""
    from app.domains.credits import finance, payment_expenses

    validate_custody(custody, bridge_journal, migration_journal, identity, intent,
                     bridge_plan_sha256=bridge_plan_sha256, bridge_receipts_sha256=bridge_receipts_sha256)
    ack, checked = _started_records(migration_journal, intent)
    _need(type(expected_liabilities) is tuple, "liability_pins_invalid")
    finance.require_current_policy(policy)
    entered = False
    try:
        with catalog.authenticated_observer(custody.database, custody.nonce, Purpose.AFTER_SCHEMA27, recovery=True) as db:
            entered = True
            observed = _observe_after(db, identity, intent, expected_liabilities)
            _need(observed.migration_committed, "same_transaction_receipt_required")
            _verify_observation(migration_journal, observed, ack, checked)
            with Session(bind=db, autoflush=False) as reader:
                snapshot = payment_expenses.collect_expenses(reader, policy=policy)
    except NeonIdentityDenied as error:
        if not entered and str(error) == TRANSACTION_PENDING:
            _need(False, "original_transaction_pending")
        if not entered and str(error) == SCHEMA_MISMATCH:
            _need(False, "funding_schema27_required")
        _need(False, "funding_observer_unavailable")
    finance.require_current_policy(policy)
    return {
        "status": "continuity_funding_facts_inspected", "schema": observed.schema,
        "migration_receipt_sha256": observed.receipt_sha256, "facts_sha256": snapshot.facts_sha256,
        "policy_sha256": rolling.sha(policy.model_dump(mode="json")),
        "total_components_count": len(snapshot.components), "unresolved_count": len(snapshot.unresolved),
        "bank_cash_independently_verified": False, "activation_granted": False,
        "replay_authorized": False, "cloud_resource_mutations": 0,
    }
