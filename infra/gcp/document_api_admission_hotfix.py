"""Schema21 API-only legacy resume admission hotfix.

The native Git/CI/image verifier, database observer, Cloud Run transport and
durable one-send journal come from the existing rolling release. This operator
never changes a worker, schema, credential, queue, or document definition.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path(__file__).resolve().parents[2] / "backend"),
]
import email_password_schema21_release as schema21
import rolling_release as rolling

API = rolling.release.SERVICES[0]
WORKERS = rolling.release.SERVICES[1:]
CONTRACT = "hirewiz_document_api_admission_hotfix_v1"
SCHEMA = schema21.contract.SCHEMA
IMAGE = re.compile(
    r"us-central1-docker\.pkg\.dev/ai-resume-parser-482412/"
    r"cloud-run-source-deploy/hirewiz@sha256:[a-f0-9]{64}\Z"
)


@dataclass(frozen=True)
class HotfixPlan(rolling.RollingPlan):
    schema_contract: Literal["hirewiz_document_api_admission_hotfix_v1"] = CONTRACT
    database_schema: Literal["20261023_0021"] = SCHEMA
    prior_images: tuple[str, str, str] = ("", "", "")
    api_target_template_sha256: str = ""

    def validate(self) -> None:
        super().validate()
        rolling.need(
            type(self) is HotfixPlan
            and self.schema_contract == CONTRACT
            and self.database_schema == SCHEMA
            and type(self.prior_images) is tuple
            and len(self.prior_images) == len(rolling.release.SERVICES)
            and all(type(image) is str and IMAGE.fullmatch(image) for image in self.prior_images)
            and self.image != self.prior_images[0]
            and self.candidate_policies is None
            and rolling.HASH.fullmatch(self.api_target_template_sha256),
            "hotfix_plan_invalid",
        )

    def disabled_flags(self) -> tuple[str, ...]:
        return ("RESUME_DIRECT_UPLOAD_ENABLED",)


def _secret_refs(boundary: NativeHotfixBoundary, pin: rolling.ServicePin) -> None:
    for secret in pin.secrets:
        rolling.need(
            boundary._reference(secret.secret, secret.version) == secret.version,
            "hotfix_secret_reference_changed",
        )


def _candidate_template(plan: HotfixPlan, current: dict[str, Any]) -> dict[str, Any]:
    pin = plan.pin(API)
    rolling._ready(current, pin)
    rolling.need(
        rolling.template_sha(current["template"], API) == pin.template_sha256
        and current["template"].get("serviceAccount") == rolling.RUNTIME,
        "hotfix_api_preimage_changed",
    )
    template = copy.deepcopy(current["template"])
    containers = template.get("containers", [])
    rolling.need(type(containers) is list and len(containers) == 1, "hotfix_api_template_invalid")
    container = containers[0]
    rows = container.get("env", [])
    env = rolling.environment(template)
    rolling.need(
        env.get("SERVICE_ROLE", {}).get("value") == "api"
        and env.get("AUTO_DB_MIGRATE", {}).get("value") == "false"
        and env.get("APP_RELEASE", {}).get("value") == pin.prior_release
        and IMAGE.fullmatch(container.get("image", ""))
        and container["image"] == plan.prior_images[0],
        "hotfix_api_template_invalid",
    )
    changed = {"APP_RELEASE": plan.release, "RESUME_DIRECT_UPLOAD_ENABLED": "false"}
    replaced: set[str] = set()
    for index, row in enumerate(rows):
        if row["name"] in changed:
            rolling.need("valueSource" not in row, "hotfix_flag_or_release_is_secret")
            rows[index] = {**row, "value": changed[row["name"]]}
            replaced.add(row["name"])
    for name in sorted(changed.keys() - replaced):
        rows.append({"name": name, "value": changed[name]})
    template["revision"] = plan.revision(API)
    template.setdefault("labels", {})["hirewiz-source"] = plan.release
    container["image"] = plan.image
    rolling.need(
        rolling.environment(template)["RESUME_DIRECT_UPLOAD_ENABLED"]["value"] == "false",
        "hotfix_upload_not_closed",
    )
    return template


def _pin(boundary: NativeHotfixBoundary, service: str) -> tuple[rolling.ServicePin, str]:
    value = boundary.service(service)
    rolling.worker_iam(service, boundary.iam(service))
    rolling.need(
        type(value) is dict
        and value.get("name") == rolling.PARENT + "/services/" + service
        and value.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
        and value.get("reconciling", False) is False
        and str(value.get("generation")) == str(value.get("observedGeneration"))
        and re.fullmatch(r"[a-f0-9-]{36}", value.get("uid", "")),
        "hotfix_service_not_ready",
    )
    revision = rolling.short_revision(value["latestReadyRevision"], service)
    rolling.need(
        revision == rolling.short_revision(value["latestCreatedRevision"], service),
        "hotfix_unresolved_revision",
    )
    traffic = tuple(rolling.pinned_traffic(service, value, prior_revision=revision))
    old = boundary.revision(service, revision)
    rolling.need(old is not None, "hotfix_prior_revision_missing")
    assert old is not None
    image = old.get("containers", [{}])[0].get("image", "")
    rolling.need(IMAGE.fullmatch(image), "hotfix_prior_image_unverified")
    keys = tuple(sorted(set(value["template"]) - {"revision"}))
    env = rolling.environment(value["template"])
    secrets = []
    for row in env.values():
        if "valueSource" in row:
            ref = row["valueSource"].get("secretKeyRef", {})
            secret, version = ref.get("secret", ""), ref.get("version", "")
            rolling.need(
                re.fullmatch(r"[a-zA-Z0-9_-]{1,255}", secret)
                and re.fullmatch(r"[1-9][0-9]{0,18}", version)
                and boundary._reference(secret, version) == version,
                "hotfix_secret_reference_unverified",
            )
            secrets.append(rolling.SecretPin(row["name"], secret, version))
    pin = rolling.ServicePin(
        service, value["uid"], int(value["generation"]), revision,
        env.get("APP_RELEASE", {}).get("value", ""),
        rolling.template_sha(value["template"], service), rolling.static_sha(value),
        rolling.sha(boundary.iam(service)),
        rolling.sha({key: old.get(key) for key in keys}), keys, traffic,
        tuple(sorted(secrets, key=lambda item: item.environment_name)),
    )
    rolling.need(
        old.get("containers", [{}])[0].get("image") == value["template"]["containers"][0].get("image")
        and re.fullmatch(r"[a-f0-9]{40}", pin.prior_release),
        "hotfix_prior_source_or_image_changed",
    )
    return pin, image


class NativeHotfixBoundary(schema21.NativeSchema21Boundary):
    def __init__(self, root: Path):
        super().__init__(root, SCHEMA)

    def capture_hotfix(
        self, *, release: str, image_digest: str, build_id: str, ci_run_id: int,
        nonce: str, observer_secret: str, retired_migration_secret: str,
        workers_release: str,
    ) -> HotfixPlan:
        self.observer.verify_source_image(
            release=release, image_digest=image_digest, build_id=build_id, ci_run_id=ci_run_id
        )
        account = self.observer._cloud("iam", "service-accounts", "describe", rolling.RUNTIME)
        uid = account.get("uniqueId")
        self._identity(uid)
        pairs = [_pin(self, service) for service in rolling.release.SERVICES]
        pins, images = tuple(item[0] for item in pairs), tuple(item[1] for item in pairs)
        api_env = rolling.environment(self.service(API)["template"])
        refs = {secret.environment_name: secret.secret + ":" + secret.version
                for secret in pins[0].secrets}
        rolling.need(
            all(key in refs for key in ("DATABASE_URL", "HIREWIZ_EXPENSE_POLICY_JSON",
                                           "LLM_MODEL_COST_POLICY_JSON", "LLM_API_KEY"))
            and all(next(item for item in pins if item.service == service).secrets
                    for service in WORKERS),
            "hotfix_required_secret_missing",
        )
        expense, model = refs["HIREWIZ_EXPENSE_POLICY_JSON"], refs["LLM_MODEL_COST_POLICY_JSON"]
        policies = rolling.PolicyPins(
            expense, hashlib.sha256(self.observer._secret(expense).encode()).hexdigest(),
            model, hashlib.sha256(self.observer._secret(model).encode()).hexdigest(),
            refs["LLM_API_KEY"], api_env["LLM_MODEL"]["value"], api_env["LLM_API_BASE"]["value"],
        )
        for pin in pins:
            rolling.verify_policy_bindings(policies, pin, self.service(pin.service)["template"])
        financial = self._target(refs["DATABASE_URL"], observer_secret, retired_migration_secret)
        plan = HotfixPlan(
            release, image_digest, build_id, ci_run_id, nonce, uid,
            refs["DATABASE_URL"], observer_secret, retired_migration_secret,
            workers_release, financial, policies, pins,
            prior_images=images, api_target_template_sha256="0" * 64,
        )
        target = _candidate_template(plan, self.service(API))
        plan = replace(plan, api_target_template_sha256=rolling.template_sha(target, API))
        plan.validate()
        self.verify_global(plan)
        _prior_api(plan, self)
        rolling.need(self.revision(API, plan.revision(API)) is None,
                     "hotfix_candidate_revision_exists")
        return plan

    def verify_global(self, plan: HotfixPlan) -> None:
        plan.validate()
        self.observer.verify_source_image(
            release=plan.release, image_digest=plan.image_digest,
            build_id=plan.build_id, ci_run_id=plan.ci_run_id,
        )
        self.observer.verify_policy_configuration(plan.policies)
        self._identity(plan.runtime_uid)
        self.observer.verify_document_configuration(SimpleNamespace(
            release=plan.workers_release, runtime_service_account=rolling.RUNTIME
        ))
        rolling.need(
            self._target(plan.runtime_secret, plan.observer_secret,
                         plan.retired_migration_secret) == plan.financial_sha256,
            "hotfix_financial_state_changed",
        )
        for index, pin in enumerate(plan.services):
            rolling.worker_iam(pin.service, self.iam(pin.service))
            rolling.need(rolling.sha(self.iam(pin.service)) == pin.iam_sha256,
                         "hotfix_iam_changed")
            old = self.revision(pin.service, pin.prior_revision)
            rolling._old_revision(self, pin)
            rolling.need(
                old is not None and old.get("containers", [{}])[0].get("image") == plan.prior_images[index],
                "hotfix_prior_image_changed",
            )
            _secret_refs(self, pin)
            if pin.service in WORKERS:
                value = self.service(pin.service)
                rolling._ready(value, pin)
                rolling.need(
                    int(value["generation"]) == pin.generation
                    and rolling.template_sha(value["template"], pin.service) == pin.template_sha256
                    and rolling.static_sha(value) == pin.static_sha256
                    and rolling.pinned_traffic(pin.service, value, prior_revision=pin.prior_revision)
                    == list(pin.traffic)
                    and rolling.short_revision(value["latestReadyRevision"], pin.service) == pin.prior_revision
                    and rolling.short_revision(value["latestCreatedRevision"], pin.service) == pin.prior_revision,
                    "hotfix_worker_changed",
                )
        api = self.service(API)
        rolling._ready(api, plan.pin(API))
        rolling.need(
            rolling.static_sha(api) == plan.pin(API).static_sha256,
            "hotfix_api_static_changed",
        )


def _prior_api(plan: HotfixPlan, boundary: NativeHotfixBoundary) -> None:
    pin = plan.pin(API)
    value = boundary.service(API)
    rolling._ready(value, pin)
    rolling.need(
        int(value["generation"]) == pin.generation
        and rolling.template_sha(value["template"], API) == pin.template_sha256
        and rolling.static_sha(value) == pin.static_sha256
        and rolling.pinned_traffic(API, value, prior_revision=pin.prior_revision) == list(pin.traffic)
        and rolling.short_revision(value["latestReadyRevision"], API) == pin.prior_revision
        and rolling.short_revision(value["latestCreatedRevision"], API) == pin.prior_revision,
        "hotfix_api_prior_changed",
    )


def _traffic(plan: HotfixPlan, phase: str) -> list[dict[str, Any]]:
    return rolling._traffic_for(
        plan, plan.pin(API), phase == "promote", rollback=phase == "rollback"
    )


def _readback(
    plan: HotfixPlan, boundary: NativeHotfixBoundary, phase: str,
    started: dict[str, Any],
) -> None:
    boundary.verify_global(plan)
    pin = plan.pin(API)
    value = boundary.service(API)
    rolling._ready(value, pin)
    rolling.need(
        int(value["generation"]) == started["expected_generation"]
        and rolling.template_sha(value["template"], API) == plan.api_target_template_sha256
        and rolling.static_sha(value) == pin.static_sha256
        and rolling.pinned_traffic(API, value) == started["traffic"]
        and rolling.short_revision(value["latestReadyRevision"], API) == plan.revision(API)
        and rolling.short_revision(value["latestCreatedRevision"], API) == plan.revision(API)
        and rolling.environment(value["template"])["RESUME_DIRECT_UPLOAD_ENABLED"].get("value") == "false",
        "hotfix_readback_changed",
    )
    candidate = boundary.revision(API, plan.revision(API))
    rolling.need(candidate is not None, "hotfix_candidate_revision_missing")
    assert candidate is not None
    keys = set(value["template"]) - {"revision"}
    rolling.need(
        {key: candidate.get(key) for key in keys}
        == {key: value["template"][key] for key in keys}
        and candidate.get("containers", [{}])[0].get("image") == plan.image
        and any(item.get("type") == "Ready" and item.get("state") == "CONDITION_SUCCEEDED"
                for item in candidate.get("conditions", [])),
        "hotfix_candidate_revision_changed",
    )
    boundary.health(plan, API, plan.revision(API), plan.release, plan.tag)
    if phase == "rollback":
        boundary.health(plan, API, pin.prior_revision, pin.prior_release, plan.rollback_tag)


def _action(
    plan: HotfixPlan, boundary: NativeHotfixBoundary, journal: rolling.Journal,
    phase: str, *, reconcile: bool = False,
) -> bool:
    rolling.need(phase in {"stage", "promote", "rollback"}, "hotfix_phase_invalid")
    name = phase + ".api"
    started, ack, checked = (journal.read(name + suffix)
                             for suffix in (".started", ".ack", ".checked"))
    boundary.verify_global(plan)
    if checked is not None:
        rolling.need(started is not None, "hotfix_checked_marker_without_start")
        if not any(journal.read(later + ".api.started") for later in
                   ({"stage": ("promote", "rollback"), "promote": ("rollback",), "rollback": ()}[phase])):
            _readback(plan, boundary, phase, started)
        return True
    if started is None:
        rolling.need(not reconcile, "hotfix_reconcile_never_starts_write")
        current = boundary.service(API)
        if phase == "stage":
            _prior_api(plan, boundary)
            rolling.need(boundary.revision(API, plan.revision(API)) is None,
                         "hotfix_candidate_revision_exists")
            template = _candidate_template(plan, current)
            rolling.need(rolling.template_sha(template, API) == plan.api_target_template_sha256,
                         "hotfix_candidate_template_changed")
            body = {"name": current["name"], "etag": current["etag"],
                    "template": template, "traffic": _traffic(plan, phase)}
            mask = "template,traffic"
        else:
            rolling.need(journal.read("stage.api.checked") is not None,
                         "hotfix_stage_must_be_verified")
            previous = journal.read(("stage" if phase == "promote" else "promote") + ".api.started")
            rolling.need(previous is not None, "hotfix_previous_action_missing")
            _readback(plan, boundary, "stage" if phase == "promote" else "promote", previous)
            body = {"name": current["name"], "etag": current["etag"],
                    "traffic": _traffic(plan, phase)}
            mask = "traffic"
        rolling.need(boundary.service(API) == current, "hotfix_native_preimage_changed")
        started = {"phase": phase, "request_sha256": rolling.sha(body), "mask": mask,
                   "traffic": body["traffic"], "expected_generation": int(current["generation"]) + 1,
                   "previous_etag": current["etag"], "replay_authorized": False}
        journal.record(name + ".started", started)
        operation = boundary.patch(API, body, mask)
        op_name = operation.get("name", "")
        rolling.need(rolling.OPERATION.fullmatch(op_name), "hotfix_operation_ack_unmapped")
        ack = {"operation": op_name, "request_sha256": started["request_sha256"]}
        journal.record(name + ".ack", ack)
    if ack is None:
        rolling.need(reconcile, "hotfix_unknown_mutation_reconcile_only")
        _readback(plan, boundary, phase, started)
    else:
        rolling.need(ack.get("request_sha256") == started["request_sha256"],
                     "hotfix_operation_ack_changed")
        operation = boundary.operation(ack["operation"])
        rolling.need(operation.get("name") == ack["operation"] and "error" not in operation,
                     "hotfix_operation_failed_reconcile_only")
        if operation.get("done") is not True:
            return False
        _readback(plan, boundary, phase, started)
    journal.record(name + ".checked", {"phase": phase, "replay_authorized": False,
                                        "operation_ack_retained": ack is not None})
    return True


def stage(plan: HotfixPlan, boundary: NativeHotfixBoundary, journal: rolling.Journal) -> dict[str, Any]:
    plan.validate()
    with journal.locked():
        rolling.need(journal.read("promote.api.started") is None
                     and journal.read("rollback.api.started") is None,
                     "hotfix_stage_already_passed")
        if not _action(plan, boundary, journal, "stage"):
            return {"status": "same_native_operation_pending", "phase": "stage",
                    "replay_authorized": False, "complete": False}
        return {"status": "api_hotfix_staged_zero_traffic", "complete": False,
                "schema": SCHEMA, "worker_mutations": 0, "database_mutations": 0}


def promote(plan: HotfixPlan, boundary: NativeHotfixBoundary, journal: rolling.Journal) -> dict[str, Any]:
    plan.validate()
    with journal.locked():
        rolling.need(journal.read("stage.api.checked") is not None
                     and journal.read("rollback.api.started") is None,
                     "hotfix_stage_required_or_rollback_in_progress")
        _action(plan, boundary, journal, "stage")
        if not _action(plan, boundary, journal, "promote"):
            return {"status": "same_native_operation_pending", "phase": "promote",
                    "replay_authorized": False, "complete": False}
        return {"status": "api_hotfix_promoted_schema21", "complete": True,
                "schema": SCHEMA, "worker_mutations": 0, "database_mutations": 0}


def reconcile(plan: HotfixPlan, boundary: NativeHotfixBoundary, journal: rolling.Journal) -> dict[str, Any]:
    plan.validate()
    with journal.locked():
        boundary.verify_global(plan)
        for phase in ("stage", "promote", "rollback"):
            if journal.read(phase + ".api.started") is not None and not _action(
                plan, boundary, journal, phase, reconcile=True
            ):
                return {"status": "same_native_operation_pending", "phase": phase,
                        "native_mutations": 0, "replay_authorized": False}
        return {"status": "existing_actions_reconciled_only", "native_mutations": 0,
                "replay_authorized": False}


def rollback(plan: HotfixPlan, boundary: NativeHotfixBoundary, journal: rolling.Journal) -> dict[str, Any]:
    plan.validate()
    with journal.locked():
        rolling.need(journal.read("promote.api.checked") is not None,
                     "hotfix_promote_must_be_verified")
        if not _action(plan, boundary, journal, "rollback"):
            return {"status": "same_native_operation_pending", "phase": "rollback",
                    "replay_authorized": False}
        return {"status": "prior_api_traffic_restored_schema21", "worker_mutations": 0,
                "database_mutations": 0, "replay_authorized": False}


def load_plan(raw: bytes) -> HotfixPlan:
    value = rolling.release._json(raw)
    rolling.need(value.get("schema_contract") == CONTRACT and value.get("database_schema") == SCHEMA,
                 "hotfix_distinct_plan_required")
    value["policies"] = rolling.PolicyPins(**value["policies"])
    rolling.need(value.get("candidate_policies") is None, "hotfix_policy_change_refused")
    pins = []
    for item in value["services"]:
        item["secrets"] = tuple(rolling.SecretPin(**secret) for secret in item["secrets"])
        item["revision_keys"], item["traffic"] = tuple(item["revision_keys"]), tuple(item["traffic"])
        pins.append(rolling.ServicePin(**item))
    value["services"] = tuple(pins)
    value["prior_images"] = tuple(value["prior_images"])
    plan = HotfixPlan(**value)
    plan.validate()
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "observe", "stage", "promote", "reconcile", "rollback"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    for name in ("release", "image-digest", "build-id", "nonce", "observer-secret",
                 "retired-migration-secret", "workers-release"):
        parser.add_argument("--" + name)
    parser.add_argument("--ci-run-id", type=int)
    args = parser.parse_args()
    boundary = NativeHotfixBoundary(args.root)
    if args.mode == "capture":
        plan = boundary.capture_hotfix(
            release=args.release, image_digest=args.image_digest,
            build_id=args.build_id, ci_run_id=args.ci_run_id, nonce=args.nonce,
            observer_secret=args.observer_secret,
            retired_migration_secret=args.retired_migration_secret,
            workers_release=args.workers_release,
        )
        rolling.need(
            args.plan.is_absolute() and args.plan.parent.resolve() == args.plan.parent
            and args.plan.parent.stat().st_uid == os.getuid()
            and stat.S_IMODE(args.plan.parent.stat().st_mode) == 0o700,
            "hotfix_private_plan_directory_required",
        )
        raw = rolling.canonical(asdict(plan)) + b"\n"
        fd = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({"status": "api_hotfix_plan_captured", "plan_sha256": hashlib.sha256(raw).hexdigest(),
                          "provider_mutations": 0, "complete": False}))
        return
    rolling.need(args.plan.is_file() and not args.plan.is_symlink()
                 and args.plan.stat().st_size <= 131072
                 and rolling.HASH.fullmatch(args.plan_sha256 or ""),
                 "hotfix_private_exact_plan_required")
    raw = args.plan.read_bytes()
    rolling.need(hashlib.sha256(raw).hexdigest() == args.plan_sha256,
                 "hotfix_plan_hash_changed")
    plan = load_plan(raw)
    if args.mode == "observe":
        boundary.verify_global(plan)
        print(json.dumps({"status": "api_hotfix_readonly_observed", "provider_mutations": 0,
                          "complete": False}))
        return
    rolling.need(args.journal is not None, "hotfix_owned_journal_required")
    journal = rolling.Journal(args.journal, plan)
    result = {"stage": stage, "promote": promote, "reconcile": reconcile, "rollback": rollback}[args.mode](
        plan, boundary, journal
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        # No native response, service environment, SQL, token, or credential leaks.
        print('{"status":"api_hotfix_stopped_reconcile_only","replay_authorized":false}')
        sys.exit(2)
