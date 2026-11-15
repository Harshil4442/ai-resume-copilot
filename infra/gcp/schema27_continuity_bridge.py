"""Native Cloud Run bridge for the continuity database, before schema27 DDL."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, replace
from pathlib import Path

from infra.gcp import schema27_continuity_bridge_plan as bridge
from infra.gcp import schema27_continuity_runtime as runtime
from infra.gcp import schema27_continuity_scope as scope
from infra.gcp import schema27_native_migration as native27

successor = bridge.successor
rolling = bridge.rolling
app26 = bridge.app26
historical = bridge.historical


def need(value, reason):
    rolling.need(value, "schema27_continuity_native_" + reason)


class NativeContinuityBoundary(successor.NativeSuccessorBoundary):
    """Reuse the retained Cloud Run transport for the separately bound plan."""

    def __init__(self, root, target_root, prior_root):
        super().__init__(root, target_root, prior_root)
        self.observer = native27.Source27BridgeBoundary(self.target_root)

    def _sources(self, plan):
        need(type(plan) is bridge.ContinuityBridgePlan, "source_plan_kind")
        self._verify_sources(
            plan.controller_release, plan.controller_source_sha256, plan.controller_ci_run_id,
            plan.source, plan.prior_source,
        )

    def _resources(self, workers_release):
        coordinator = historical.coordinator
        reader = coordinator.NativeCoordinatorRun()
        try:
            service = reader.service(coordinator.SERVICE)
            coordinator.ready(service)
            targets = {
                rolling.short_revision(service[key], coordinator.SERVICE)
                for key in ("latestCreatedRevision", "latestReadyRevision")
            }
            targets.update(row["revision"] for row in rolling.pinned_traffic(coordinator.SERVICE, service))
            secret, version = scope.COORDINATOR_REFERENCE.split(":")
            expected = {"name": "DATABASE_URL",
                        "valueSource": {"secretKeyRef": {"secret": secret, "version": version}}}
            templates = [service["template"], *(coordinator.revision(reader, name) for name in sorted(targets))]
            need(all(rolling.environment(template).get("DATABASE_URL") == expected for template in templates),
                 "coordinator_database_reference")
            pins = super()._resources(workers_release)
            # Match the stock resource snapshot to the same settled service.
            # Immutable revisions were just verified from that service's targets.
            need(reader.service(coordinator.SERVICE) == service, "coordinator_resource_changed")
            need(self._reference(secret, version) == version, "coordinator_secret_version")
            return pins
        finally:
            reader.stock.session.close()

    def verify_global(self, plan):
        from infra.gcp import schema27_continuity_catalog as catalog

        need(type(plan) is bridge.ContinuityBridgePlan and type(self.journal) is rolling.Journal, "ownership")
        custody = plan.custody()
        scope.verify_journal(custody, self.journal)
        self._sources(plan)
        if plan.candidate_policies is not None:
            successor.verify_historical_policy(self, plan.policies)
        self.observer.verify_policy_configuration(plan.target_policies())
        self._identity(plan.runtime_uid)
        need(self._reachable(plan.services, plan.prior_source) == plan.reachable_revisions, "reachable_prior")
        need(self._resources(plan.workers_release) == plan.resources, "resources")
        runtime.observe_runtime(custody, self.journal, scope.neon.Purpose.BEFORE_SCHEMA27, self.observer)
        observed = catalog.observe_schema26(custody, self.journal, expected_liabilities=plan.protected_liabilities)
        need(observed.catalog_sha256 == plan.financial_sha256, "database_catalog")
        for pin in plan.services:
            self._verify_service(plan, pin)

    def _verify_service(self, plan, pin):
        current = self.service(pin.service)
        iam = self.iam(pin.service)
        rolling.worker_iam(pin.service, iam)
        need(rolling.static_sha(current) == pin.static_sha256 and rolling.sha(iam) == pin.iam_sha256,
             "static_or_iam")
        rolling._old_revision(self, pin)
        revision = self.revision(pin.service, pin.prior_revision)
        prior = {key: revision.get(key) for key in pin.revision_keys}
        if rolling.template_sha(prior, pin.service) != pin.template_sha256:
            prior["revision"] = pin.prior_revision
        need(rolling.template_sha(prior, pin.service) == pin.template_sha256
             and revision["containers"][0]["image"].endswith("@" + plan.prior_source.image_digest), "prior_template")
        target = self._target_template(plan, pin, current, prior)
        live_hash = rolling.template_sha(current["template"], pin.service)
        live_traffic = rolling.pinned_traffic(pin.service, current)
        if (int(current["generation"]) == pin.generation and live_hash == pin.template_sha256
                and live_traffic == list(pin.traffic)):
            rolling._ready(current, pin)
        else:
            started = next((row for phase in ("rollback", "promote", "stage")
                            if (row := self.journal.read(f"{phase}.{pin.service}.started")) is not None), None)
            need(started is not None and int(current["generation"]) == started["expected_generation"]
                 and live_hash == started["template_sha256"] == rolling.template_sha(target, pin.service)
                 and live_traffic == started["traffic"], "unexpected_phase")
        native27.require_closed(prior, plan.disabled_flags())
        native27.require_closed(current["template"], plan.disabled_flags())
        self._verify_policy_and_secrets(plan.policies, pin, prior)
        candidate = live_hash != pin.template_sha256
        self._verify_policy_and_secrets(plan.target_policies() if candidate else plan.policies,
                                        rolling.candidate_service_pin(plan, pin) if candidate else pin,
                                        current["template"])

    def _verify_policy_and_secrets(self, policies, pin, template):
        rolling.verify_policy_bindings(policies, pin, template)
        env = rolling.environment(template)
        for item in pin.secrets:
            expected = {"name": item.environment_name, "valueSource":
                        {"secretKeyRef": {"secret": item.secret, "version": item.version}}}
            need(env.get(item.environment_name) == expected
                 and self._reference(item.secret, item.version) == item.version,
                 "secret_changed")

    def _capture_policies(self, pins, expense, model):
        refs = {item.environment_name: item.secret + ":" + item.version for item in pins[0].secrets}
        env = rolling.environment(self.service(pins[0].service)["template"])
        policies = rolling.PolicyPins(
            refs["HIREWIZ_EXPENSE_POLICY_JSON"],
            self._policy_sha(refs["HIREWIZ_EXPENSE_POLICY_JSON"]),
            refs["LLM_MODEL_COST_POLICY_JSON"],
            self._policy_sha(refs["LLM_MODEL_COST_POLICY_JSON"]),
            refs["LLM_API_KEY"],
            env["LLM_MODEL"]["value"],
            env["LLM_API_BASE"]["value"],
        )
        candidate = None
        if expense is not None:
            candidate = replace(policies, expense_secret=expense, expense_sha256=self._policy_sha(expense),
                                model_policy_secret=model, model_policy_sha256=self._policy_sha(model))
            successor.verify_historical_policy(self, policies)
        self.observer.verify_policy_configuration(candidate or policies)
        successor.verify_rollout_window(self, candidate or policies)
        return policies, candidate

    def _policy_sha(self, reference):
        name, version = reference.split(":")
        need(self._reference(name, version) == version, "policy_version")
        return hashlib.sha256(self.observer._secret(reference).encode()).hexdigest()

    def capture(self, source, prior, *, controller_ci_run_id, nonce, workers_release,
                employer_cache_secret=None, candidate_expense_secret=None, candidate_model_policy_secret=None):
        from infra.gcp import schema27_continuity_catalog as catalog

        source.validate()
        prior.validate()
        need(scope._nonce(nonce) and type(controller_ci_run_id) is int and controller_ci_run_id > 0,
             "capture_inputs")
        need(type(workers_release) is str and re.fullmatch(r"[a-f0-9]{40}", workers_release), "workers_release")
        need((candidate_expense_secret is None) == (candidate_model_policy_secret is None), "policy_pair")
        for reference in (candidate_expense_secret, candidate_model_policy_secret):
            need(reference is None or (type(reference) is str and rolling.REF.fullmatch(reference)),
                 "policy_reference")
        cache = rolling.EmployerCacheBinding(employer_cache_secret) if employer_cache_secret is not None else None
        if cache is not None:
            cache.validate()
        controller, stamp = successor.controller_source(self.root)
        self._verify_sources(controller, stamp, controller_ci_run_id, source, prior)
        pins = tuple(self._pin(service, prior) for service in rolling.release.SERVICES)
        for pin in pins:
            need([(item.secret + ":" + item.version) for item in pin.secrets
                  if item.environment_name == "DATABASE_URL"] == [scope.RUNTIME_REFERENCE], "runtime_reference")
        policies, candidate = self._capture_policies(pins, candidate_expense_secret, candidate_model_policy_secret)
        resources = self._resources(workers_release)
        database = scope.capture_database_pin(nonce)
        observed = catalog.capture_schema26(database, nonce)
        account = self.observer._cloud("iam", "service-accounts", "describe", rolling.RUNTIME)
        plan = bridge.ContinuityBridgePlan(
            source=source, prior_source=prior, controller_release=controller, controller_source_sha256=stamp,
            controller_ci_run_id=controller_ci_run_id, nonce=nonce, database=database,
            runtime_uid=account["uniqueId"], workers_release=workers_release, financial_sha256=observed.catalog_sha256,
            policies=policies, candidate_policies=candidate, services=pins, resources=resources,
            protected_liabilities=observed.protected_liabilities, reachable_revisions=self._reachable(pins, prior),
            candidate_employer_cache=cache,
        )
        if candidate is not None or cache is not None:
            plan = replace(plan, services=tuple(
                replace(pin, candidate_template_sha256=rolling.template_sha(
                    rolling.stage_body(plan, pin, self.service(pin.service))["template"], pin.service))
                if candidate is not None or cache.service == pin.service else pin
                for pin in pins
            ))
        plan.validate()
        need(all(self.revision(pin.service, plan.revision(pin.service)) is None for pin in pins),
             "candidate_exists")
        return plan


def _bind_new(plan, directory):
    custody = plan.custody()
    journal = rolling.Journal(directory, custody)
    with journal.locked():
        need(all(item.name == "owner.lock" for item in directory.iterdir()), "nonempty_capture_journal")
        journal.record(scope.JOURNAL_BINDING, custody.journal_binding())
        journal.record(scope.JOURNAL_LOCATION, scope.journal_location(journal))
    return journal


def _pending_acknowledged(plan, boundary, journal):
    for pin in plan.services:
        for phase in ("rollback", "promote", "stage"):
            marker = f"{phase}.{pin.service}"
            started = journal.read(marker + ".started")
            if started is None:
                continue
            ack = journal.read(marker + ".ack")
            if ack is not None and journal.read(marker + ".checked") is None:
                boundary._sources(plan)
                if historical.join_metadata(plan, boundary, pin, started, ack) is None:
                    return {"status": "same_native_operation_pending", "phase": phase, "service": pin.service,
                            "rolling_release_complete": False, "replay_authorized": False}
            break
    return None


def execute(mode, plan, boundary, journal):
    need(mode in {"stage", "advance", "observe", "reconcile", "rollback"}
         and type(plan) is bridge.ContinuityBridgePlan and type(boundary) is NativeContinuityBoundary,
         "exact_native_owner")
    scope.verify_journal(plan.custody(), journal)
    with scope.lifecycle_lock(journal):
        need(journal.read(scope.MIGRATION_FENCE) is None, "migration_started")
        return _dispatch(mode, plan, boundary, journal)


def _dispatch(mode, plan, boundary, journal):
    boundary.journal = journal
    if pending := _pending_acknowledged(plan, boundary, journal):
        return pending
    boundary.verify_global(plan)
    if mode == "rollback" or mode in {"reconcile", "observe"} and journal.read(historical.ROLLBACK_INTENT):
        result = historical.rollback_app(plan, boundary, journal, reconcile=mode != "rollback")
        return _bridge_result(result, "rollback")
    if mode == "advance":
        return _bridge_result(rolling.advance(plan, boundary, journal), "advance")
    if mode in {"observe", "reconcile"}:
        return _bridge_result(rolling.reconcile(plan, boundary, journal), mode)
    with journal.locked():
        need(journal.read(historical.ROLLBACK_INTENT) is None, "rollback_started")
        need(not any(journal.read(f"promote.{pin.service}.started") for pin in plan.services), "stage_passed")
        for pin in plan.services:
            if not rolling.action(plan, boundary, journal, pin, "stage"):
                return {"status": "same_native_operation_pending", "phase": "stage", "service": pin.service,
                        "replay_authorized": False}
        boundary.verify_global(plan)
    return {"status": "continuity_bridge_staged", "rolling_release_complete": False,
            "schema": bridge.DB_SCHEMA, "replay_authorized": False}


def _bridge_result(result, phase):
    if result["status"] == "same_native_operation_pending":
        return result
    return result | {"status": "continuity_bridge_" + phase, "schema": bridge.DB_SCHEMA}


def promoted_receipts(plan, journal):
    """Bind the migration to acknowledged stage and promotion on all three services."""
    scope.verify_journal(plan.custody(), journal)
    need(journal.read(historical.ROLLBACK_INTENT) is None, "rollback_started")
    operations, receipts = set(), {}
    for pin in plan.services:
        rows = {}
        for phase, offset, mask in (("stage", 1, "template,traffic"), ("promote", 2, "traffic")):
            prefix = f"{phase}.{pin.service}"
            started, ack, checked = (journal.read(prefix + suffix) for suffix in (".started", ".ack", ".checked"))
            need(started is not None and ack is not None and checked is not None, "promotion_receipt_missing")
            expected = {
                "phase": phase, "mask": mask, "expected_generation": pin.generation + offset,
                "service_uid": pin.uid, "traffic": rolling._traffic_for(plan, pin, phase == "promote"),
                "plan_sha256": journal.plan_hash, "replay_authorized": False,
            }
            hashes = ("request_sha256", "template_sha256")
            need(set(started) == set(expected) | set(hashes) | {"previous_etag"}
                 and all(started[key] == value for key, value in expected.items())
                 and type(started["expected_generation"]) is int and started["replay_authorized"] is False
                 and all(scope._hash(started[key]) for key in hashes)
                 and type(started["previous_etag"]) is str and 1 <= len(started["previous_etag"]) <= 256,
                 "promotion_started_shape")
            operation = ack.get("operation")
            need(set(ack) == {"operation", "request_sha256", "plan_sha256"}
                 and type(operation) is str and rolling.OPERATION.fullmatch(operation)
                 and operation not in operations and ack["request_sha256"] == started["request_sha256"],
                 "promotion_ack_lineage")
            operations.add(operation)
            need(checked == {"service_uid": pin.uid, "revision": plan.revision(pin.service),
                             "generation": pin.generation + offset, "native_metadata_reobserved": True,
                             "replay_authorized": False, "operation_ack_retained": True,
                             "plan_sha256": journal.plan_hash}
                 and type(checked["generation"]) is int and checked["native_metadata_reobserved"] is True
                 and checked["operation_ack_retained"] is True and checked["replay_authorized"] is False,
                 "promotion_readback_shape")
            rows[phase] = {"started": started, "ack": ack, "checked": checked}
        need(rows["stage"]["started"]["template_sha256"] == rows["promote"]["started"]["template_sha256"]
             and journal.read(f"rollback.{pin.service}.started") is None, "promotion_template_lineage")
        receipts[pin.service] = rows
    return rolling.sha(receipts)


def _capture_config(raw):
    config = rolling.release._json(raw)
    need(type(config) is dict and set(config) == {"source", "prior", "options"}, "capture_config")
    options = config["options"]
    required = {"controller_ci_run_id", "nonce", "workers_release"}
    optional = {"employer_cache_secret", "candidate_expense_secret", "candidate_model_policy_secret"}
    need(type(options) is dict and required <= set(options) <= required | optional, "capture_options")
    sources = []
    for key in ("source", "prior"):
        source = config[key]
        need(type(source) is dict and set(source) == {"release", "image_digest", "build_id", "ci_run_id"},
             "capture_source")
        pin = successor.SourcePin(**source)
        pin.validate()
        sources.append(pin)
    return sources, options


def _write_plan(path, plan):
    need(path.is_absolute() and path.parent.resolve() == path.parent
         and path.parent.stat().st_uid == os.getuid()
         and stat.S_IMODE(path.parent.stat().st_mode) == 0o700, "private_plan_destination")
    raw = rolling.canonical(asdict(plan)) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "stage", "advance", "observe", "reconcile", "rollback"))
    for name in ("root", "target-root", "prior-root", "plan", "journal"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--capture-config", type=Path)
    parser.add_argument("--capture-config-sha256")
    args = parser.parse_args()
    need(Path(__file__).resolve() == args.root.resolve() / "infra/gcp/schema27_continuity_bridge.py",
         "controller_origin")
    boundary = NativeContinuityBoundary(args.root, args.target_root, args.prior_root)
    try:
        if args.mode == "capture":
            need(args.capture_config is not None and type(args.capture_config_sha256) is str,
                 "private_capture_config")
            raw = app26.initial._read_pinned(args.capture_config, args.capture_config_sha256)
            sources, options = _capture_config(raw)
            plan = boundary.capture(*sources, **options)
            boundary.journal = _bind_new(plan, args.journal)
            boundary.verify_global(plan)
            result = {"status": "continuity_bridge_captured", "native_mutations": 0,
                      "plan_sha256": _write_plan(args.plan, plan)}
        else:
            need(type(args.plan_sha256) is str, "private_plan_pin")
            raw = app26.initial._read_pinned(args.plan, args.plan_sha256)
            plan = bridge.load(raw, args.plan_sha256)
            result = execute(args.mode, plan, boundary, bridge.bind(plan, args.journal))
        print(json.dumps(result))
    finally:
        boundary.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as refusal:
        code = str(refusal)
        projection = {"status": "continuity_bridge_stopped", "replay_authorized": False}
        if type(refusal) is rolling.RollingDenied and re.fullmatch(r"[a-z0-9_]{1,110}", code):
            projection["fixed_code"] = code
        print(json.dumps(projection))
        raise SystemExit(1) from None
