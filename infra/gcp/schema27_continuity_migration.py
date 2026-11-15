"""Capture, execute once, and reconcile the schema27 continuity migration."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from sqlalchemy import event

from infra.gcp import schema27_continuity_bootstrap as bootstrap
from infra.gcp import schema27_continuity_bridge as native
from infra.gcp import schema27_continuity_bridge_plan as bridge
from infra.gcp import schema27_continuity_catalog as catalog
from infra.gcp import schema27_continuity_runtime as runtime
from infra.gcp import schema27_continuity_scope as scope

# isort: split
from scripts import notification_schema27_continuity_step as step
from scripts import notification_schema27_contract as contract

rolling = scope.rolling
SCHEMA_KIND = "hirewiz_schema27_continuity_migration_v1"
METADATA_URL = "postgresql+psycopg://migration_metadata@127.0.0.1:1/metadata"


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
    with bridge_journal.locked():
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


def execute(mode, boundary, bridge_plan, bridge_journal, plan, migration_journal):
    from infra.gcp import schema27_continuity_reconcile as recovery

    need(mode in {"upgrade", "reconcile"} and type(boundary) is native.NativeContinuityBoundary
         and type(plan) is MigrationPlan and type(migration_journal) is rolling.Journal
         and migration_journal.plan_hash == rolling.sha(asdict(plan))
         and migration_journal.directory != bridge_journal.directory, "exact_owner")
    boundary.journal = bridge_journal
    custody = bridge_plan.custody()
    identity = bootstrap.migration_identity(bridge_plan.database)
    intent = plan.intent(identity, native.successor.native.host_auth.ACCOUNT)
    with bridge_journal.locked(), migration_journal.locked():
        plan.validate(bridge_plan, bridge_journal)
        recovery.validate_custody(custody, bridge_journal, migration_journal, identity, intent,
                                  bridge_plan_sha256=plan.bridge_plan_sha256,
                                  bridge_receipts_sha256=plan.bridge_receipts_sha256)
        started = migration_journal.read(recovery.STARTED)
        need((started is not None) == (mode == "reconcile"), "started_reconcile_only")
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
    parser.add_argument("mode", choices=("capture", "upgrade", "reconcile"))
    for name in ("root", "target-root", "prior-root", "bridge-plan", "bridge-journal", "plan"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--bridge-plan-sha256", required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args()
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
            result = execute(args.mode, boundary, bridge_plan, journal, plan, migration)
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
