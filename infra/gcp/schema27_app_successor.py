"""Closed-pilot Source27 application rolling release; never DDL or payment activation.

Both immutable images are Source27 and use the already migrated continuity DB.
Only the three app images/releases/revisions and their traffic may change.
Every action retains the stock started/ACK/readback protocol and fresh guards.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from infra.gcp import schema27_app_source as sources
from infra.gcp import schema27_app_successor_plan as plans
from infra.gcp import schema27_continuity_bridge as bridge
from infra.gcp import schema27_continuity_catalog as catalog
from infra.gcp import schema27_continuity_runtime as runtime
from infra.gcp import schema27_continuity_scope as scope
from infra.gcp import schema27_native_migration as native27
from sqlalchemy.engine import Connection

from scripts import notification_schema27_contract as contract

rolling, successor, historical = plans.rolling, plans.successor, plans.historical
need = plans.need


@dataclass(frozen=True, slots=True)
class Catalog27:
    catalog_sha256: str
    retained_acl_sha256: str
    protected_liabilities: tuple[tuple[str, str, int], ...]


def read_catalog27(connection, *, expected_liabilities=None):
    """Compose stock shape/ACL/finance gates on one authenticated exact27 snapshot."""
    need(isinstance(connection, Connection), "catalog_session_unverified")
    verified = catalog._authenticated.get(connection)
    need(verified is not None and verified[0] is scope.neon.Purpose.AFTER_SCHEMA27
         and verified[2].is_active and connection.get_transaction() is verified[2], "catalog_session_unverified")
    _, identity, _ = verified
    catalog._verify_migrator_session(connection, identity)
    catalog.financial_catalog.verify_financial_catalog(connection, catalog.native)
    catalog.browser19.verify_permissions(connection, identity.namespace, identity.runtime, identity.migrator, identity.observer)
    catalog.reset21.verify_reset_permissions(connection, identity.namespace, identity.runtime, identity.migrator, identity.observer)
    catalog.unified25.verify_new_permissions(connection, identity)
    signature = contract.observe(connection, identity)
    catalog.schema26.verify_writer_permissions(connection, runtime=identity.runtime, migrator=identity.migrator)
    liabilities = catalog.financial.liability_pins(connection)
    need(expected_liabilities is None or liabilities == expected_liabilities, "protected_liability_changed")
    return Catalog27(signature, rolling.sha(catalog.retained.old_acls(connection, identity)), liabilities)


def observe_catalog27(database, nonce, *, expected_liabilities=None):
    # The fixed session lock precedes its snapshot and excludes an in-flight
    # migration. A transport/role/schema failure is never a fallback to26.
    with catalog.authenticated_observer(database, nonce, scope.neon.Purpose.AFTER_SCHEMA27,
                                       recovery=True) as connection:
        return read_catalog27(connection, expected_liabilities=expected_liabilities)


class Source27Boundary(native27.Source27BridgeBoundary):
    def verify_source_image(self, *, release, image_digest, build_id, ci_run_id):
        self.descriptor = sources.qualify(self.root, release)
        # Retain real five-job CI, family steps, Git-source Build and resolved
        # image checks. A source-only descriptor itself grants none of these.
        native27.application27.verify_native_ci(self, ci_run_id)
        self._verify_source_image_provenance(release=release, image_digest=image_digest,
                                            build_id=build_id, ci_run_id=ci_run_id)


class NativeApp27Boundary(bridge.NativeContinuityBoundary):
    def __init__(self, root, target_root, prior_root):
        super().__init__(root, target_root, prior_root)
        self.observer = Source27Boundary(self.target_root)
        self.prior_observer = Source27Boundary(self.prior_root)

    def _verify_sources(self, controller, stamp, controller_ci, target, prior):
        # A qualified file on disk is insufficient if Python retained a helper
        # from a different checkout in its module cache.
        for module, relative in ((sources, "schema27_app_source.py"),
                (plans, "schema27_app_successor_plan.py"), (bridge, "schema27_continuity_bridge.py"),
                (catalog, "schema27_continuity_catalog.py"), (runtime, "schema27_continuity_runtime.py"),
                (scope, "schema27_continuity_scope.py"), (native27, "schema27_native_migration.py")):
            need(Path(module.__file__).resolve() == self.root / "infra/gcp" / relative, "controller_module_origin")
        need(Path(__file__).resolve() == self.root / "infra/gcp/schema27_app_successor.py"
             and Path(contract.__file__).resolve() == self.root / "backend/scripts/notification_schema27_contract.py",
             "controller_module_origin")
        super()._verify_sources(controller, stamp, controller_ci, target, prior)
        for root, pin in ((self.target_root, target), (self.prior_root, prior)):
            need(successor._git(root, "rev-parse", "HEAD").decode().strip() == pin.release
                 and not successor._git(root, "status", "--porcelain=v1", "--untracked-files=all"),
                 "source_changed_during_native_readback")

    def _sources(self, plan):
        need(type(plan) is plans.App27Plan, "plan_kind")
        self._verify_sources(plan.controller_release, plan.controller_source_sha256,
                             plan.controller_ci_run_id, plan.source, plan.prior_source)
        need(self.observer.descriptor == plan.source_contract
             and self.prior_observer.descriptor == plan.prior_contract, "qualified_source_changed")

    def verify_global(self, plan):
        plan.validate()
        need(type(self.journal) is rolling.Journal, "journal_required")
        scope.verify_journal(plan.custody(), self.journal)
        self._sources(plan)
        self.observer.verify_policy_configuration(plan.policies)
        self._identity(plan.runtime_uid)
        need(self._reachable(plan.services, plan.prior_source) == plan.reachable_revisions, "reachable_prior")
        need(self._resources(plan.workers_release) == plan.resources, "resources")
        runtime.observe_runtime(plan.custody(), self.journal, scope.neon.Purpose.AFTER_SCHEMA27, self.observer)
        observed = observe_catalog27(plan.database, plan.nonce, expected_liabilities=plan.protected_liabilities)
        need(observed.catalog_sha256 == plan.financial_sha256, "database_catalog")
        need(observed.retained_acl_sha256 == plan.retained_acl_sha256, "retained_acl")
        for pin in plan.services:
            self._verify_service(plan, pin)

    def capture(self, source, prior, *, controller_ci_run_id, nonce, workers_release):
        need(type(source) is successor.SourcePin and type(prior) is successor.SourcePin, "source_kind")
        source.validate()
        prior.validate()
        need(scope._nonce(nonce) and type(controller_ci_run_id) is int and controller_ci_run_id > 0,
             "capture_inputs")
        need(type(workers_release) is str and re.fullmatch(r"[a-f0-9]{40}", workers_release), "workers_release")
        controller, stamp = successor.controller_source(self.root)
        self._verify_sources(controller, stamp, controller_ci_run_id, source, prior)
        pins = tuple(self._pin(service, prior) for service in rolling.release.SERVICES)
        for pin in pins:
            need([item.secret + ":" + item.version for item in pin.secrets if item.environment_name == "DATABASE_URL"]
                 == [scope.RUNTIME_REFERENCE], "runtime_reference")
            native27.require_closed(self.service(pin.service)["template"],
                                    (*bridge.bridge.app26.CLOSED_FLAGS, *native27.F07_FLAGS))
        policies, candidate = self._capture_policies(pins, None, None)
        need(candidate is None, "configuration_unchanged")
        successor.verify_rollout_window(self, policies)
        resources = self._resources(workers_release)
        database = scope.capture_database_pin(nonce)
        observed = observe_catalog27(database, nonce)
        account = self.observer._cloud("iam", "service-accounts", "describe", rolling.RUNTIME)
        plan = plans.App27Plan(source=source, prior_source=prior,
            source_contract=self.observer.descriptor, prior_contract=self.prior_observer.descriptor,
            controller_release=controller, controller_source_sha256=stamp, controller_ci_run_id=controller_ci_run_id,
            nonce=nonce, database=database, runtime_uid=account["uniqueId"], workers_release=workers_release,
            financial_sha256=observed.catalog_sha256, retained_acl_sha256=observed.retained_acl_sha256,
            policies=policies, services=pins, resources=resources, protected_liabilities=observed.protected_liabilities,
            reachable_revisions=self._reachable(pins, prior))
        plan.validate()
        need(all(self.revision(pin.service, plan.revision(pin.service)) is None for pin in pins), "candidate_exists")
        return plan


def execute(mode, plan, boundary, journal):
    need(mode in {"stage", "advance", "observe", "reconcile", "rollback"}
         and type(plan) is plans.App27Plan and type(boundary) is NativeApp27Boundary, "exact_native_owner")
    plan.validate()
    scope.verify_journal(plan.custody(), journal)
    with scope.lifecycle_lock(journal):
        need(journal.read(scope.MIGRATION_FENCE) is None, "foreign_migration_fence")
        boundary.journal = journal
        if pending := bridge._pending_acknowledged(plan, boundary, journal):
            return pending
        boundary.verify_global(plan)
        if mode == "rollback" or mode in {"observe", "reconcile"} and journal.read(historical.ROLLBACK_INTENT):
            result = historical.rollback_app(plan, boundary, journal, reconcile=mode != "rollback")
        elif mode == "advance":
            result = rolling.advance(plan, boundary, journal)
        elif mode in {"observe", "reconcile"}:
            result = rolling.reconcile(plan, boundary, journal)
        else:
            with journal.locked():
                need(journal.read(historical.ROLLBACK_INTENT) is None, "rollback_started")
                need(not any(journal.read(f"promote.{pin.service}.started") for pin in plan.services), "stage_passed")
                for pin in plan.services:
                    if not rolling.action(plan, boundary, journal, pin, "stage"):
                        return {"status": "same_native_operation_pending", "phase": "stage", "service": pin.service,
                                "replay_authorized": False}
                boundary.verify_global(plan)
            result = {"status": "schema27_app_staged", "rolling_release_complete": False, "replay_authorized": False}
        if result["status"] == "same_native_operation_pending":
            return result
        return result | {"status": "schema27_app_" + mode, "schema": plans.SCHEMA, "migration_authorized": False,
                         "payment_activation_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "stage", "advance", "observe", "reconcile", "rollback"))
    for name in ("root", "target-root", "prior-root", "plan", "journal"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--capture-config", type=Path)
    parser.add_argument("--capture-config-sha256")
    args = parser.parse_args()
    need(Path(__file__).resolve() == args.root.resolve() / "infra/gcp/schema27_app_successor.py", "controller_origin")
    boundary = NativeApp27Boundary(args.root, args.target_root, args.prior_root)
    try:
        if args.mode == "capture":
            need(args.capture_config is not None and type(args.capture_config_sha256) is str, "private_capture_config")
            raw = bridge.bridge.app26.initial._read_pinned(args.capture_config, args.capture_config_sha256)
            pins, options = bridge._capture_config(raw)
            need(set(options) == {"controller_ci_run_id", "nonce", "workers_release"}, "application_only_options")
            plan = boundary.capture(*pins, **options)
            boundary.journal = bridge._bind_new(plan, args.journal)
            boundary.verify_global(plan)
            result = {"status": "schema27_app_captured", "native_mutations": 0,
                      "plan_sha256": bridge._write_plan(args.plan, plan)}
        else:
            need(type(args.plan_sha256) is str, "private_plan_pin")
            raw = bridge.bridge.app26.initial._read_pinned(args.plan, args.plan_sha256)
            plan = plans.load(raw, args.plan_sha256)
            result = execute(args.mode, plan, boundary, plans.bind(plan, args.journal))
        print(rolling.canonical(result).decode())
    finally:
        boundary.close()


if __name__ == "__main__":
    main()
