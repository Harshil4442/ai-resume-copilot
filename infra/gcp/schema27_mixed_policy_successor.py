"""Versioned post27 application successor with one reviewed policy-reference pair.

V1 plans, journals, source helpers and private identity custody remain unchanged.
The old pair is historical rollback metadata; only the fresh candidate pair can
authorize current work. All product/spending admission stays closed. No migration,
identity/key change, model call, app build, secret publication or IAM writer.
"""
from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from infra.gcp import schema27_mixed_app_successor as v1

if TYPE_CHECKING:
    from infra.gcp.rolling_release import PolicyPins
    from infra.gcp.schema27_app_successor_plan import App27Plan
    from infra.gcp.schema27_mixed_identity_profile import Profile

app, rolling, scope, identities = v1.app, v1.rolling, v1.scope, v1.identities
KIND = "hirewiz_schema27_mixed_policy_app_successor_v2"
ROOT = Path(__file__).resolve().parents[2]
SOURCE70 = "70a1ef77c19c7f00570bc159b3db514b38d112d0"
SOURCE3F7 = "3f7fd47738b336e4cbfdfdff2f6fdc13adca74c6"


def need(value, code):
    rolling.need(value, "schema27_mixed_policy_" + code)


def diagnostic(error, operation):
    result = v1.diagnostics.diagnostic(error, operation)
    if isinstance(error, rolling.RollingDenied) and len(error.args) == 1:
        code = error.args[0]
        if type(code) is str and re.fullmatch(r"schema27_mixed_policy_[a-z_]{1,90}", code):
            result["denial_code"] = code
    return result


@dataclass(frozen=True)
class CandidateTemplate:
    service: str
    sha256: str


@dataclass(frozen=True)
class Plan:
    application: App27Plan
    identities: Profile
    journal_location: dict
    candidate_policies: PolicyPins
    candidate_templates: tuple[CandidateTemplate, ...]
    schema_contract: str = KIND

    def __getattr__(self, name):
        if name in {field.name for field in fields(app.plans.App27Plan)} - {"schema_contract", "services"}:
            return getattr(self.application, name)
        raise AttributeError(name)

    @property
    def release(self):
        return self.application.release

    @property
    def image_digest(self):
        return self.application.image_digest

    @property
    def runtime_secret(self):
        return self.application.runtime_secret

    @property
    def image(self):
        return self.application.image

    @property
    def tag(self):
        return self.application.tag

    @property
    def rollback_tag(self):
        return self.application.rollback_tag

    @property
    def candidate_employer_cache(self):
        return None

    @property
    def services(self):
        stamps = {pin.service: pin.sha256 for pin in self.candidate_templates}
        return tuple(replace(pin, candidate_template_sha256=stamps.get(pin.service))
                     for pin in self.application.services)

    def pin(self, service):
        need(service in rolling.release.SERVICES, "service_scope")
        return next(pin for pin in self.services if pin.service == service)

    def revision(self, service):
        return self.application.revision(service)

    def disabled_flags(self):
        return self.application.disabled_flags()

    def target_policies(self):
        return self.candidate_policies

    def promoted_aliases(self, pin, rows):
        return app.bridge.bridge.app26.App26V3Plan.promoted_aliases(self, pin, rows)

    def validate(self):
        need(type(self) is Plan and self.schema_contract == KIND
             and type(self.application) is app.plans.App27Plan
             and type(self.identities) is identities.Profile, "plan_kind")
        # This immutable child retains None candidate hashes and V1 validation.
        self.application.validate()
        self.identities.validate()
        need(self.release == SOURCE3F7 and self.prior_source.release == SOURCE70, "source_scope")
        need(self.runtime_uid == self.identities.transition.old_api.uid
             and all(pin.uid == self.identities.promotion.before["services"][pin.service]["uid"]
                     for pin in self.services), "service_identity_pins")
        prior, target = self.policies, self.candidate_policies
        app.plans.bridge._validate_policies(target)
        need(type(target) is rolling.PolicyPins
             and (target.model_key_secret, target.model_primary, target.model_api_base)
                 == (prior.model_key_secret, prior.model_primary, prior.model_api_base)
             and all(old != new and old.split(":")[0] == new.split(":")[0] for old, new in (
                 (prior.expense_secret, target.expense_secret),
                 (prior.model_policy_secret, target.model_policy_secret))), "candidate_route_or_pair_changed")
        need(type(self.candidate_templates) is tuple
             and tuple(pin.service for pin in self.candidate_templates) == rolling.release.SERVICES
             and all(type(pin) is CandidateTemplate and scope._hash(pin.sha256)
                     for pin in self.candidate_templates), "candidate_template_pins")
        for pin in self.services:
            rolling.verify_policy_bindings(target, rolling.candidate_service_pin(self, pin))
        location = self.journal_location
        need(type(location) is dict and set(location) == {"path", "device", "inode"}
             and Path(location["path"]).is_absolute()
             and all(type(location[key]) is int and location[key] >= 0 for key in ("device", "inode")),
             "journal_location")

    def custody(self):
        self.validate()
        return scope.BridgeCustody(self.nonce, rolling.sha(asdict(self)), self.database)


def load(raw, digest):
    need(type(raw) is bytes and len(raw) <= 524288 and scope._hash(digest)
         and hashlib.sha256(raw).hexdigest() == digest, "plan_file")
    value = rolling.release._json(raw)
    need(type(value) is dict and set(value) == {field.name for field in fields(Plan)}
         and value["schema_contract"] == KIND, "plan_kind")
    child = rolling.canonical(value["application"])
    result = Plan(app.plans.load(child, hashlib.sha256(child).hexdigest()),
        identities.Profile.from_dict(value["identities"]), value["journal_location"],
        rolling.PolicyPins(**value["candidate_policies"]),
        tuple(CandidateTemplate(**pin) for pin in value["candidate_templates"]))
    result.validate()
    need(rolling.canonical(asdict(result)) == rolling.canonical(value), "plan_canonical")
    return result


def stage_body(plan, pin, current):
    """Keep the dedicated API principal while applying both reviewed references."""
    need(type(plan) is Plan, "plan_kind")
    identity = plan.identities.identity(pin.service)
    need(current["template"].get("serviceAccount") == identity.email, "service_identity")
    if pin.service != v1.API:
        return rolling.stage_body(plan, pin, current)
    # Reuse V1's exact API source/request builder with its unchanged baseline.
    # A genuine baseline plan is constructed, never a substituted identity view.
    baseline = v1.Plan(plan.application, plan.identities, plan.journal_location)
    body = v1.stage_body(baseline, plan.application.pin(pin.service), current)
    template = body["template"]
    env = rolling.environment(template)
    target_pin = rolling.candidate_service_pin(plan, pin)
    for secret in target_pin.secrets:
        env[secret.environment_name] = {"name": secret.environment_name, "valueSource": {
            "secretKeyRef": {"secret": secret.secret, "version": secret.version}}}
    template["containers"][0]["env"] = list(env.values())
    rolling.verify_policy_bindings(plan.target_policies(), target_pin, template)
    need(template["serviceAccount"] == identity.email, "service_identity")
    if pin.candidate_template_sha256 is not None:
        need(rolling.template_sha(template, pin.service) == pin.candidate_template_sha256,
             "candidate_template_changed")
    return body


class NativeBoundary(v1.NativeBoundary):
    """Compose real mixed principal, scanner, exact27 catalog and policy guards."""
    def __init__(self, root, target_root, prior_root, profile):
        super().__init__(root, target_root, prior_root, profile)
        self._verified_source_tuple = None

    def _local_sources(self, controller, stamp, target, prior):
        """Repeat every local source/module/checkout guard, including on cache hits."""
        for module, relative in ((app.sources, "schema27_app_source.py"),
                (app.plans, "schema27_app_successor_plan.py"), (app.bridge, "schema27_continuity_bridge.py"),
                (app.catalog, "schema27_continuity_catalog.py"), (app.runtime, "schema27_continuity_runtime.py"),
                (scope, "schema27_continuity_scope.py"), (app.native27, "schema27_native_migration.py"),
                (v1, "schema27_mixed_app_successor.py"), (identities, "schema27_mixed_identity_profile.py")):
            need(Path(module.__file__).resolve() == self.root / "infra/gcp" / relative, "module_origin")
        need(Path(__file__).resolve() == self.root / "infra/gcp/schema27_mixed_policy_successor.py"
             and Path(app.__file__).resolve() == self.root / "infra/gcp/schema27_app_successor.py"
             and Path(app.contract.__file__).resolve() == self.root / "backend/scripts/notification_schema27_contract.py",
             "operator_origin")
        need(app.successor.controller_source(self.root) == (controller, stamp), "controller_source_changed")
        app.successor.verify_app_equivalence(self.root, self.target_root)
        app.successor.verify_controller_app_imports(self.root)
        for observer, root, pin in ((self.observer, self.target_root, target),
                                   (self.prior_observer, self.prior_root, prior)):
            need(observer.root == root and app.successor._git(root, "rev-parse", "HEAD").decode().strip() == pin.release
                 and not app.successor._git(root, "status", "--porcelain=v1", "--untracked-files=all"),
                 "source_checkout_changed")
            # Source27 qualification runs in that checkout's isolated interpreter.
            # Its descriptor is source-only, never scanner/native acceptance.
            observer.descriptor = app.sources.qualify(root, pin.release)
            rolling.release.verify_ci_checkout_contract(root)

    def _verify_sources(self, controller, stamp, controller_ci, target, prior):
        self._local_sources(controller, stamp, target, prior)
        key = (str(self.root), str(self.target_root), str(self.prior_root),
               controller, stamp, controller_ci, target, prior)
        verified = self._verified_source_tuple
        if verified is not None:
            need(verified == key, "source_tuple_changed")
            return
        # Only immutable successful CI/Git Build/image provenance is reused.
        # Stateful scanner/caller proof remains fresh in _resources each global.
        app.successor._native_ci(self.observer, controller, controller_ci)
        for observer, pin in ((self.observer, target), (self.prior_observer, prior)):
            app.native27.application27.verify_native_ci(observer, pin.ci_run_id)
            observer._verify_source_image_provenance(**asdict(pin))
            app.successor._native_ci(observer, pin.release, pin.ci_run_id)
        self._local_sources(controller, stamp, target, prior)
        need((str(self.root), str(self.target_root), str(self.prior_root)) == key[:3], "source_tuple_changed")
        # A failed/partial tuple is never published and cannot produce a hit.
        self._verified_source_tuple = key

    def _sources(self, plan):
        need(type(plan) is Plan and plan.identities == self.identity_profile, "plan_kind")
        need(Path(__file__).resolve() == self.root / "infra/gcp/schema27_mixed_policy_successor.py",
             "operator_origin")
        self._verify_sources(plan.controller_release, plan.controller_source_sha256,
                             plan.controller_ci_run_id, plan.source, plan.prior_source)
        need(self.observer.descriptor == plan.source_contract
             and self.prior_observer.descriptor == plan.prior_contract, "qualified_source_changed")

    def _target_template(self, plan, pin, current, prior):
        baseline = current | {"template": prior, "generation": pin.generation,
            "observedGeneration": pin.generation, "trafficStatuses": list(pin.traffic),
            "latestReadyRevision": pin.prior_revision, "latestCreatedRevision": pin.prior_revision,
            "reconciling": False, "terminalCondition": {"state": "CONDITION_SUCCEEDED"}}
        return stage_body(plan, pin, baseline)["template"]

    def verify_global(self, plan):
        plan.validate()
        scope.verify_journal(plan.custody(), self.journal)
        need(scope.journal_location(self.journal) == plan.journal_location, "journal_changed")
        self._sources(plan)
        plan.identities.verify_principals(lambda email: identities.read_identity(self.observer, email))
        app.successor.verify_historical_policy(self, plan.policies)
        self.observer.verify_policy_configuration(plan.target_policies())
        need(self._reachable(plan.services, plan.prior_source) == plan.reachable_revisions, "reachable_prior")
        need(self._resources(plan.workers_release) == plan.resources, "resources")
        from infra.gcp import schema27_mixed_document_proof

        proof = schema27_mixed_document_proof.validate_receipt(plan.identities, self.observer.last_document_authorization)
        record = "mixed-documents." + rolling.sha(proof)[:40]
        if self.journal.read(record) is None:
            self.journal.record(record, proof)
        app.runtime.observe_runtime(plan.custody(), self.journal, scope.neon.Purpose.AFTER_SCHEMA27, self.observer)
        observed = app.observe_catalog27(plan.database, plan.nonce, expected_liabilities=plan.protected_liabilities)
        need(observed.catalog_sha256 == plan.financial_sha256
             and observed.retained_acl_sha256 == plan.retained_acl_sha256, "database_catalog_acl")
        for pin in plan.services:
            need(self.service(pin.service)["template"].get("serviceAccount") == plan.identities.identity(pin.service).email,
                 "service_identity")
            self._verify_service(plan, pin)

    def capture(self, source, prior, *, controller_ci_run_id, nonce, workers_release,
                journal_directory, candidate_expense_secret, candidate_model_policy_secret):
        need(all(type(ref) is str and rolling.REF.fullmatch(ref)
                 for ref in (candidate_expense_secret, candidate_model_policy_secret)), "candidate_numeric_pair")
        need(type(source) is app.successor.SourcePin and type(prior) is app.successor.SourcePin,
             "source_kind")
        source.validate()
        prior.validate()
        need(source.release == SOURCE3F7 and prior.release == SOURCE70, "source_scope")
        need(scope._nonce(nonce) and type(controller_ci_run_id) is int and controller_ci_run_id > 0
             and type(workers_release) is str and re.fullmatch(r"[a-f0-9]{40}", workers_release), "capture_inputs")
        location = scope.journal_location(SimpleNamespace(directory=journal_directory))
        self.identity_profile.verify_principals(lambda email: identities.read_identity(self.observer, email))
        for service in rolling.release.SERVICES:
            need(self.service(service)["template"].get("serviceAccount") == self.identity_profile.identity(service).email,
                 "service_identity")
        controller, stamp = app.successor.controller_source(self.root)
        self._verify_sources(controller, stamp, controller_ci_run_id, source, prior)
        pins = tuple(self._pin(service, prior) for service in rolling.release.SERVICES)
        for pin in pins:
            need([secret.secret + ":" + secret.version for secret in pin.secrets
                  if secret.environment_name == "DATABASE_URL"] == [scope.RUNTIME_REFERENCE], "runtime_reference")
            app.native27.require_closed(self.service(pin.service)["template"],
                (*app.bridge.bridge.app26.CLOSED_FLAGS, *app.native27.F07_FLAGS))
        refs = {item.environment_name: item.secret + ":" + item.version for item in pins[0].secrets}
        need(all(old != target and old.split(":")[0] == target.split(":")[0] for old, target in (
            (refs["HIREWIZ_EXPENSE_POLICY_JSON"], candidate_expense_secret),
            (refs["LLM_MODEL_COST_POLICY_JSON"], candidate_model_policy_secret))), "candidate_route_or_pair_changed")
        # This performs historical audit + fresh target validation and the four
        # hour rollout window before any catalog capture or sendable plan.
        policies, candidate = self._capture_policies(pins, candidate_expense_secret, candidate_model_policy_secret)
        resources = self._resources(workers_release)
        database = scope.capture_database_pin(nonce)
        observed = app.observe_catalog27(database, nonce)
        application = app.plans.App27Plan(source=source, prior_source=prior,
            source_contract=self.observer.descriptor, prior_contract=self.prior_observer.descriptor,
            controller_release=controller, controller_source_sha256=stamp,
            controller_ci_run_id=controller_ci_run_id, nonce=nonce, database=database,
            runtime_uid=self.identity_profile.transition.old_api.uid, workers_release=workers_release,
            financial_sha256=observed.catalog_sha256, retained_acl_sha256=observed.retained_acl_sha256,
            policies=policies, services=pins, resources=resources,
            protected_liabilities=observed.protected_liabilities, reachable_revisions=self._reachable(pins, prior))
        result = Plan(application, self.identity_profile, location, candidate, ())
        stamps = tuple(CandidateTemplate(pin.service, rolling.template_sha(
            stage_body(result, pin, self.service(pin.service))["template"], pin.service)) for pin in result.services)
        result = replace(result, candidate_templates=stamps)
        result.validate()
        need(all(self.revision(pin.service, result.revision(pin.service)) is None for pin in result.services),
             "candidate_exists")
        return result


def _acknowledged(plan, journal):
    """V2 never turns an unknown outcome into a retained native ACK."""
    for pin in plan.services:
        for phase in ("stage", "promote", "rollback"):
            key = phase + "." + pin.service
            started, ack, checked = (journal.read(key + suffix) for suffix in (".started", ".ack", ".checked"))
            if started is not None:
                need(ack is not None and ack.get("request_sha256") == started.get("request_sha256")
                     and rolling.OPERATION.fullmatch(ack.get("operation", "")), "missing_native_ack_reconcile_only")
                need(checked is None or checked.get("operation_ack_retained") is True,
                     "unacknowledged_checked_action")
            else:
                need(ack is None and checked is None, "orphan_action")


def _stage(plan, boundary, journal, pin):
    key = "stage." + pin.service
    if journal.read(key + ".started") is not None:
        return rolling.action(plan, boundary, journal, pin, "stage", reconcile=True)
    boundary.verify_global(plan)
    current = boundary.service(pin.service)
    need(rolling.sha(boundary.iam(pin.service)) == pin.iam_sha256, "iam_changed")
    rolling._old_revision(boundary, pin)
    need(boundary.revision(pin.service, plan.revision(pin.service)) is None, "candidate_exists")
    body = stage_body(plan, pin, current)
    started = {"phase": "stage", "request_sha256": rolling.sha(body), "mask": "template,traffic",
        "template_sha256": rolling.template_sha(body["template"], pin.service), "traffic": body["traffic"],
        "expected_generation": int(current["generation"]) + 1, "service_uid": pin.uid,
        "previous_etag": current["etag"], "replay_authorized": False}
    need(boundary.service(pin.service) == current, "native_preimage_changed")
    journal.record(key + ".started", started)
    ack = boundary.patch(pin.service, body, "template,traffic")
    need(type(ack.get("name")) is str and rolling.OPERATION.fullmatch(ack["name"]), "ack_unmapped")
    journal.record(key + ".ack", {"operation": ack["name"], "request_sha256": started["request_sha256"]})
    return rolling.action(plan, boundary, journal, pin, "stage", reconcile=True)


def execute(mode, plan, boundary, journal):
    need(mode in {"stage", "advance", "observe", "reconcile", "rollback"}
         and type(plan) is Plan and type(boundary) is NativeBoundary, "exact_native_owner")
    plan.validate()
    scope.verify_journal(plan.custody(), journal)
    need(scope.journal_location(journal) == plan.journal_location, "journal_changed")
    with scope.lifecycle_lock(journal):
        need(journal.read(scope.MIGRATION_FENCE) is None, "migration_started")
        boundary.journal = journal
        _acknowledged(plan, journal)
        if pending := app.bridge._pending_acknowledged(plan, boundary, journal):
            return pending
        boundary.verify_global(plan)
        if mode == "rollback" or mode in {"observe", "reconcile"} and journal.read(app.historical.ROLLBACK_INTENT):
            result = app.historical.rollback_app(plan, boundary, journal, reconcile=mode != "rollback")
        elif mode in {"observe", "reconcile"}:
            result = rolling.reconcile(plan, boundary, journal)
        else:
            with journal.locked():
                need(journal.read(app.historical.ROLLBACK_INTENT) is None, "rollback_started")
                for pin in plan.services:
                    if journal.read("promote." + pin.service + ".started"):
                        need(mode == "advance", "stage_passed")
                        continue
                    if not _stage(plan, boundary, journal, pin):
                        return {"status": "same_native_operation_pending", "service": pin.service,
                                "phase": "stage", "rolling_release_complete": False}
                boundary.verify_global(plan)
            result = rolling.advance(plan, boundary, journal) if mode == "advance" else {
                "status": "mixed_policy_app_staged", "rolling_release_complete": False}
        return result | {"schema": app.plans.SCHEMA, "mixed_identity_profile_sha256": plan.identities.digest,
                         "migration_authorized": False, "payment_activation_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "stage", "advance", "observe", "reconcile", "rollback"))
    for name in ("root", "target-root", "prior-root", "plan", "journal", "identity-profile"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--identity-profile-sha256", required=True)
    parser.add_argument("--capture-config", type=Path)
    parser.add_argument("--capture-config-sha256")
    args = parser.parse_args()
    boundary = None
    try:
        need(Path(__file__).resolve() == args.root.resolve() / "infra/gcp/schema27_mixed_policy_successor.py",
             "operator_origin")
        read = app.bridge.bridge.app26.initial._read_pinned
        profile = identities.Profile.from_dict(rolling.release._json(read(
            args.identity_profile, args.identity_profile_sha256)))
        profile.verify_custody()
        boundary = NativeBoundary(args.root, args.target_root, args.prior_root, profile)
        if args.mode == "capture":
            pins, options = app.bridge._capture_config(read(args.capture_config, args.capture_config_sha256))
            need(set(options) == {"controller_ci_run_id", "nonce", "workers_release",
                 "candidate_expense_secret", "candidate_model_policy_secret"}, "capture_options")
            plan = boundary.capture(*pins, **options, journal_directory=args.journal)
            boundary.journal = app.bridge._bind_new(plan, args.journal)
            boundary.verify_global(plan)
            result = {"status": "mixed_policy_app_captured", "native_mutations": 0,
                      "plan_sha256": app.bridge._write_plan(args.plan, plan)}
        else:
            plan = load(read(args.plan, args.plan_sha256), args.plan_sha256)
            need(plan.identities == profile, "profile_changed")
            result = execute(args.mode, plan, boundary, rolling.Journal(args.journal, plan.custody()))
        print(rolling.canonical(result).decode())
        return 0
    except Exception as error:  # noqa: BLE001 - only the fixed nonsecret projection is emitted.
        print(rolling.canonical(diagnostic(error, args.mode)).decode())
        return 1
    finally:
        if boundary is not None:
            boundary.close()


if __name__ == "__main__":
    raise SystemExit(main())
