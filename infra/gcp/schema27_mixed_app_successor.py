"""Source27 app successors retaining the explicitly promoted dedicated API identity.

The existing exact27 source/database implementation is composed unchanged. This
distinct plan keeps real shared-worker and dedicated-API UIDs separate. It never
changes IAM, account identity, payment bindings, schema or worker source proof.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from infra.gcp import schema27_api_identity_operator as rehearsal
from infra.gcp import schema27_app_successor as app
from infra.gcp import schema27_forward_diagnostics as diagnostics
from infra.gcp import schema27_mixed_identity_profile as identities

rolling, scope, need = app.rolling, app.scope, identities.need
KIND = "hirewiz_schema27_mixed_app_successor_v1"
API = rehearsal.API


@dataclass(frozen=True)
class Plan:
    application: app.plans.App27Plan
    identities: identities.Profile
    journal_location: dict
    schema_contract: str = KIND

    def __getattr__(self, name):
        if name in {field.name for field in fields(app.plans.App27Plan)} - {"schema_contract"}:
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
    def candidate_policies(self):
        return None

    @property
    def candidate_employer_cache(self):
        return None

    def revision(self, service):
        return self.application.revision(service)

    def pin(self, service):
        return self.application.pin(service)

    def target_policies(self):
        return self.application.policies

    def disabled_flags(self):
        return self.application.disabled_flags()

    def promoted_aliases(self, pin, rows):
        return self.application.promoted_aliases(pin, rows)

    def validate(self):
        need(type(self) is Plan and self.schema_contract == KIND
             and type(self.application) is app.plans.App27Plan
             and type(self.identities) is identities.Profile, "app_plan_kind")
        self.application.validate()
        self.identities.validate()
        need(self.application.runtime_uid == self.identities.transition.old_api.uid, "shared_worker_uid")
        need(all(pin.uid == self.identities.promotion.before["services"][pin.service]["uid"]
                 for pin in self.application.services), "app_service_uid")
        location = self.journal_location
        need(type(location) is dict and set(location) == {"path", "device", "inode"}
             and type(location["path"]) is str and Path(location["path"]).is_absolute()
             and all(type(location[key]) is int and location[key] >= 0 for key in ("device", "inode")),
             "app_original_journal_pin")

    def custody(self):
        self.validate()
        return scope.BridgeCustody(self.nonce, rolling.sha(asdict(self)), self.database)


def load(raw, digest):
    need(type(raw) is bytes and len(raw) <= 524288 and identities.readback._hash(digest)
         and hashlib.sha256(raw).hexdigest() == digest, "app_private_plan")
    value = rolling.release._json(raw)
    need(type(value) is dict and set(value) == {field.name for field in fields(Plan)}
         and value["schema_contract"] == KIND, "app_plan_kind")
    child = rolling.canonical(value["application"])
    plan = Plan(app.plans.load(child, hashlib.sha256(child).hexdigest()),
                identities.Profile.from_dict(value["identities"]), value["journal_location"])
    need(rolling.canonical(asdict(plan)) == rolling.canonical(value), "app_canonical_plan")
    plan.validate()
    return plan


def stage_body(plan, pin, current):
    """API-specific stock-shaped builder; never substitute a shared account."""
    need(type(plan) is Plan, "app_plan_kind")
    identity = plan.identities.identity(pin.service)
    need(current["template"].get("serviceAccount") == identity.email, "app_service_identity")
    if pin.service != API:
        return rolling.stage_body(plan, pin, current)
    rolling._ready(current, pin)
    need(int(current["generation"]) == pin.generation
         and rolling.template_sha(current["template"], pin.service) == pin.template_sha256
         and rolling.static_sha(current) == pin.static_sha256
         and rolling.pinned_traffic(pin.service, current, prior_revision=pin.prior_revision) == list(pin.traffic),
         "app_preimage")
    template = copy.deepcopy(current["template"])
    rolling.verify_policy_bindings(plan.policies, pin, template)
    env = rolling.environment(template)
    need(all(env.get(name, {"value": "false"}).get("value") == "false" for name in plan.disabled_flags())
         and env.get("SERVICE_ROLE", {}).get("value") == "api", "app_admission_closed")
    for name in sorted(set(plan.disabled_flags()) - set(rolling.OFF)):
        env[name] = {"name": name, "value": "false"}
    for secret in pin.secrets:
        ref = env.get(secret.environment_name, {}).get("valueSource", {}).get("secretKeyRef", {})
        need(ref.get("secret") == secret.secret and ref.get("version") in {secret.version, "latest"}, "app_secret")
        ref["version"] = secret.version
    need({name for name, row in env.items() if "valueSource" in row}
         == {secret.environment_name for secret in pin.secrets}, "app_secret_inventory")
    for name, value in {"APP_RELEASE": plan.release, "AUTO_DB_MIGRATE": "false",
                        "PGSSLROOTCERT": rolling.release.SAFE_ENV["PGSSLROOTCERT"]}.items():
        env[name] = {"name": name, "value": value}
    template["revision"] = plan.revision(pin.service)
    template.setdefault("labels", {})["hirewiz-source"] = plan.release
    container = template["containers"][0]
    ports = container.get("ports", [])
    need(len(ports) == 1 and type(ports[0].get("containerPort")) is int
         and ports[0]["containerPort"] in {8080, 10000}, "app_port")
    container.update(image=plan.image, env=list(env.values()), command=["python"],
        args=["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(ports[0]["containerPort"])])
    need(template["serviceAccount"] == identity.email, "app_service_identity")
    return {"name": current["name"], "etag": current["etag"], "template": template,
            "traffic": rolling._traffic_for(plan, pin, False)}


MixedSourceBoundary = identities.MixedSourceBoundary


class NativeBoundary(app.NativeApp27Boundary):
    def __init__(self, root, target_root, prior_root, profile):
        need(type(profile) is identities.Profile, "profile_kind")
        super().__init__(root, target_root, prior_root)
        self.identity_profile = profile
        self.observer = MixedSourceBoundary(self.target_root, profile)
        self.prior_observer = MixedSourceBoundary(self.prior_root, profile)

    def _reachable(self, pins, prior_source):
        result = []
        for pin in pins:
            baseline = self._reachable_revision(pin.service, pin.prior_revision)
            for revision in sorted({row["revision"] for row in pin.traffic}):
                value = self._reachable_revision(pin.service, revision)
                need(value is not None and baseline is not None
                     and value.get("name") == rolling.PARENT + "/services/" + pin.service + "/revisions/" + revision
                     and any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
                             for row in value.get("conditions", [])), "app_prior_revision_unready")
                env = rolling.environment(value)
                need(value.get("serviceAccount") == self.identity_profile.identity(pin.service).email
                     and env.get("APP_RELEASE") == {"name": "APP_RELEASE", "value": prior_source.release}
                     and value["containers"][0].get("image") == rehearsal.readback.IMAGE + "@" + prior_source.image_digest
                     and all(env.get(name, {"value": "false"}).get("value") == "false"
                             for name in app.historical.CLOSED_FLAGS), "app_prior_source_identity")
                outputs = app.historical.maintenance.REVISION_READ_OUTPUTS
                template = {key: item for key, item in value.items() if key not in outputs}
                expected = copy.deepcopy({key: item for key, item in baseline.items() if key not in outputs | {"revision"}})
                compared = copy.deepcopy({key: item for key, item in template.items() if key != "revision"})
                expected_env, compared_env = rolling.environment(expected), rolling.environment(compared)
                for name in app.historical.PILOT_FLAGS:
                    original, actual = expected_env.get(name, {"value": "false"}), compared_env.get(name, {"value": "false"})
                    need(actual.get("value") in {"true", "false"}
                         and not (original.get("value") != "true" and actual.get("value") == "true"),
                         "app_prior_pilot_expansion")
                    if name in compared_env:
                        compared_env[name] = copy.deepcopy(original)
                compared["containers"][0]["env"] = list(compared_env.values())
                need(compared == expected, "app_prior_configuration_differs")
                result.append(app.historical.ReachablePin(pin.service, revision, rolling.template_sha(template, pin.service)))
        return tuple(result)

    def _sources(self, plan):
        need(type(plan) is Plan and plan.identities == self.identity_profile, "app_plan_kind")
        need(Path(__file__).resolve() == self.root / "infra/gcp/schema27_mixed_app_successor.py"
             and Path(identities.__file__).resolve() == self.root / "infra/gcp/schema27_mixed_identity_profile.py",
             "app_controller_origin")
        self._verify_sources(plan.controller_release, plan.controller_source_sha256, plan.controller_ci_run_id,
                             plan.source, plan.prior_source)
        need(self.observer.descriptor == plan.source_contract
             and self.prior_observer.descriptor == plan.prior_contract, "app_qualified_source_changed")

    def _target_template(self, plan, pin, current, prior):
        baseline = current | {"template": prior, "generation": pin.generation, "observedGeneration": pin.generation,
            "trafficStatuses": list(pin.traffic), "latestReadyRevision": pin.prior_revision,
            "latestCreatedRevision": pin.prior_revision, "reconciling": False,
            "terminalCondition": {"state": "CONDITION_SUCCEEDED"}}
        return stage_body(plan, pin, baseline)["template"]

    def verify_global(self, plan):
        plan.validate()
        scope.verify_journal(plan.custody(), self.journal)
        need(scope.journal_location(self.journal) == plan.journal_location, "app_original_journal_changed")
        self._sources(plan)
        plan.identities.verify_principals(lambda email: identities.read_identity(self.observer, email))
        self.observer.verify_policy_configuration(plan.policies)
        need(self._reachable(plan.services, plan.prior_source) == plan.reachable_revisions, "app_reachable_prior")
        need(self._resources(plan.workers_release) == plan.resources, "app_resources")
        # Retain the NEW forward authorization receipt alongside its independently
        # scoped binary evidence; do not discard it behind the legacy resource pin.
        from infra.gcp import schema27_mixed_document_proof

        proof = schema27_mixed_document_proof.validate_receipt(plan.identities, self.observer.last_document_authorization)
        record = "mixed-documents." + rolling.sha(proof)[:40]
        if self.journal.read(record) is None:
            self.journal.record(record, proof)
        app.runtime.observe_runtime(plan.custody(), self.journal, scope.neon.Purpose.AFTER_SCHEMA27, self.observer)
        observed = app.observe_catalog27(plan.database, plan.nonce, expected_liabilities=plan.protected_liabilities)
        need(observed.catalog_sha256 == plan.financial_sha256
             and observed.retained_acl_sha256 == plan.retained_acl_sha256, "app_database_catalog_acl")
        for pin in plan.services:
            need(self.service(pin.service)["template"].get("serviceAccount") == plan.identities.identity(pin.service).email,
                 "app_service_identity")
            self._verify_service(plan, pin)

    def capture(self, *args, **kwargs):
        directory = kwargs.pop("journal_directory")
        # Reuse the existing owned/private directory validation, then bind it in
        # the externally reviewed plan rather than an editable binding alone.
        location = scope.journal_location(type("Location", (), {"directory": directory})())
        self.identity_profile.verify_principals(lambda email: identities.read_identity(self.observer, email))
        for service in rolling.release.SERVICES:
            need(self.service(service)["template"].get("serviceAccount") == self.identity_profile.identity(service).email,
                 "app_service_identity")
        application = super().capture(*args, **kwargs)
        result = Plan(application, self.identity_profile, location)
        result.validate()
        return result


def _stage(plan, boundary, journal, pin):
    """Stock once/ACK/readback protocol, selecting only the new typed stage builder."""
    name = "stage." + pin.service
    if journal.read(name + ".started") is not None:
        return rolling.action(plan, boundary, journal, pin, "stage", reconcile=True)
    boundary.verify_global(plan)
    current = boundary.service(pin.service)
    rolling.need(rolling.sha(boundary.iam(pin.service)) == pin.iam_sha256)
    rolling._old_revision(boundary, pin)
    need(boundary.revision(pin.service, plan.revision(pin.service)) is None, "app_candidate_exists")
    body = stage_body(plan, pin, current)
    started = {"phase": "stage", "request_sha256": rolling.sha(body), "mask": "template,traffic",
        "template_sha256": rolling.template_sha(body["template"], pin.service), "traffic": body["traffic"],
        "expected_generation": int(current["generation"]) + 1, "service_uid": pin.uid,
        "previous_etag": current["etag"], "replay_authorized": False}
    need(boundary.service(pin.service) == current, "app_native_preimage_changed")
    journal.record(name + ".started", started)
    ack = boundary.patch(pin.service, body, "template,traffic")
    need(type(ack.get("name")) is str and rolling.OPERATION.fullmatch(ack["name"]), "app_ack_unmapped")
    journal.record(name + ".ack", {"operation": ack["name"], "request_sha256": started["request_sha256"]})
    return rolling.action(plan, boundary, journal, pin, "stage", reconcile=True)


def execute(mode, plan, boundary, journal):
    need(mode in {"stage", "advance", "observe", "reconcile", "rollback"}
         and type(plan) is Plan and type(boundary) is NativeBoundary, "app_exact_native_owner")
    plan.validate()
    scope.verify_journal(plan.custody(), journal)
    need(scope.journal_location(journal) == plan.journal_location, "app_original_journal_changed")
    with scope.lifecycle_lock(journal):
        need(journal.read(scope.MIGRATION_FENCE) is None, "app_foreign_migration_fence")
        boundary.journal = journal
        if pending := app.bridge._pending_acknowledged(plan, boundary, journal):
            return pending
        boundary.verify_global(plan)
        if mode == "rollback" or mode in {"observe", "reconcile"} and journal.read(app.historical.ROLLBACK_INTENT):
            result = app.historical.rollback_app(plan, boundary, journal, reconcile=mode != "rollback")
        elif mode in {"observe", "reconcile"}:
            result = rolling.reconcile(plan, boundary, journal)
        else:
            with journal.locked():
                need(journal.read(app.historical.ROLLBACK_INTENT) is None, "app_rollback_started")
                for pin in plan.services:
                    if journal.read("promote." + pin.service + ".started"):
                        need(mode == "advance", "app_stage_passed")
                        continue
                    if not _stage(plan, boundary, journal, pin):
                        return {"status": "same_native_operation_pending", "service": pin.service, "phase": "stage"}
                boundary.verify_global(plan)
            result = (rolling.advance(plan, boundary, journal) if mode == "advance" else {
                "status": "mixed_identity_app_staged", "rolling_release_complete": False})
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
        need(Path(__file__).resolve() == args.root.resolve() / "infra/gcp/schema27_mixed_app_successor.py", "app_controller_origin")
        read = app.bridge.bridge.app26.initial._read_pinned
        raw_profile = read(args.identity_profile, args.identity_profile_sha256)
        profile = identities.Profile.from_dict(rolling.release._json(raw_profile))
        profile.verify_custody()
        boundary = NativeBoundary(args.root, args.target_root, args.prior_root, profile)
        if args.mode == "capture":
            raw = read(args.capture_config, args.capture_config_sha256)
            pins, options = app.bridge._capture_config(raw)
            need(set(options) == {"controller_ci_run_id", "nonce", "workers_release"}, "app_only_options")
            plan = boundary.capture(*pins, **options, journal_directory=args.journal)
            boundary.journal = app.bridge._bind_new(plan, args.journal)
            boundary.verify_global(plan)
            result = {"status": "mixed_identity_app_captured", "native_mutations": 0,
                      "plan_sha256": app.bridge._write_plan(args.plan, plan)}
        else:
            plan = load(read(args.plan, args.plan_sha256), args.plan_sha256)
            need(plan.identities == profile, "app_profile_changed")
            journal = rolling.Journal(args.journal, plan.custody())
            result = execute(args.mode, plan, boundary, journal)
        print(rolling.canonical(result).decode())
        return 0
    except Exception as error:
        print(rolling.canonical(diagnostics.diagnostic(error, args.mode)).decode())
        return 1
    finally:
        if boundary is not None:
            try:
                boundary.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
