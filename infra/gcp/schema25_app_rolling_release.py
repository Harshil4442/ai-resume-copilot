"""Closed-AI schema25 application-only rolling release; never migration authority.

Preserve the running reviewed pilot and its resource/IAM/native-document pins.
Only three immutable app images/releases/revisions and their traffic may change.
The initial qualification permits prior8cb and no new-wallet or active AI work.
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[2] / "backend")]
import rolling_native as native  # noqa: E402
import rolling_release as rolling  # noqa: E402
import schema25_coordinator_pause as coordinator  # noqa: E402
import schema25_maintenance as maintenance  # noqa: E402
import unified_credit_schema25_release as initial  # noqa: E402
from browser_schema19_release import (  # noqa: E402
    PriorSource,
    bootstrap_identity,
    verify_permissions,
)

from scripts import schema25_app_release_contract as contract  # noqa: E402

SERVICES = rolling.release.SERVICES
PILOT_FLAGS = frozenset({"RESUME_DIRECT_UPLOAD_ENABLED", "EMPLOYER_DISCOVERY_ENABLED",
    "BROWSER_ASSISTANCE_ENABLED", "BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED",
    "BROWSER_PAIRING_NATIVE_ENABLED", "CANDIDATE_INGRESS_NATIVE_ENABLED",
    "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED"})
CLOSED_FLAGS = tuple(sorted(set(initial.OFF) - PILOT_FLAGS))
PRIOR = PriorSource(contract.PRIOR_RELEASE,
    "sha256:956735cd476c1159397ef21143b497f0b4d40df4d47f6b5d54cd8818a544eafa",
    "45473be6-d177-49e1-a8a3-7970df8271db", 40784200833)
EXTERNAL_SERVICES = ("hirewiz-document-inspector", "hirewiz-native-tex", coordinator.SERVICE)


def need(value, reason):
    rolling.need(value, "schema25_app_" + reason)


@dataclass(frozen=True)
class ResourcePin:
    kind: str
    name: str
    fingerprint: str


@dataclass(frozen=True)
class ReachablePin:
    service: str
    revision: str
    template_sha256: str


@dataclass(frozen=True)
class App25Plan(rolling.RollingPlan):
    schema_contract: Literal["hirewiz_schema25_app_rolling_v1"] = contract.CONTRACT
    database_schema: Literal["20261028_0025"] = contract.schema25.SCHEMA
    prior_source: PriorSource = PRIOR
    compatibility_sha256: str = ""
    resources: tuple[ResourcePin, ...] = ()
    protected_liabilities: tuple[tuple[str, str, int], ...] = ()
    reachable_revisions: tuple[ReachablePin, ...] = ()

    def disabled_flags(self):
        return CLOSED_FLAGS

    def validate(self):
        super().validate()
        need(type(self) is App25Plan and self.schema_contract == contract.CONTRACT
             and self.database_schema == contract.schema25.SCHEMA and self.prior_source == PRIOR
             and self.candidate_policies is None
             and rolling.HASH.fullmatch(self.compatibility_sha256)
             and all(pin.prior_release == PRIOR.release for pin in self.services), "plan_invalid")
        expected = {(kind, name) for kind, names in maintenance.RESOURCE_NAMES.items() for name in names}
        expected |= {("service", name) for name in EXTERNAL_SERVICES}
        need({(row.kind, row.name) for row in self.resources} == expected
             and len(self.resources) == len(expected)
             and all(rolling.HASH.fullmatch(row.fingerprint) for row in self.resources), "resource_pins_invalid")
        need(len(self.protected_liabilities) <= 256
             and len({row[0] for row in self.protected_liabilities}) == len(self.protected_liabilities)
             and all(re.fullmatch(r"[A-Za-z0-9_:-]{1,64}", identity) and rolling.HASH.fullmatch(digest)
                     and type(amount) is int and amount > 0
                     for identity, digest, amount in self.protected_liabilities), "liability_pins_invalid")
        reachable = {(pin.service, row["revision"]) for pin in self.services for row in pin.traffic}
        need(3 <= len(self.reachable_revisions) <= 60
             and {(row.service, row.revision) for row in self.reachable_revisions} == reachable
             and len(self.reachable_revisions) == len(reachable)
             and all(rolling.short_revision(row.revision, row.service) == row.revision
                     and rolling.HASH.fullmatch(row.template_sha256) for row in self.reachable_revisions),
             "reachable_prior_pins_invalid")


def observed_flags(template, service):
    env = rolling.environment(template)
    for flag in initial.OFF:
        expected = "true" if flag in PILOT_FLAGS and (
            service == SERVICES[0] or flag == "EMPLOYER_DISCOVERY_ENABLED") else "false"
        need(env.get(flag, {"value": "false"}).get("value") == expected,
             "pilot_or_closed_flag_changed")


def reachable_pin(service, revision, value, baseline=None):
    """No existing tagged URL can hide an unknown source or paid/AI admission."""
    need(value is not None and value.get("name") == rolling.PARENT + "/services/" + service + "/revisions/" + revision
         and any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
                 for row in value.get("conditions", [])), "reachable_prior_revision_unready")
    env = rolling.environment(value)
    need(value.get("serviceAccount") == rolling.RUNTIME
         and env.get("APP_RELEASE") == {"name": "APP_RELEASE", "value": PRIOR.release}
         and value["containers"][0].get("image") == f"{rolling.release.REGION}-docker.pkg.dev/{rolling.release.PROJECT}/cloud-run-source-deploy/hirewiz@{PRIOR.image_digest}"
         and all(env.get(name, {"value": "false"}).get("value") == "false" for name in CLOSED_FLAGS),
         "reachable_prior_source_or_admission_changed")
    template = {key: item for key, item in value.items() if key not in maintenance.REVISION_READ_OUTPUTS}
    if baseline is not None:
        expected = {key: item for key, item in baseline.items() if key not in maintenance.REVISION_READ_OUTPUTS | {"revision"}}
        compared = copy.deepcopy({key: item for key, item in template.items() if key != "revision"})
        expected_env, compared_env = rolling.environment(expected), rolling.environment(compared)
        for name in PILOT_FLAGS:
            original, current = expected_env.get(name, {"value": "false"}), compared_env.get(name, {"value": "false"})
            need(current.get("value") in {"true", "false"}
                 and not (original.get("value") != "true" and current.get("value") == "true"),
                 "reachable_prior_pilot_expansion")
            if name in compared_env:
                compared_env[name] = copy.deepcopy(original)
        compared["containers"][0]["env"] = list(compared_env.values())
        need(compared == expected, "reachable_prior_configuration_differs")
    return ReachablePin(service, revision, rolling.template_sha(template, service))


def candidate_template(plan, pin, current):
    """Reuse the standard stage body, then prove its *entire* permitted diff."""
    observed_flags(current["template"], pin.service)
    body = rolling.stage_body(plan, pin, current)
    expected = copy.deepcopy(current["template"])
    expected["revision"] = plan.revision(pin.service)
    expected.setdefault("labels", {})["hirewiz-source"] = plan.release
    expected["containers"][0]["image"] = plan.image
    env = expected["containers"][0]["env"]
    for index, row in enumerate(env):
        if row["name"] == "APP_RELEASE":
            need(set(row) == {"name", "value"}, "release_binding_invalid")
            env[index] = {"name": "APP_RELEASE", "value": plan.release}
    need(rolling.template_sha(body["template"], pin.service) == rolling.template_sha(expected, pin.service),
         "unrelated_candidate_configuration_changed")
    observed_flags(body["template"], pin.service)
    return body["template"]


def current_phase(plan, pin, current, target):
    """Only exact original/stage/promote/rollback native resource shapes exist."""
    phase: str | None
    rolling._ready(current, pin)
    traffic = rolling.pinned_traffic(pin.service, current)
    template = rolling.template_sha(current["template"], pin.service)
    generation = int(current["generation"])
    original = template == pin.template_sha256 and generation == pin.generation and traffic == list(pin.traffic)
    if original:
        expected_revision = pin.prior_revision
        phase = "prior"
    else:
        need(template == rolling.template_sha(target, pin.service), "current_phase_template_changed")
        phases = {"stage": (1, rolling._traffic_for(plan, pin, False)),
                  "promote": (2, rolling._traffic_for(plan, pin, True))}
        phase = next((name for name, (offset, expected) in phases.items()
                      if generation == pin.generation + offset and traffic == expected), None)
        if phase is None and generation in {pin.generation + 2, pin.generation + 3} and traffic == rolling._traffic_for(plan, pin, False, rollback=True):
            phase = "rollback"
        need(phase is not None, "unrecognized_current_phase")
        expected_revision = plan.revision(pin.service)
    need(all(rolling.short_revision(current[key], pin.service) == expected_revision
             for key in ("latestReadyRevision", "latestCreatedRevision")), "current_phase_revision_changed")
    return phase


def verify_ci_contract(root):
    import yaml

    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["backend"]["steps"]
    matches = [row for row in steps if row.get("name") == contract.COMPATIBILITY_STEP]
    need(len(matches) == 1 and matches[0] == {
        "name": contract.COMPATIBILITY_STEP, "working-directory": "backend",
        "env": {"HIREWIZ_TEST_POSTGRES_URL": "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55433/hirewiz_admission_test",
                "HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true"},
        "run": contract.COMPATIBILITY_COMMAND}, "compatibility_ci_step_changed")
    checkout = [row for row in steps if row.get("uses", "").startswith("actions/checkout@")]
    need(len(checkout) == 1 and checkout[0].get("with", {}).get("fetch-depth") == 0,
         "prior_git_history_required")


def compatibility_source(root):
    prior = initial.prior_app_files(root, PRIOR.release)
    need(initial.prior_app_digest(prior) == contract.PRIOR_APP_SHA256, "prior_app_bytes_unqualified")
    verify_ci_contract(root)
    return contract.source_fingerprint(root, prior)


class NativeApp25Boundary(native.NativeRollingBoundary):
    """CLI constructs this exact native class; no provider/plugin injection."""
    def __init__(self, root):
        super().__init__(root)
        self.observer = initial.Source25Boundary(self.root)

    def _compatibility(self, plan):
        need(compatibility_source(self.root) == plan.compatibility_sha256, "compatibility_source_changed")
        run = rolling.release._json(self.observer._command("gh", "run", "view", str(plan.ci_run_id),
            "--repo", rolling.release.REPOSITORY, "--json", "jobs"))
        jobs = [row for row in run.get("jobs", []) if row.get("name") == "backend"]
        steps = [step for job in jobs for step in job.get("steps", [])
                 if step.get("name") == contract.COMPATIBILITY_STEP]
        need(len(jobs) == len(steps) == 1 and steps[0].get("status") == "completed"
             and steps[0].get("conclusion") == "success", "compatibility_rehearsal_not_successful")

    def _database(self, runtime_secret, observer_secret, retired_secret, *, expected=None):
        """Normal retained credential manager; native TLS/OID/roles and read-only SQL."""
        module = self.observer._module("backend/scripts/monetary_cutover_preflight.py", "hirewiz_app25_observer")
        neon = module.neon_identity
        retired = self.observer.describe_release_secret(retired_secret)
        name, version = retired_secret.split(":")
        need(retired.get("name") == f"projects/157225724590/secrets/{name}/versions/{version}"
             and retired.get("state") == "DISABLED", "retired_migrator_changed")
        signature, liabilities = None, None
        for alias, reference in (("observer", observer_secret), ("replacement_runtime", runtime_secret)):
            binding, target = neon.collect_schema25_release_target(self.observer, reference, alias=alias,
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=contract.schema25.SCHEMA)
            engine = target.engine()
            try:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                    connection.exec_driver_sql("SET LOCAL statement_timeout='2000ms'")
                    connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
                    connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                    neon.verify_transport(connection, binding)
                    neon.verify_native_target(connection, binding, neon.FRESH_DATABASE_OID)
                    if alias == "observer":
                        sessions, _ = module._native_session_ids(connection, neon.FRESH_DATABASE_OID, neon_binding=binding)
                        rolling.release.verify_fresh_role_phase(connection, neon.FRESH_RELEASE_ACTORS, "serving", sessions)
                        initial.identity_step.verify_financial_catalog(connection, module)
                        verify_permissions(connection, "public", neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0], neon.FRESH_RELEASE_ACTORS["observer"][0])
                        initial.previous.contract.verify_reset_permissions(connection, "public",
                            neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0], neon.FRESH_RELEASE_ACTORS["observer"][0])
                        initial.step.verify_new_permissions(connection, bootstrap_identity())
                        signature = contract.observe(connection)
                        liabilities = contract.liability_pins(connection)
                        if expected is not None:
                            contract.verify_liability_pins(connection, expected)
            finally:
                engine.dispose()
        need(signature is not None and liabilities is not None
             and self.observer.describe_release_secret(retired_secret) == retired, "database_observation_changed")
        return signature, liabilities

    def _resources(self, workers_release):
        # Existing full inspector/TeX/IAM/routing and fresh native acceptance
        # remain mandatory. It checks document sources, not app commit labels.
        self.observer.verify_document_configuration(type("DocumentSource", (), {
            "release": workers_release, "runtime_service_account": rolling.RUNTIME})())
        result = []
        controls = maintenance.NativeControls()
        try:
            for kind, names in maintenance.RESOURCE_NAMES.items():
                for name in names:
                    row = controls.get(kind, name)
                    need(row.get("state") in ({"RUNNING", "PAUSED"} if kind == "queue" else {"ENABLED", "PAUSED"}),
                         "resource_state_invalid")
                    result.append(ResourcePin(kind, name, rolling.sha({"state": row["state"],
                        "configuration": maintenance.configuration(row)})))
        finally:
            controls.session.close()
        for name in EXTERNAL_SERVICES:
            resource = rolling.PARENT + "/services/" + name
            # Keep Source19/25's document read scope unchanged. Coordinator
            # metadata uses its already fixed one-service native reader.
            coordinator_reader = coordinator.NativeCoordinatorRun() if name == coordinator.SERVICE else None
            try:
                row = (coordinator_reader.service(name) if coordinator_reader is not None
                       else self.observer._get("run", resource))
                need(row.get("name") == resource and row.get("reconciling", False) is False
                     and str(row.get("generation")) == str(row.get("observedGeneration"))
                     and row.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED", "external_service_unready")
                traffic = rolling.pinned_traffic(name, row)
                active = next(item["revision"] for item in traffic if item.get("percent") == 100)
                created = rolling.short_revision(row["latestCreatedRevision"], name)
                keys = tuple(sorted(set(row["template"]) - {"revision"}))
                revisions = []
                for revision in sorted({created} | {item["revision"] for item in traffic}):
                    actual = (coordinator_reader.revision(name, revision) if coordinator_reader is not None
                              else self.observer._get("run", resource + "/revisions/" + revision))
                    need(actual.get("name") == resource + "/revisions/" + revision
                         and any(item.get("type") == "Ready" and item.get("state") == "CONDITION_SUCCEEDED"
                                 for item in actual.get("conditions", [])), "external_revision_unready")
                    if revision in {created, active}:
                        need({key: actual.get(key) for key in keys} == {key: row["template"][key] for key in keys},
                             "external_serving_template_changed")
                    revisions.append((revision, rolling.template_sha({key: value for key, value in actual.items()
                        if key not in maintenance.REVISION_READ_OUTPUTS}, name)))
                iam = self.observer._cloud("run", "services", "get-iam-policy", name, "--region", rolling.release.REGION)
                result.append(ResourcePin("service", name, rolling.sha({"uid": row["uid"],
                    "generation": row["generation"], "template": rolling.template_sha(row["template"], name),
                    "static": rolling.static_sha(row), "traffic": traffic, "created": created,
                    "revisions": revisions, "iam": rolling.sha(iam)})))
            finally:
                if coordinator_reader is not None:
                    coordinator_reader.stock.session.close()
        return tuple(sorted(result, key=lambda row: (row.kind, row.name)))

    def _pin(self, name):
        current = self.service(name)
        observed_flags(current["template"], name)
        prior = next(row["revision"] for row in rolling.pinned_traffic(name, current) if row.get("percent") == 100)
        need(rolling.short_revision(current["latestCreatedRevision"], name) == prior
             and rolling.short_revision(current["latestReadyRevision"], name) == prior, "prior_serving_revision_required")
        revision = self.revision(name, prior)
        need(revision is not None, "prior_revision_missing")
        keys = tuple(sorted(set(current["template"]) - {"revision"}))
        need({key: revision.get(key) for key in keys} == {key: current["template"][key] for key in keys},
             "prior_template_changed")
        env = rolling.environment(current["template"])
        secrets = []
        for key, row in env.items():
            if "valueSource" in row:
                ref = row["valueSource"]["secretKeyRef"]
                need(rolling.REF.fullmatch(ref["secret"] + ":" + ref["version"]), "numeric_secret_required")
                need(self._reference(ref["secret"], ref["version"]) == ref["version"], "secret_changed")
                secrets.append(rolling.SecretPin(key, ref["secret"], ref["version"]))
        pin = rolling.ServicePin(name, current["uid"], int(current["generation"]), prior,
            env["APP_RELEASE"]["value"], rolling.template_sha(current["template"], name),
            rolling.static_sha(current), rolling.sha(self.iam(name)),
            rolling.sha({key: revision.get(key) for key in keys}), keys,
            tuple(rolling.pinned_traffic(name, current)), tuple(sorted(secrets, key=lambda row: row.environment_name)))
        rolling._ready(current, pin)
        need(pin.prior_release == PRIOR.release and revision["containers"][0]["image"].endswith("@" + PRIOR.image_digest),
             "actual_prior_source_unqualified")
        return pin

    def _reachable(self, pins):
        return tuple(reachable_pin(pin.service, name, self.revision(pin.service, name),
                                   self.revision(pin.service, pin.prior_revision))
            for pin in pins for name in sorted({row["revision"] for row in pin.traffic}))

    def capture_app(self, *, release, image_digest, build_id, ci_run_id, nonce,
                    observer_secret, retired_migration_secret, workers_release):
        self.observer.verify_source_image(release=release, image_digest=image_digest, build_id=build_id, ci_run_id=ci_run_id)
        pins = tuple(self._pin(name) for name in SERVICES)
        refs = {row.environment_name: row.secret + ":" + row.version for row in pins[0].secrets}
        env = rolling.environment(self.service(SERVICES[0])["template"])
        policies = rolling.PolicyPins(refs["HIREWIZ_EXPENSE_POLICY_JSON"],
            hashlib.sha256(self.observer._secret(refs["HIREWIZ_EXPENSE_POLICY_JSON"]).encode()).hexdigest(),
            refs["LLM_MODEL_COST_POLICY_JSON"], hashlib.sha256(self.observer._secret(refs["LLM_MODEL_COST_POLICY_JSON"]).encode()).hexdigest(),
            refs["LLM_API_KEY"], env["LLM_MODEL"]["value"], env["LLM_API_BASE"]["value"])
        account = self.observer._cloud("iam", "service-accounts", "describe", rolling.RUNTIME)
        signature, liabilities = self._database(refs["DATABASE_URL"], observer_secret, retired_migration_secret)
        plan = App25Plan(release, image_digest, build_id, ci_run_id, nonce, account["uniqueId"],
            refs["DATABASE_URL"], observer_secret, retired_migration_secret, workers_release,
            signature, policies, pins, compatibility_sha256=compatibility_source(self.root),
            resources=self._resources(workers_release), protected_liabilities=liabilities,
            reachable_revisions=self._reachable(pins))
        plan.validate()
        for pin in pins:
            candidate_template(plan, pin, self.service(pin.service))
            need(self.revision(pin.service, plan.revision(pin.service)) is None, "candidate_revision_exists")
        self.verify_global(plan)
        return plan

    def verify_global(self, plan):
        plan.validate()
        need(type(plan) is App25Plan, "distinct_app_plan_required")
        self.observer.verify_source_image(release=plan.release, image_digest=plan.image_digest,
            build_id=plan.build_id, ci_run_id=plan.ci_run_id)
        self.observer._verify_source_image_provenance(release=PRIOR.release, image_digest=PRIOR.image_digest,
            build_id=PRIOR.build_id, ci_run_id=PRIOR.ci_run_id)
        self._compatibility(plan)
        self.observer.verify_policy_configuration(plan.policies)
        self._identity(plan.runtime_uid)
        need(self._reachable(plan.services) == plan.reachable_revisions, "reachable_prior_revision_changed")
        need(self._resources(plan.workers_release) == plan.resources, "resource_or_document_state_changed")
        signature, _ = self._database(plan.runtime_secret, plan.observer_secret,
            plan.retired_migration_secret, expected=plan.protected_liabilities)
        need(signature == plan.financial_sha256, "database_contract_changed")
        for pin in plan.services:
            current = self.service(pin.service)
            rolling.worker_iam(pin.service, self.iam(pin.service))
            need(rolling.sha(self.iam(pin.service)) == pin.iam_sha256
                 and rolling.static_sha(current) == pin.static_sha256, "identity_or_static_configuration_changed")
            rolling._old_revision(self, pin)
            prior = self.revision(pin.service, pin.prior_revision)
            need(prior["containers"][0]["image"] == plan.image.split("@")[0] + "@" + PRIOR.image_digest,
                 "prior_image_changed")
            original = {key: prior.get(key) for key in pin.revision_keys}
            if rolling.template_sha(original, pin.service) != pin.template_sha256:
                original["revision"] = pin.prior_revision
            need(rolling.template_sha(original, pin.service) == pin.template_sha256, "prior_service_template_changed")
            target = candidate_template(plan, pin, {**current, "template": original,
                "generation": pin.generation, "observedGeneration": pin.generation,
                "trafficStatuses": list(pin.traffic)})
            current_phase(plan, pin, current, target)
            for template in (current["template"], prior):
                observed_flags(template, pin.service)
                rolling.verify_policy_bindings(plan.policies, pin, template)
                rows = rolling.environment(template)
                for secret in pin.secrets:
                    need(rows.get(secret.environment_name) == {"name": secret.environment_name, "valueSource": {
                        "secretKeyRef": {"secret": secret.secret, "version": secret.version}}}
                        and self._reference(secret.secret, secret.version) == secret.version,
                        "secret_reference_changed")


def load(raw):
    value = rolling.release._json(raw)
    need(value.get("schema_contract") == contract.CONTRACT, "distinct_app_plan_required")
    value["policies"] = rolling.PolicyPins(**value["policies"])
    value["prior_source"] = PriorSource(**value["prior_source"])
    value["resources"] = tuple(ResourcePin(**row) for row in value["resources"])
    value["reachable_revisions"] = tuple(ReachablePin(**row) for row in value["reachable_revisions"])
    value["protected_liabilities"] = tuple(tuple(row) for row in value["protected_liabilities"])
    pins = []
    for row in value["services"]:
        row["secrets"] = tuple(rolling.SecretPin(**secret) for secret in row["secrets"])
        row["revision_keys"], row["traffic"] = tuple(row["revision_keys"]), tuple(row["traffic"])
        pins.append(rolling.ServicePin(**row))
    value["services"] = tuple(pins)
    plan = App25Plan(**value)
    plan.validate()
    return plan


ROLLBACK_INTENT = "app25.rollback.intent"


def join_metadata(plan, boundary, pin, started, ack):
    """Join this exact existing mutation without re-sending or candidate health."""
    if ack is not None:
        need(ack.get("request_sha256") == started["request_sha256"]
             and rolling.OPERATION.fullmatch(ack.get("operation", "")), "rollback_predecessor_ack_invalid")
        operation = boundary.operation(ack["operation"])
        need(operation.get("name") == ack["operation"] and "error" not in operation,
             "rollback_operation_failed_reconcile_only")
        if operation.get("done", False) is not True:
            return None
    return rolling._readback_metadata(plan, boundary, pin, started)


def rollback_readback(plan, boundary, pin, started, ack):
    result = join_metadata(plan, boundary, pin, started, ack)
    if result is not None:
        # The broken candidate endpoint is not a prerequisite for recovery.
        # Exact native metadata, prior source/funding/catalog and prior tagged
        # HTTP health remain mandatory. Do not mark candidate health checked.
        boundary.health(plan, pin.service, pin.prior_revision, pin.prior_release, plan.rollback_tag)
    return result


def rollback_app(plan, boundary, journal, *, reconcile=False):
    """App25-only traffic recovery; never weakens legacy action/default health."""
    mutations = 0
    with journal.locked():
        if journal.read(ROLLBACK_INTENT) is None:
            need(not reconcile, "rollback_intent_required")
            journal.record(ROLLBACK_INTENT, {"prior_release": PRIOR.release,
                "candidate_health_required": False, "prior_health_required": True,
                "forward_continuation_authorized": False, "replay_authorized": False})
        for name in (SERVICES[0], *reversed(SERVICES[1:])):
            pin = plan.pin(name)
            key = "rollback." + name
            started, ack = journal.read(key + ".started"), journal.read(key + ".ack")
            if started is not None:
                need(ack is not None or reconcile or journal.read(key + ".checked") is not None,
                     "unknown_rollback_mutation_reconcile_only")
                # A pending same LRO can be observed without requiring a settled
                # Service yet. No additional write is authorized by this read.
                result = rollback_readback(plan, boundary, pin, started, ack)
                if result is None:
                    return {"status": "same_native_operation_pending", "phase": "rollback",
                            "rolling_release_complete": False, "replay_authorized": False}
                boundary.verify_global(plan)
                if journal.read(key + ".checked") is None:
                    journal.record(key + ".checked", {**result, "prior_http_health_observed": True,
                        "candidate_http_health_required": False, "operation_ack_retained": ack is not None,
                        "replay_authorized": False})
                continue
            previous = "promote" if journal.read("promote." + name + ".started") else "stage"
            predecessor = journal.read(previous + "." + name + ".started")
            if predecessor is None:
                # Partial staging can fail before another service is changed.
                # That service must still be its exact original native shape;
                # it needs no traffic mutation or invented candidate receipt.
                boundary.verify_global(plan)
                need(rolling.template_sha(boundary.service(name)["template"], name) == pin.template_sha256,
                     "unstaged_service_changed")
                continue
            previous_ack = journal.read(previous + "." + name + ".ack")
            previous_observed = join_metadata(plan, boundary, pin, predecessor, previous_ack)
            if previous_observed is None:
                return {"status": "same_native_operation_pending", "phase": "rollback_predecessor",
                        "rolling_release_complete": False, "replay_authorized": False}
            boundary.verify_global(plan)
            if reconcile:
                continue  # Observe only; never start the next rollback PATCH.
            evidence_key = "app25.rollback.predecessor." + name
            evidence = {**previous_observed, "phase": previous,
                "request_sha256": predecessor["request_sha256"],
                "operation_ack_retained": previous_ack is not None,
                "candidate_http_health_required": False, "replay_authorized": False}
            retained = journal.read(evidence_key)
            if retained is None:
                journal.record(evidence_key, evidence)
            else:
                need(retained == evidence | {"plan_sha256": journal.plan_hash}, "rollback_predecessor_changed")
            current = boundary.service(name)
            body = {"name": current["name"], "etag": current["etag"],
                    "traffic": rolling._traffic_for(plan, pin, False, rollback=True)}
            started = {"phase": "rollback", "request_sha256": rolling.sha(body), "mask": "traffic",
                "template_sha256": rolling.template_sha(current["template"], name), "traffic": body["traffic"],
                "expected_generation": int(current["generation"]) + 1, "service_uid": pin.uid,
                "previous_etag": current["etag"], "replay_authorized": False}
            need(boundary.service(name) == current, "rollback_native_preimage_changed")
            journal.record(key + ".started", started)
            mutations += 1
            operation = boundary.patch(name, body, "traffic")
            need(type(operation.get("name")) is str and rolling.OPERATION.fullmatch(operation["name"]),
                 "rollback_operation_ack_unmapped")
            ack = {"operation": operation["name"], "request_sha256": started["request_sha256"]}
            journal.record(key + ".ack", ack)
            result = rollback_readback(plan, boundary, pin, started, ack)
            if result is None:
                return {"status": "same_native_operation_pending", "phase": "rollback",
                        "rolling_release_complete": False, "replay_authorized": False}
            boundary.verify_global(plan)
            journal.record(key + ".checked", {**result, "prior_http_health_observed": True,
                "candidate_http_health_required": False, "operation_ack_retained": True, "replay_authorized": False})
        boundary.verify_global(plan)
        return {"status": "existing_actions_reconciled_only" if reconcile else "prior_traffic_restored_schema18",
                "native_mutations": mutations, "native_mutation_scope": "traffic_only",
                "rolling_release_complete": False, "replay_authorized": False, "goal_complete": False}


def execute(phase, plan, boundary, journal):
    need(type(plan) is App25Plan and type(journal) is rolling.Journal
         and journal.plan_hash == rolling.sha(asdict(plan)), "original_app_plan_journal_required")
    plan.validate()
    if phase in {"stage", "advance"}:
        need(journal.read(ROLLBACK_INTENT) is None, "explicit_rollback_in_progress")
    if phase == "stage":
        with journal.locked():
            need(journal.read(ROLLBACK_INTENT) is None, "explicit_rollback_in_progress")
            need(not any(journal.read(later + "." + name + ".started")
                         for later in ("promote", "rollback") for name in SERVICES), "stage_already_passed")
            for pin in plan.services:
                if not rolling.action(plan, boundary, journal, pin, "stage"):
                    return {"status": "schema25_app_same_operation_pending", "phase": "stage",
                            "rolling_release_complete": False, "replay_authorized": False}
            boundary.verify_global(plan)
        return {"status": "schema25_app_staged_zero_traffic", "schema": contract.schema25.SCHEMA,
                "rolling_release_complete": False, "migrations": 0, "resource_access_changes": 0,
                "pilot_admission": "preserved", "replay_authorized": False}
    if phase == "rollback" or phase == "reconcile" and journal.read(ROLLBACK_INTENT) is not None:
        result = rollback_app(plan, boundary, journal, reconcile=phase == "reconcile")
    else:
        result = {"advance": rolling.advance, "reconcile": rolling.reconcile}[phase](plan, boundary, journal)
    status = {"joined_schema18_release_observed": "schema25_app_release_observed",
        "existing_actions_reconciled_only": "schema25_app_existing_actions_reconciled",
        "prior_traffic_restored_schema18": "schema25_app_prior_traffic_restored",
        "same_native_operation_pending": "schema25_app_same_operation_pending"}.get(result.get("status"))
    need(status is not None, "shared_result_unrecognized")
    # Shared engine's legacy status strings never become false schema18/closed
    # queue or broad recovery claims for this distinct application contract.
    result = {key: value for key, value in result.items() if key not in {"status", "schema", "queue_admission"}}
    return {**result, "status": status, "schema": contract.schema25.SCHEMA,
        "pilot_admission": "preserved", "migrations": 0, "resource_access_changes": 0,
        "disaster_recovery_rto_qualified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "observe", "stage", "advance", "reconcile", "rollback"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    for name in ("release", "image-digest", "build-id", "nonce", "observer-secret", "retired-migration-secret", "workers-release"):
        parser.add_argument("--" + name)
    parser.add_argument("--ci-run-id", type=int)
    args = parser.parse_args()
    boundary = NativeApp25Boundary(args.root)
    if args.phase == "capture":
        need(all(getattr(args, name) is not None for name in ("release", "image_digest", "build_id", "ci_run_id",
            "nonce", "observer_secret", "retired_migration_secret", "workers_release")), "capture_arguments_required")
        plan = boundary.capture_app(**{name: getattr(args, name) for name in ("release", "image_digest", "build_id", "ci_run_id",
            "nonce", "observer_secret", "retired_migration_secret", "workers_release")})
        need(args.plan.is_absolute() and args.plan.parent.resolve() == args.plan.parent
             and args.plan.parent.stat().st_uid == os.getuid()
             and stat.S_IMODE(args.plan.parent.stat().st_mode) == 0o700, "private_plan_required")
        raw = rolling.canonical(asdict(plan)) + b"\n"
        fd = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({"status": "schema25_app_plan_captured", "plan_sha256": hashlib.sha256(raw).hexdigest(), "native_mutations": 0}))
        return
    plan = load(initial._read_pinned(args.plan, args.plan_sha256))
    if args.phase == "observe":
        boundary.verify_global(plan)
        print(json.dumps({"status": "schema25_app_readonly_observed", "native_mutations": 0}))
        return
    need(args.journal is not None, "owned_journal_required")
    print(json.dumps(execute(args.phase, plan, boundary, rolling.Journal(args.journal, plan))))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        code = str(error) if type(error) is rolling.RollingDenied and re.fullmatch(r"schema25_app_[a-z_]{1,100}", str(error)) else "schema25_app_observation_unavailable"
        print(json.dumps({"status": "schema25_app_stopped_reconcile_only", "code": code, "replay_authorized": False}))
        raise SystemExit(2) from None
