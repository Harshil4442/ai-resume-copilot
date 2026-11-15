"""Capture, execute once, reconcile, or inspect the schema27 continuity migration."""
from __future__ import annotations

import argparse
import fcntl
import importlib
import json
import os
import re
import stat
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from infra.gcp import schema27_continuity_bootstrap as bootstrap
from infra.gcp import schema27_continuity_bridge as native
from infra.gcp import schema27_continuity_bridge_plan as bridge
from infra.gcp import schema27_continuity_catalog as catalog
from infra.gcp import schema27_continuity_runtime as runtime
from infra.gcp import schema27_continuity_scope as scope
from sqlalchemy import event

# isort: split
from scripts import notification_schema27_continuity_step as step
from scripts import notification_schema27_contract as contract

rolling = scope.rolling
SCHEMA_KIND = "hirewiz_schema27_continuity_migration_v1"
METADATA_URL = "postgresql+psycopg://migration_metadata@127.0.0.1:1/metadata"
INSPECT = "inspect-migration-outcome"
INSPECT_FUNDING = "inspect-funding-facts"


def need(value, reason):
    rolling.need(value, "schema27_continuity_migration_" + reason)


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    nonce: str
    bridge_plan_sha256: str
    bridge_receipts_sha256: str
    source_sha256: str
    catalog26_sha256: str
    retained_acl_sha256: str
    services: tuple[rolling.ServicePin, ...]
    kind: str = SCHEMA_KIND

    def validate(self, bridge_plan, bridge_journal):
        scope.verify_journal(bridge_plan.custody(), bridge_journal)
        need(type(self) is MigrationPlan and self.kind == SCHEMA_KIND and scope._nonce(self.nonce)
             and self.nonce == bridge_plan.nonce
             and self.bridge_plan_sha256 == bridge_journal.plan_hash
             and self.catalog26_sha256 == bridge_plan.financial_sha256
             and all(scope._hash(value) for value in
                     (self.bridge_receipts_sha256, self.source_sha256, self.retained_acl_sha256)),
             "plan_identity")
        need(type(self.services) is tuple and all(type(pin) is rolling.ServicePin for pin in self.services)
             and tuple(pin.service for pin in self.services) == rolling.release.SERVICES
             and all(pin.prior_release == bridge_plan.release
                     and pin.prior_revision == bridge_plan.revision(pin.service) for pin in self.services),
             "service_pins")

    def intent(self, identity, actor_email):
        return step.MigrationIntent(
            self.nonce, actor_email, rolling.sha(asdict(self)), rolling.sha(asdict(identity)),
            self.bridge_receipts_sha256, self.source_sha256, self.catalog26_sha256, self.retained_acl_sha256,
        )


def _pins(boundary, bridge_plan):
    return tuple(boundary._pin(name, bridge_plan.source) for name in rolling.release.SERVICES)


def _verify_live(boundary, bridge_plan, bridge_journal, plan):
    # Reconciliation can run on schema27, so recheck cloud and source without
    # the bridge's exact26 SQL port. SQL recovery uses its own authenticated read.
    plan.validate(bridge_plan, bridge_journal)
    boundary._sources(bridge_plan)
    need(contract.verify_source(boundary.root) == plan.source_sha256, "migration_source")
    boundary.observer.verify_policy_configuration(bridge_plan.target_policies())
    boundary._identity(bridge_plan.runtime_uid)
    need(boundary._resources(bridge_plan.workers_release) == bridge_plan.resources, "resources")
    need(native.promoted_receipts(bridge_plan, bridge_journal) == plan.bridge_receipts_sha256, "bridge_receipts")
    need(_pins(boundary, bridge_plan) == plan.services, "services_changed")
    for pin in plan.services:
        original = bridge_plan.pin(pin.service)
        prefix = f"promote.{pin.service}"
        for phase in ("stage", "promote"):
            ack = bridge_journal.read(f"{phase}.{pin.service}.ack")
            response = boundary.operation(ack["operation"])
            need(response.get("name") == ack["operation"] and response.get("done") is True
                 and "error" not in response, "operation_unverified")
        rolling._readback(bridge_plan, boundary, original, bridge_journal.read(prefix + ".started"))
        boundary._verify_service(bridge_plan, original)
        current = boundary.service(pin.service)
        revisions = {row["revision"] for row in rolling.pinned_traffic(pin.service, current)}
        need(revisions == {pin.prior_revision}, "unexpected_reachable_revision")


def capture(boundary, bridge_plan, bridge_journal):
    need(type(boundary) is native.NativeContinuityBoundary, "capture_inputs")
    scope.verify_journal(bridge_plan.custody(), bridge_journal)
    boundary.journal = bridge_journal
    with scope.lifecycle_lock(bridge_journal), bridge_journal.locked():
        need(bridge_journal.read(scope.MIGRATION_FENCE) is None, "migration_started")
        boundary.verify_global(bridge_plan)
        receipts = native.promoted_receipts(bridge_plan, bridge_journal)
        before = catalog.observe_schema26(bridge_plan.custody(), bridge_journal,
                                          expected_liabilities=bridge_plan.protected_liabilities)
        plan = MigrationPlan(bridge_plan.nonce, bridge_journal.plan_hash, receipts, contract.verify_source(boundary.root),
                             before.catalog_sha256, before.retained_acl_sha256, _pins(boundary, bridge_plan))
        _verify_live(boundary, bridge_plan, bridge_journal, plan)
        return plan


@contextmanager
def _metadata(root):
    # Metadata needs a PostgreSQL dialect. The only real SQL connection is
    # the independently authenticated bootstrap passed directly to Alembic.
    need(not any(name in sys.modules for name in (
        "app.database", "app.models", "backend.app.database", "backend.app.models")),
        "fresh_metadata_process")
    previous = {name: os.environ.get(name) for name in ("DATABASE_URL", "APP_ENV")}
    database = None

    def refuse(*_args, **_kwargs):
        need(False, "metadata_connection_forbidden")

    try:
        os.environ.update(DATABASE_URL=METADATA_URL, APP_ENV="production")
        database = importlib.import_module("app.database")
        need(Path(database.__file__).resolve() == root / "backend/app/database.py"
             and database.DATABASE_URL == METADATA_URL, "metadata_origin")
        event.listen(database.engine, "do_connect", refuse)
        models = importlib.import_module("app.models")
        need(Path(models.__file__).resolve() == root / "backend/app/models.py"
             and models.User.metadata is database.Base.metadata, "model_origin")
        yield
    finally:
        if database is not None:
            database.engine.dispose()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def _existing_lock(path):
    """Join the owner's existing lock without creating or rewriting files."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        observed = os.fstat(descriptor)
        need(stat.S_ISREG(observed.st_mode) and observed.st_uid == os.getuid()
             and stat.S_IMODE(observed.st_mode) == 0o600 and observed.st_nlink == 1, "inspection_lock_unverified")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


@contextmanager
def _journal_locks(bridge_journal, migration_journal, *, inspection):
    with ExitStack() as stack:
        if inspection:
            location = scope.journal_location(bridge_journal)
            expected = location | {"plan_sha256": bridge_journal.plan_hash}
            need(bridge_journal.read(scope.JOURNAL_LOCATION) == expected, "inspection_bridge_journal_location")
            stack.enter_context(_existing_lock(bridge_journal.directory / "continuity-lifecycle.lock"))
            stack.enter_context(_existing_lock(bridge_journal.directory / "owner.lock"))
            stack.enter_context(_existing_lock(migration_journal.directory / "owner.lock"))
            need(bridge_journal.read(scope.JOURNAL_LOCATION) == expected
                 and scope.journal_location(bridge_journal) == location, "inspection_bridge_journal_location")
        else:
            stack.enter_context(scope.lifecycle_lock(bridge_journal))
            stack.enter_context(bridge_journal.locked())
            stack.enter_context(migration_journal.locked())
        yield


def _verify_inspection_source(boundary, bridge_plan, bridge_journal, plan, recovery):
    root = boundary.root.resolve()
    modules = (
        (recovery, "infra/gcp/schema27_continuity_reconcile.py"),
        (catalog, "infra/gcp/schema27_continuity_catalog.py"),
        (scope, "infra/gcp/schema27_continuity_scope.py"),
        (scope.neon, "backend/scripts/neon_continuity_identity.py"),
        (step, "backend/scripts/notification_schema27_continuity_step.py"),
    )
    need(Path(__file__).resolve() == root / "infra/gcp/schema27_continuity_migration.py"
         and all(Path(module.__file__).resolve() == root / path for module, path in modules)
         and native.successor.controller_source(root) == (
             bridge_plan.controller_release, bridge_plan.controller_source_sha256,
         )
         and contract.verify_source(root) == plan.source_sha256, "inspection_source_changed")
    need(native.promoted_receipts(bridge_plan, bridge_journal) == plan.bridge_receipts_sha256,
         "inspection_bridge_receipts")


def execute(mode, boundary, bridge_plan, bridge_journal, plan, migration_journal, *, funding_policy=None):
    from infra.gcp import schema27_continuity_reconcile as recovery

    need(mode in {"upgrade", "reconcile", INSPECT, INSPECT_FUNDING}
         and (funding_policy is not None) == (mode == INSPECT_FUNDING)
         and type(boundary) is native.NativeContinuityBoundary
         and type(bridge_plan) is bridge.ContinuityBridgePlan and type(bridge_journal) is rolling.Journal
         and type(plan) is MigrationPlan and type(migration_journal) is rolling.Journal
         and migration_journal.plan_hash == rolling.sha(asdict(plan))
         and migration_journal.directory != bridge_journal.directory, "exact_owner")
    boundary.journal = bridge_journal
    custody = bridge_plan.custody()
    identity = bootstrap.migration_identity(bridge_plan.database)
    intent = plan.intent(identity, native.successor.native.host_auth.ACCOUNT)
    inspection = mode in {INSPECT, INSPECT_FUNDING}
    with _journal_locks(bridge_journal, migration_journal, inspection=inspection):
        plan.validate(bridge_plan, bridge_journal)
        recovery.validate_custody(custody, bridge_journal, migration_journal, identity, intent,
                                  bridge_plan_sha256=plan.bridge_plan_sha256,
                                  bridge_receipts_sha256=plan.bridge_receipts_sha256)
        started = migration_journal.read(recovery.STARTED)
        need((started is not None) == (mode != "upgrade"), "started_reconcile_only")
        fence = {
            "migration_plan_sha256": migration_journal.plan_hash, "intent_sha256": intent.sha256(),
            "migration_journal_location": scope.journal_location(migration_journal),
        }
        retained_fence = bridge_journal.read(scope.MIGRATION_FENCE)
        matched_fence = retained_fence == fence | {"plan_sha256": bridge_journal.plan_hash}
        need(matched_fence or (retained_fence is None and mode == "upgrade"), "bridge_migration_intent")
        if inspection:
            need(rolling.canonical(started) == rolling.canonical(intent.snapshot()), "inspection_intent_changed")
            _verify_inspection_source(boundary, bridge_plan, bridge_journal, plan, recovery)
        if mode == INSPECT_FUNDING:
            with _metadata(boundary.root):
                from app.domains.credits.finance import FinancialPolicy

                return recovery.funding_facts_locked(
                    custody, bridge_journal, migration_journal, identity, intent,
                    bridge_plan_sha256=plan.bridge_plan_sha256, bridge_receipts_sha256=plan.bridge_receipts_sha256,
                    expected_liabilities=bridge_plan.protected_liabilities,
                    policy=FinancialPolicy.model_validate(funding_policy),
                )
        if mode == INSPECT:
            observed = recovery.reconcile_locked(
                custody, bridge_journal, migration_journal, identity, intent,
                bridge_plan_sha256=plan.bridge_plan_sha256, bridge_receipts_sha256=plan.bridge_receipts_sha256,
                expected_liabilities=bridge_plan.protected_liabilities,
            )
            return {
                "status": "continuity_migration_outcome_inspected", "schema": observed.schema,
                "migration_committed": observed.migration_committed, "formal_reconciliation": False,
                "replay_authorized": False, "new_admission_enabled": False, "cloud_resource_mutations": 0,
            }
        _verify_live(boundary, bridge_plan, bridge_journal, plan)
        if mode == "upgrade":
            need(migration_journal.read(recovery.COMMIT_ACK) is None
                 and migration_journal.read(recovery.CHECKED) is None, "journal_not_empty")
            runtime.observe_runtime(custody, bridge_journal, scope.neon.Purpose.BEFORE_SCHEMA27, boundary.observer)
            before = catalog.observe_schema26(custody, bridge_journal,
                                              expected_liabilities=bridge_plan.protected_liabilities)
            need((before.catalog_sha256, before.retained_acl_sha256) == (
                plan.catalog26_sha256, plan.retained_acl_sha256), "before_catalog_or_acl")
            with _metadata(boundary.root):
                db_engine = bootstrap.engine(bridge_plan.database)
                try:
                    if retained_fence is None:
                        bridge_journal.record(scope.MIGRATION_FENCE, fence)
                    need(bridge_journal.read(scope.MIGRATION_FENCE) == fence | {
                        "plan_sha256": bridge_journal.plan_hash}, "durable_bridge_intent_changed")
                    migration_journal.record(recovery.STARTED, intent.snapshot())
                    stored = migration_journal.read(recovery.STARTED)
                    need(stored == intent.snapshot(), "durable_intent_changed")
                    with db_engine.begin() as connection:
                        result = step.upgrade_transaction(connection, identity, intent, root=boundary.root,
                                                          flags=dict.fromkeys(contract.DISABLED_FLAGS, "false"),
                                                          journal_intent_sha256=rolling.sha(stored))
                        need(result["transaction_ready_to_commit"] is True and result["committed"] is False,
                             "transaction_not_ready")
                    migration_journal.record(recovery.COMMIT_ACK, {"receipt_sha256": result["receipt_sha256"]})
                finally:
                    db_engine.dispose()
        observation = recovery.reconcile_locked(
            custody, bridge_journal, migration_journal, identity, intent,
            bridge_plan_sha256=plan.bridge_plan_sha256, bridge_receipts_sha256=plan.bridge_receipts_sha256,
            expected_liabilities=bridge_plan.protected_liabilities,
        )
        _verify_live(boundary, bridge_plan, bridge_journal, plan)
        if observation.migration_committed and migration_journal.read(recovery.CHECKED) is None:
            migration_journal.record(recovery.CHECKED, observation.journal_payload())
        return observation.journal_payload() | {"new_admission_enabled": False, "cloud_resource_mutations": 0}


def load(raw):
    value = rolling.release._json(raw)
    need(type(value) is dict and set(value) == {field.name for field in fields(MigrationPlan)}, "plan_shape")
    canonical = rolling.canonical(value)
    value["services"] = tuple(rolling.ServicePin(**(pin | {
        "revision_keys": tuple(pin["revision_keys"]), "traffic": tuple(pin["traffic"]),
        "secrets": tuple(rolling.SecretPin(**secret) for secret in pin["secrets"]),
    })) for pin in value["services"])
    result = MigrationPlan(**value)
    need(rolling.canonical(asdict(result)) == canonical, "noncanonical_plan")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "upgrade", "reconcile", INSPECT, INSPECT_FUNDING))
    for name in ("root", "target-root", "prior-root", "bridge-plan", "bridge-journal", "plan"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--bridge-plan-sha256", required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--funding-policy", type=Path)
    parser.add_argument("--funding-policy-sha256")
    args = parser.parse_args()
    need((args.funding_policy is not None) == (args.mode == INSPECT_FUNDING)
         and (args.funding_policy_sha256 is not None) == (args.mode == INSPECT_FUNDING), "funding_policy_arguments")
    need(Path(__file__).resolve() == args.root.resolve() / "infra/gcp/schema27_continuity_migration.py",
         "controller_origin")
    read = native.app26.initial._read_pinned
    bridge_plan = bridge.load(read(args.bridge_plan, args.bridge_plan_sha256), args.bridge_plan_sha256)
    journal = bridge.bind(bridge_plan, args.bridge_journal)
    boundary = native.NativeContinuityBoundary(args.root.resolve(), args.target_root, args.prior_root)
    try:
        if args.mode == "capture":
            plan = capture(boundary, bridge_plan, journal)
            result = {"status": "continuity_migration_captured", "native_mutations": 0,
                      "plan_sha256": native._write_plan(args.plan, plan)}
        else:
            need(args.journal is not None and type(args.plan_sha256) is str, "private_migration_plan")
            plan = load(read(args.plan, args.plan_sha256))
            migration = rolling.Journal(args.journal, plan)
            policy = None
            if args.mode == INSPECT_FUNDING:
                policy = rolling.release._json(read(args.funding_policy, args.funding_policy_sha256))
                need(type(policy) is dict, "funding_policy_shape")
            result = execute(args.mode, boundary, bridge_plan, journal, plan, migration, funding_policy=policy)
        print(json.dumps(result))
    finally:
        boundary.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        code = str(error)
        result = {"status": "continuity_migration_stopped", "replay_authorized": False}
        if type(error) is rolling.RollingDenied and re.fullmatch(r"[a-z0-9_]{1,110}", code):
            result["fixed_code"] = code
        print(json.dumps(result))
        raise SystemExit(1) from None
