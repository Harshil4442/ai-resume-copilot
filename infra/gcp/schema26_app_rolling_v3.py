"""Finite prospective e23→schema26 application release; no migration authority.

The prior pins are EXPECTED, never a statement of current serving traffic. Capture
requires genuine e23 serving/readback and the independently applied schema26
fence. Historical25 operators and receipts remain disjoint. No phase removes the
fence or authorizes old evidence writers, including ordinary app rollback.
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

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path(__file__).resolve().parents[2] / "backend"),
    str(Path(__file__).resolve().parents[2]),
]
import rolling_release as rolling  # noqa: E402
import schema25_app_rolling_release as historical  # noqa: E402
import unified_credit_schema25_release as initial  # noqa: E402
from browser_schema19_release import (  # noqa: E402
    PriorSource,
    bootstrap_identity,
    verify_permissions,
)

from scripts import evidence_writer_schema26_contract as schema26  # noqa: E402
from scripts import schema25_app_release_contract_v3 as contract  # noqa: E402

SERVICES = historical.SERVICES
ResourcePin, ReachablePin = historical.ResourcePin, historical.ReachablePin
PRIOR = PriorSource(
    contract.PRIOR_RELEASE,
    "sha256:df4618fa19a05645384a4e0aba92ad691e5f40c55344b34da55c95fc1b44c77f",
    "3a251d77-44e7-4b7a-9987-2c991c8e6d89",
    41095660943,
)
CLOSED_FLAGS = historical.CLOSED_FLAGS
maintenance = historical.maintenance


def need(value, reason):
    rolling.need(value, "schema26_app_" + reason)


@dataclass(frozen=True)
class ClosedApiBaseline:
    """Metadata from the original stock close, never an authority by itself."""

    maintenance_plan_sha256: str
    close_started_sha256: str
    close_acknowledged_sha256: str
    close_checked_sha256: str
    api_service_uid: str
    api_generation: int
    api_etag: str
    closed_revision: str
    closed_revision_uid: str
    closed_template_sha256: str
    original_pilot_flags: tuple[tuple[str, str], ...]

    def validate(self):
        need(all(type(value) is str and rolling.HASH.fullmatch(value) for value in (
            self.maintenance_plan_sha256, self.close_started_sha256,
            self.close_acknowledged_sha256, self.close_checked_sha256,
            self.closed_template_sha256)), "maintenance_lineage_invalid")
        need(all(type(value) is str and re.fullmatch(r"[a-f0-9-]{36}", value)
                 for value in (self.api_service_uid, self.closed_revision_uid))
             and type(self.api_generation) is int and self.api_generation > 1
             and type(self.api_etag) is str and 1 <= len(self.api_etag) <= 256
             and rolling.short_revision(self.closed_revision, SERVICES[0]) == self.closed_revision,
             "maintenance_native_identity_invalid")
        need(self.original_pilot_flags == tuple((flag, "true") for flag in sorted(historical.PILOT_FLAGS)),
             "maintenance_original_pilot_invalid")


def _maintenance_lineage(plan, journal, run):
    need(type(plan) is maintenance.MaintenancePlan and type(journal) is rolling.Journal
         and journal.plan_hash == rolling.sha(asdict(plan)), "original_maintenance_journal_required")
    plan.validate()
    pin = plan.services[0]
    need(pin.name == SERVICES[0] and pin.source == PRIOR.release
         and pin.image == f"{rolling.release.REGION}-docker.pkg.dev/{rolling.release.PROJECT}"
                          f"/cloud-run-source-deploy/hirewiz@{PRIOR.image_digest}",
         "maintenance_prior_source_changed")
    marker = "service." + pin.name + ".close"
    started, ack, checked = (journal.read(marker + suffix)
        for suffix in (".started", ".acknowledged", ".checked"))
    need(started is not None and ack is not None and checked is not None,
         "maintenance_close_not_checked")
    need(set(started) == {"prior_generation", "request_sha256", "replay_authorized", "plan_sha256"}
         and type(started["prior_generation"]) is int and started["prior_generation"] == pin.generation
         and started["replay_authorized"] is False
         and type(started["request_sha256"]) is str and rolling.HASH.fullmatch(started["request_sha256"])
         and set(ack) == {"operation", "plan_sha256"}
         and type(ack["operation"]) is str and rolling.OPERATION.fullmatch(ack["operation"])
         and set(checked) == {"generation", "revision", "plan_sha256"}
         and type(checked["generation"]) is int and checked["generation"] == pin.generation + 1
         and rolling.short_revision(checked["revision"], pin.name) == plan.revision(pin.name, "close"),
         "maintenance_close_lineage_changed")
    need(not any(journal.read("service." + service + ".restore.started") is not None
                 for service in SERVICES)
         and not any(journal.read("service." + service + ".close.started") is not None
                     for service in SERVICES[1:]), "maintenance_scope_changed")
    operation = run.operation(ack["operation"])
    need(operation.get("name") == ack["operation"] and operation.get("done") is True
         and "error" not in operation, "maintenance_close_operation_unresolved")
    template, _ = maintenance.desired(plan, pin, run, "close")
    revision = run.revision(pin.name, plan.revision(pin.name, "close"))
    maintenance.revision_ready(revision, pin.name, plan.revision(pin.name, "close"))
    need(type(revision.get("uid")) is str and re.fullmatch(r"[a-f0-9-]{36}", revision["uid"])
         and {key: revision.get(key) for key in pin.template_keys}
             == {key: template.get(key) for key in pin.template_keys},
         "maintenance_closed_revision_changed")
    original = maintenance.pinned_revision(pin, run, pin.prior_revision, dict(pin.revisions)[pin.prior_revision])
    historical.observed_flags(original, pin.name)
    v3_flags(original)
    flags = tuple((flag, rolling.environment(original)[flag]["value"])
                  for flag in sorted(historical.PILOT_FLAGS))
    return pin, started, ack, checked, revision, template, flags


def capture_closed_api_baseline(plan, journal, run):
    pin, started, ack, checked, revision, template, flags = _maintenance_lineage(plan, journal, run)
    row = maintenance.service_readback(plan, pin, run, "close", started)
    for worker in plan.services[1:]:
        current = run.service(worker.name)
        maintenance.ready(current, worker.name)
        need(current["uid"] == worker.uid and int(current["generation"]) == worker.generation
             and rolling.static_sha(current) == worker.static_sha256
             and maintenance.coordinator.template_hash(current["template"]) == worker.template_sha256
             and rolling.pinned_traffic(worker.name, current) == list(worker.traffic)
             and rolling.short_revision(current["latestCreatedRevision"], worker.name) == worker.prior_revision,
             "maintenance_worker_changed")
    baseline = ClosedApiBaseline(rolling.sha(asdict(plan)), rolling.sha(started), rolling.sha(ack),
        rolling.sha(checked), row["uid"], int(row["generation"]), row["etag"],
        plan.revision(pin.name, "close"), revision["uid"], rolling.template_sha(row["template"], pin.name), flags)
    baseline.validate()
    return baseline


def verify_closed_api_baseline(baseline, plan, journal, run):
    """Reobserve immutable lineage after staging; never require the old Service template."""
    need(type(baseline) is ClosedApiBaseline, "maintenance_baseline_required")
    baseline.validate()
    pin, started, ack, checked, revision, template, flags = _maintenance_lineage(plan, journal, run)
    immutable = {key: revision.get(key) for key in pin.template_keys}
    if rolling.template_sha(immutable, pin.name) != baseline.closed_template_sha256:
        immutable["revision"] = baseline.closed_revision
    need((baseline.maintenance_plan_sha256, baseline.close_started_sha256,
          baseline.close_acknowledged_sha256, baseline.close_checked_sha256,
          baseline.api_service_uid, baseline.api_generation, baseline.closed_revision,
          baseline.closed_revision_uid, baseline.closed_template_sha256, baseline.original_pilot_flags)
         == (rolling.sha(asdict(plan)), rolling.sha(started), rolling.sha(ack), rolling.sha(checked),
             pin.uid, checked["generation"], plan.revision(pin.name, "close"), revision["uid"],
             rolling.template_sha(immutable, pin.name), flags), "maintenance_baseline_changed")


@dataclass(frozen=True)
class App26V3Plan(rolling.RollingPlan):
    schema_contract: Literal["hirewiz_schema26_app_rolling_v3"] = "hirewiz_schema26_app_rolling_v3"
    database_schema: Literal["20261031_0026"] = "20261031_0026"
    prior_source: PriorSource = PRIOR
    compatibility_sha256: str = ""
    resources: tuple[historical.ResourcePin, ...] = ()
    protected_liabilities: tuple[tuple[str, str, int], ...] = ()
    reachable_revisions: tuple[historical.ReachablePin, ...] = ()
    maintenance_baseline: ClosedApiBaseline | None = None

    def disabled_flags(self):
        return CLOSED_FLAGS

    def promoted_aliases(self, pin, rows):
        need(pin == self.pin(pin.service), "alias_service_pin_changed")
        expected = [
            {key: value for key, value in row.items() if key != "percent"}
            for row in pin.traffic
            if row.get("tag")
        ]
        need(rows == expected, "alias_intent_changed")
        return [{**row, "revision": self.revision(pin.service)} for row in rows]

    def validate(self):
        super().validate()
        need(
            type(self) is App26V3Plan
            and self.schema_contract == contract.CONTRACT
            and self.database_schema == schema26.SCHEMA
            and self.prior_source == PRIOR
            and self.candidate_policies is None
            and rolling.HASH.fullmatch(self.compatibility_sha256)
            and all(pin.prior_release == PRIOR.release for pin in self.services),
            "distinct_plan_required",
        )
        historical.validate_extensions(self)
        if self.maintenance_baseline is not None:
            need(type(self.maintenance_baseline) is ClosedApiBaseline, "maintenance_baseline_required")
            self.maintenance_baseline.validate()
            pin = self.services[0]
            need((pin.uid, pin.generation, pin.prior_revision, pin.template_sha256)
                 == (self.maintenance_baseline.api_service_uid, self.maintenance_baseline.api_generation,
                     self.maintenance_baseline.closed_revision, self.maintenance_baseline.closed_template_sha256)
                 and self.release != PRIOR.release, "maintenance_plan_pin_changed")


def v3_flags(template):
    historical.v2_flags(template)
    env = rolling.environment(template)
    need(
        contract.PRIVACY_FLAG not in env
        or env[contract.PRIVACY_FLAG] == {"name": contract.PRIVACY_FLAG, "value": "false"},
        "privacy_mail_activation_outside_release",
    )


def observed_flags(template, service, baseline=None):
    if baseline is None or service != SERVICES[0]:
        historical.observed_flags(template, service)
        return
    need(type(baseline) is ClosedApiBaseline, "maintenance_baseline_required")
    baseline.validate()
    env = rolling.environment(template)
    need(env.get("APP_RELEASE") == {"name": "APP_RELEASE", "value": PRIOR.release}
         and template["containers"][0]["image"].endswith("@" + PRIOR.image_digest)
         and all(env.get(flag) == {"name": flag, "value": "false"} for flag in initial.OFF),
         "maintenance_closed_admission_changed")


def candidate_template(plan, pin, current):
    v3_flags(current["template"])
    baseline = plan.maintenance_baseline if pin.service == SERVICES[0] else None
    if baseline is None:
        result = historical.candidate_template(plan, pin, current)
    else:
        plan.validate()
        observed_flags(current["template"], pin.service, baseline)
        need(rolling.template_sha(current["template"], pin.service) == baseline.closed_template_sha256,
             "maintenance_closed_template_changed")
        result = rolling.stage_body(plan, pin, current)["template"]
        env = rolling.environment(result)
        for flag, value in baseline.original_pilot_flags:
            env[flag] = {"name": flag, "value": value}
        result["containers"][0]["env"] = list(env.values())
        expected = copy.deepcopy(current["template"])
        expected["revision"] = plan.revision(pin.service)
        expected.setdefault("labels", {})["hirewiz-source"] = plan.release
        expected["containers"][0]["image"] = plan.image
        expected_env = rolling.environment(expected)
        expected_env["APP_RELEASE"] = {"name": "APP_RELEASE", "value": plan.release}
        for flag, value in baseline.original_pilot_flags:
            expected_env[flag] = {"name": flag, "value": value}
        expected["containers"][0]["env"] = list(expected_env.values())
        need(rolling.template_sha(result, pin.service) == rolling.template_sha(expected, pin.service),
             "unrelated_candidate_configuration_changed")
        historical.observed_flags(result, pin.service)
    v3_flags(result)
    return result


class Source26Boundary(historical.App25V2Source):
    def verify_source_image(self, *, release, image_digest, build_id, ci_run_id):
        tracked = rolling.release.source_guard.checked_files(self.root, release)
        need(
            rolling.release.source_guard.migration_chain(self.root, tracked, allow_literal_sql=True)
            == (*initial.CHAIN, schema26.SCHEMA),
            "exact_source_chain_required",
        )
        schema26.verify_source(self.root)
        self._verify_source_image_provenance(
            release=release, image_digest=image_digest, build_id=build_id, ci_run_id=ci_run_id
        )


class NativeApp26Boundary(historical.NativeApp25Boundary):
    plan_type, prior_source, source_boundary = App26V3Plan, PRIOR, Source26Boundary

    def __init__(self, root, *, maintenance_plan_path=None, maintenance_plan_sha256=None,
                 maintenance_journal=None):
        need(isinstance(maintenance_plan_path, Path) and isinstance(maintenance_journal, Path)
             and type(maintenance_plan_sha256) is str
             and rolling.HASH.fullmatch(maintenance_plan_sha256), "maintenance_custody_required")
        self.maintenance_plan_path = maintenance_plan_path
        self.maintenance_plan_sha256 = maintenance_plan_sha256
        self.maintenance_journal = maintenance_journal
        super().__init__(root)

    def _maintenance_custody(self):
        raw = initial._read_pinned(self.maintenance_plan_path, self.maintenance_plan_sha256)
        plan = maintenance.load(raw)
        return plan, rolling.Journal(self.maintenance_journal, plan)

    def _capture_maintenance_baseline(self):
        plan, journal = self._maintenance_custody()
        return capture_closed_api_baseline(plan, journal, self.run)

    def _verify_maintenance_baseline(self, baseline):
        plan, journal = self._maintenance_custody()
        verify_closed_api_baseline(baseline, plan, journal, self.run)

    def _pin(self, name, baseline=None):
        if name != SERVICES[0] or baseline is None:
            return super()._pin(name)
        need(type(baseline) is ClosedApiBaseline, "maintenance_baseline_required")
        baseline.validate()
        current = self.service(name)
        observed_flags(current["template"], name, baseline)
        need((current["uid"], int(current["generation"]), current["etag"])
             == (baseline.api_service_uid, baseline.api_generation, baseline.api_etag)
             and rolling.template_sha(current["template"], name) == baseline.closed_template_sha256
             and all(rolling.short_revision(current[key], name) == baseline.closed_revision
                     for key in ("latestCreatedRevision", "latestReadyRevision")),
             "maintenance_closed_preimage_changed")
        revision = self.revision(name, baseline.closed_revision)
        need(revision is not None and revision.get("uid") == baseline.closed_revision_uid,
             "maintenance_closed_revision_changed")
        keys = tuple(sorted(set(current["template"]) - {"revision"}))
        need({key: revision.get(key) for key in keys} == {key: current["template"][key] for key in keys},
             "maintenance_closed_revision_changed")
        traffic = rolling.pinned_traffic(name, current)
        need(all(row["revision"] == baseline.closed_revision for row in traffic),
             "maintenance_open_alias_changed")
        secrets = []
        for key, row in rolling.environment(current["template"]).items():
            if "valueSource" in row:
                ref = row["valueSource"]["secretKeyRef"]
                need(rolling.REF.fullmatch(ref["secret"] + ":" + ref["version"])
                     and self._reference(ref["secret"], ref["version"]) == ref["version"],
                     "numeric_secret_required")
                secrets.append(rolling.SecretPin(key, ref["secret"], ref["version"]))
        pin = rolling.ServicePin(name, current["uid"], int(current["generation"]), baseline.closed_revision,
            PRIOR.release, baseline.closed_template_sha256, rolling.static_sha(current),
            rolling.sha(self.iam(name)), rolling.sha({key: revision.get(key) for key in keys}), keys,
            tuple(traffic), tuple(sorted(secrets, key=lambda row: row.environment_name)))
        rolling._ready(current, pin)
        return pin

    def _compatibility(self, plan):
        fingerprint = contract.source_fingerprint(
            self.root, initial.prior_app_files(self.root, PRIOR.release)
        )
        need(fingerprint == plan.compatibility_sha256, "compatibility_source_changed")
        run = rolling.release._json(
            self.observer._command(
                "gh",
                "run",
                "view",
                str(plan.ci_run_id),
                "--repo",
                rolling.release.REPOSITORY,
                "--json",
                "jobs",
            )
        )
        jobs = [row for row in run.get("jobs", []) if row.get("name") == "backend"]
        names = (contract.COMPATIBILITY_STEP, contract.PROTOCOL_STEP, contract.RETAINED_STEP)
        steps = [step for job in jobs for step in job.get("steps", []) if step.get("name") in names]
        need(
            len(jobs) == 1
            and len(steps) == len(names)
            and {step.get("name") for step in steps} == set(names)
            and all(
                step.get("status") == "completed" and step.get("conclusion") == "success"
                for step in steps
            ),
            "compatibility_rehearsal_not_successful",
        )

    def _database(self, runtime_secret, observer_secret, retired_secret, *, expected=None):
        """Normal retained credential manager; native TLS/OID/roles and read-only SQL."""
        module = self.observer._module(
            "backend/scripts/monetary_cutover_preflight.py", "hirewiz_app26_observer"
        )
        neon = module.neon_identity
        retired = self.observer.describe_release_secret(retired_secret)
        name, version = retired_secret.split(":")
        need(
            retired.get("name") == f"projects/157225724590/secrets/{name}/versions/{version}"
            and retired.get("state") == "DISABLED",
            "retired_migrator_changed",
        )
        signature, liabilities = None, None
        for alias, reference in (
            ("observer", observer_secret),
            ("replacement_runtime", runtime_secret),
        ):
            binding, target = neon.collect_schema26_release_target(
                self.observer,
                reference,
                alias=alias,
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
                expected_schema=schema26.SCHEMA,
            )
            engine = target.engine()
            try:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                    connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                    connection.exec_driver_sql("SET LOCAL statement_timeout='2000ms'")
                    connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
                    connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                    neon.verify_transport(connection, binding)
                    neon.verify_native_target(connection, binding, neon.FRESH_DATABASE_OID)
                    if alias == "observer":
                        sessions, _ = module._native_session_ids(
                            connection, neon.FRESH_DATABASE_OID, neon_binding=binding
                        )
                        rolling.release.verify_fresh_role_phase(
                            connection, neon.FRESH_RELEASE_ACTORS, "serving", sessions
                        )
                        initial.identity_step.verify_financial_catalog(connection, module)
                        verify_permissions(
                            connection,
                            "public",
                            neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                            neon.FRESH_RELEASE_ACTORS["observer"][0],
                        )
                        initial.previous.contract.verify_reset_permissions(
                            connection,
                            "public",
                            neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                            neon.FRESH_RELEASE_ACTORS["observer"][0],
                        )
                        initial.step.verify_new_permissions(connection, bootstrap_identity())
                        signature = schema26.observe(connection)
                        schema26.verify_writer_permissions(
                            connection,
                            runtime=neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            migrator=neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                        )
                        liabilities = contract.v1.liability_pins(connection)
                        if expected is not None:
                            contract.v1.verify_liability_pins(connection, expected)
            finally:
                engine.dispose()
        need(
            signature is not None
            and liabilities is not None
            and self.observer.describe_release_secret(retired_secret) == retired,
            "database_observation_changed",
        )
        return signature, liabilities

    def _reachable(self, pins):
        result = []
        for pin in pins:
            for name in sorted({row["revision"] for row in pin.traffic}):
                value = self.revision(pin.service, name)
                if self.plan_type is App26V3Plan:
                    need(value is not None, "reachable_prior_revision_unready")
                    v3_flags(value)
                result.append(
                    historical.reachable_pin(
                        pin.service,
                        name,
                        value,
                        self.revision(pin.service, pin.prior_revision),
                        prior_source=self.prior_source,
                    )
                )
        return tuple(result)

    def capture_app(
        self,
        *,
        release,
        image_digest,
        build_id,
        ci_run_id,
        nonce,
        observer_secret,
        retired_migration_secret,
        workers_release,
    ):
        self.observer.verify_source_image(
            release=release, image_digest=image_digest, build_id=build_id, ci_run_id=ci_run_id
        )
        baseline = self._capture_maintenance_baseline()
        pins = tuple(self._pin(name, baseline) for name in SERVICES)
        refs = {row.environment_name: row.secret + ":" + row.version for row in pins[0].secrets}
        env = rolling.environment(self.service(SERVICES[0])["template"])
        policies = rolling.PolicyPins(
            refs["HIREWIZ_EXPENSE_POLICY_JSON"],
            hashlib.sha256(
                self.observer._secret(refs["HIREWIZ_EXPENSE_POLICY_JSON"]).encode()
            ).hexdigest(),
            refs["LLM_MODEL_COST_POLICY_JSON"],
            hashlib.sha256(
                self.observer._secret(refs["LLM_MODEL_COST_POLICY_JSON"]).encode()
            ).hexdigest(),
            refs["LLM_API_KEY"],
            env["LLM_MODEL"]["value"],
            env["LLM_API_BASE"]["value"],
        )
        account = self.observer._cloud("iam", "service-accounts", "describe", rolling.RUNTIME)
        signature, liabilities = self._database(
            refs["DATABASE_URL"], observer_secret, retired_migration_secret
        )
        plan = self.plan_type(
            release,
            image_digest,
            build_id,
            ci_run_id,
            nonce,
            account["uniqueId"],
            refs["DATABASE_URL"],
            observer_secret,
            retired_migration_secret,
            workers_release,
            signature,
            policies,
            pins,
            compatibility_sha256=contract.source_fingerprint(
                self.root, initial.prior_app_files(self.root, self.prior_source.release)
            ),
            resources=self._resources(workers_release),
            protected_liabilities=liabilities,
            reachable_revisions=self._reachable(pins),
            maintenance_baseline=baseline,
        )
        plan.validate()
        for pin in pins:
            candidate_template(plan, pin, self.service(pin.service))
            need(
                self.revision(pin.service, plan.revision(pin.service)) is None,
                "candidate_revision_exists",
            )
        self.verify_global(plan)
        return plan

    def verify_global(self, plan):
        self._verify_global_before_resources(plan)
        need(
            self._resources(plan.workers_release) == plan.resources,
            "resource_or_document_state_changed",
        )
        self._verify_global_after_resources(plan)

    def _verify_global_before_resources(self, plan):
        plan.validate()
        need(type(plan) is self.plan_type, "distinct_app_plan_required")
        need(type(plan.maintenance_baseline) is ClosedApiBaseline, "maintenance_baseline_required")
        self._verify_maintenance_baseline(plan.maintenance_baseline)
        self.observer.verify_source_image(
            release=plan.release,
            image_digest=plan.image_digest,
            build_id=plan.build_id,
            ci_run_id=plan.ci_run_id,
        )
        self.observer._verify_source_image_provenance(
            release=self.prior_source.release,
            image_digest=self.prior_source.image_digest,
            build_id=self.prior_source.build_id,
            ci_run_id=self.prior_source.ci_run_id,
        )
        self._compatibility(plan)
        self.observer.verify_policy_configuration(plan.policies)
        self._identity(plan.runtime_uid)
        need(
            self._reachable(plan.services) == plan.reachable_revisions,
            "reachable_prior_revision_changed",
        )

    def _verify_global_after_resources(self, plan):
        signature, _ = self._database(
            plan.runtime_secret,
            plan.observer_secret,
            plan.retired_migration_secret,
            expected=plan.protected_liabilities,
        )
        need(signature == plan.financial_sha256, "database_contract_changed")
        for pin in plan.services:
            current = self.service(pin.service)
            rolling.worker_iam(pin.service, self.iam(pin.service))
            need(
                rolling.sha(self.iam(pin.service)) == pin.iam_sha256
                and rolling.static_sha(current) == pin.static_sha256,
                "identity_or_static_configuration_changed",
            )
            rolling._old_revision(self, pin)
            prior = self.revision(pin.service, pin.prior_revision)
            need(
                prior["containers"][0]["image"]
                == plan.image.split("@")[0] + "@" + self.prior_source.image_digest,
                "prior_image_changed",
            )
            original = {key: prior.get(key) for key in pin.revision_keys}
            if rolling.template_sha(original, pin.service) != pin.template_sha256:
                original["revision"] = pin.prior_revision
            need(
                rolling.template_sha(original, pin.service) == pin.template_sha256,
                "prior_service_template_changed",
            )
            target = candidate_template(
                plan,
                pin,
                {
                    **current,
                    "template": original,
                    "generation": pin.generation,
                    "observedGeneration": pin.generation,
                    "trafficStatuses": list(pin.traffic),
                },
            )
            historical.current_phase(plan, pin, current, target)
            for template in (current["template"], prior):
                observed_flags(template, pin.service, plan.maintenance_baseline
                    if rolling.environment(template).get("APP_RELEASE", {}).get("value") == PRIOR.release else None)
                if self.plan_type is App26V3Plan:
                    v3_flags(template)
                rolling.verify_policy_bindings(plan.policies, pin, template)
                rows = rolling.environment(template)
                for secret in pin.secrets:
                    need(
                        rows.get(secret.environment_name)
                        == {
                            "name": secret.environment_name,
                            "valueSource": {
                                "secretKeyRef": {"secret": secret.secret, "version": secret.version}
                            },
                        }
                        and self._reference(secret.secret, secret.version) == secret.version,
                        "secret_reference_changed",
                    )


def load(raw):
    value = rolling.release._json(raw)
    need(value.get("schema_contract") == contract.CONTRACT, "distinct_plan_required")
    value["policies"] = rolling.PolicyPins(**value["policies"])
    value["prior_source"] = PriorSource(**value["prior_source"])
    value["resources"] = tuple(ResourcePin(**row) for row in value["resources"])
    value["reachable_revisions"] = tuple(
        ReachablePin(**row) for row in value["reachable_revisions"]
    )
    value["protected_liabilities"] = tuple(tuple(row) for row in value["protected_liabilities"])
    if value.get("maintenance_baseline") is not None:
        row = value["maintenance_baseline"]
        row["original_pilot_flags"] = tuple(tuple(pair) for pair in row["original_pilot_flags"])
        value["maintenance_baseline"] = ClosedApiBaseline(**row)
    pins = []
    for row in value["services"]:
        row["secrets"] = tuple(rolling.SecretPin(**secret) for secret in row["secrets"])
        row["revision_keys"], row["traffic"] = tuple(row["revision_keys"]), tuple(row["traffic"])
        pins.append(rolling.ServicePin(**row))
    value["services"] = tuple(pins)
    plan = App26V3Plan(**value)
    plan.validate()
    return plan


def stage_closed_api(plan, boundary, journal, *, reconcile=False):
    """V3-only stage request; original shared engine remains byte-for-byte unchanged."""
    need(type(plan) is App26V3Plan and type(plan.maintenance_baseline) is ClosedApiBaseline
         and type(journal) is rolling.Journal and journal.plan_hash == rolling.sha(asdict(plan)),
         "maintenance_baseline_required")
    pin = plan.services[0]
    marker = "stage." + pin.service
    started, ack, checked = (journal.read(marker + suffix) for suffix in (".started", ".ack", ".checked"))
    if checked is not None:
        need(started is not None, "maintenance_stage_checked_without_start")
        rolling._readback(plan, boundary, pin, started)
        return True
    boundary.verify_global(plan)
    if started is None:
        need(not reconcile and journal.read(historical.ROLLBACK_INTENT) is None
             and not any(journal.read(later + "." + service + ".started")
                         for later in ("promote", "rollback") for service in SERVICES),
             "maintenance_stage_not_authorized")
        current = boundary.service(pin.service)
        rolling._ready(current, pin)
        need(rolling.sha(boundary.iam(pin.service)) == pin.iam_sha256,
             "maintenance_stage_iam_changed")
        rolling._old_revision(boundary, pin)
        need(boundary.revision(pin.service, plan.revision(pin.service)) is None,
             "maintenance_candidate_revision_exists")
        body = rolling.stage_body(plan, pin, current)
        body["template"] = candidate_template(plan, pin, current)
        started = {"phase": "stage", "request_sha256": rolling.sha(body), "mask": "template,traffic",
            "template_sha256": rolling.template_sha(body["template"], pin.service),
            "traffic": body["traffic"], "expected_generation": int(current["generation"]) + 1,
            "service_uid": pin.uid, "previous_etag": current["etag"], "replay_authorized": False}
        need(boundary.service(pin.service) == current, "maintenance_stage_preimage_changed")
        journal.record(marker + ".started", started)
        operation = boundary.patch(pin.service, body, "template,traffic")
        name = operation.get("name")
        need(type(name) is str and rolling.OPERATION.fullmatch(name), "maintenance_stage_ack_unknown")
        ack = {"operation": name, "request_sha256": started["request_sha256"]}
        journal.record(marker + ".ack", ack)
    if ack is None:
        need(reconcile, "maintenance_stage_unknown_reconcile_only")
        observation = rolling._readback(plan, boundary, pin, started)
    else:
        need(ack.get("request_sha256") == started["request_sha256"]
             and rolling.OPERATION.fullmatch(ack.get("operation", "")), "maintenance_stage_ack_changed")
        operation = boundary.operation(ack["operation"])
        need(operation.get("name") == ack["operation"] and "error" not in operation,
             "maintenance_stage_operation_failed")
        if operation.get("done") is not True:
            return False
        observation = rolling._readback(plan, boundary, pin, started)
    journal.record(marker + ".checked", {**observation, "replay_authorized": False,
        "operation_ack_retained": ack is not None})
    return True


def execute(phase, plan, boundary, journal):
    need(
        type(plan) is App26V3Plan
        and type(journal) is rolling.Journal
        and journal.plan_hash == rolling.sha(asdict(plan)),
        "original_plan_journal_required",
    )
    plan.validate()
    if phase in {"stage", "advance"}:
        need(journal.read(historical.ROLLBACK_INTENT) is None, "explicit_rollback_in_progress")
    if phase == "stage":
        with journal.locked():
            need(journal.read(historical.ROLLBACK_INTENT) is None, "explicit_rollback_in_progress")
            need(
                not any(
                    journal.read(later + "." + name + ".started")
                    for later in ("promote", "rollback")
                    for name in SERVICES
                ),
                "stage_already_passed",
            )
            for pin in plan.services:
                complete = (stage_closed_api(plan, boundary, journal)
                    if plan.maintenance_baseline is not None and pin.service == SERVICES[0]
                    else rolling.action(plan, boundary, journal, pin, "stage"))
                if not complete:
                    return {
                        "status": "schema26_app_same_operation_pending",
                        "phase": "stage",
                        "rolling_release_complete": False,
                        "replay_authorized": False,
                    }
            boundary.verify_global(plan)
        return {
            "status": "schema26_app_staged_zero_traffic",
            "schema": schema26.SCHEMA,
            "rolling_release_complete": False,
            "migrations": 0,
            "resource_access_changes": 0,
            "evidence_fence_required": True,
            "replay_authorized": False,
        }
    if (
        phase == "rollback"
        or phase == "reconcile"
        and journal.read(historical.ROLLBACK_INTENT) is not None
    ):
        result = historical.rollback_app(plan, boundary, journal, reconcile=phase == "reconcile")
    else:
        need(phase in {"advance", "reconcile"}, "phase_invalid")
        if (phase == "advance" and plan.maintenance_baseline is not None
                and journal.read("promote." + SERVICES[0] + ".started") is None):
            with journal.locked():
                need(journal.read(historical.ROLLBACK_INTENT) is None, "explicit_rollback_in_progress")
                if not stage_closed_api(plan, boundary, journal):
                    return {"status": "schema26_app_same_operation_pending", "phase": "stage",
                        "rolling_release_complete": False, "replay_authorized": False}
        result = {"advance": rolling.advance, "reconcile": rolling.reconcile}[phase](
            plan, boundary, journal
        )
    status = {
        "joined_schema18_release_observed": "schema26_app_release_observed",
        "existing_actions_reconciled_only": "schema26_app_existing_actions_reconciled",
        "prior_traffic_restored_schema18": "schema26_app_prior_traffic_restored_writes_restricted",
        "same_native_operation_pending": "schema26_app_same_operation_pending",
    }.get(result.get("status"))
    need(status is not None, "shared_result_unrecognized")
    result = {
        key: value
        for key, value in result.items()
        if key not in {"status", "schema", "queue_admission"}
    }
    return {
        **result,
        "status": status,
        "schema": schema26.SCHEMA,
        "migrations": 0,
        "resource_access_changes": 0,
        "evidence_fence_required": True,
        "old_source_evidence_writes_authorized": False,
        "frontend_recovery_qualified": False,
        "frontend_rollout_order": "api_then_frontend",
        "disaster_recovery_rto_qualified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=("source", "capture", "observe", "stage", "advance", "reconcile", "rollback"),
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--maintenance-plan", type=Path)
    parser.add_argument("--maintenance-plan-sha256")
    parser.add_argument("--maintenance-journal", type=Path)
    for name in (
        "release",
        "image-digest",
        "build-id",
        "nonce",
        "observer-secret",
        "retired-migration-secret",
        "workers-release",
    ):
        parser.add_argument("--" + name)
    parser.add_argument("--ci-run-id", type=int)
    args = parser.parse_args()
    if args.phase == "source":
        fingerprint = contract.source_fingerprint(
            args.root, initial.prior_app_files(args.root, PRIOR.release)
        )
        print(
            json.dumps(
                {
                    "source_sha256": fingerprint,
                    "serving_prior_observed": False,
                    "schema26_applied": False,
                    "migration_authority": False,
                    "rollout_authority": False,
                }
            )
        )
        return
    need(args.plan is not None, "private_plan_required")
    need(
        os.getenv("CLOUDSDK_CORE_DISABLE_FILE_LOGGING") == "true"
        and os.getenv("CLOUDSDK_CORE_LOG_HTTP") == "false",
        "private_sdk_logging_required",
    )
    need(args.maintenance_plan is not None and args.maintenance_plan_sha256 is not None
         and args.maintenance_journal is not None, "maintenance_custody_required")
    boundary = NativeApp26Boundary(args.root, maintenance_plan_path=args.maintenance_plan,
        maintenance_plan_sha256=args.maintenance_plan_sha256, maintenance_journal=args.maintenance_journal)
    if args.phase == "capture":
        names = (
            "release",
            "image_digest",
            "build_id",
            "ci_run_id",
            "nonce",
            "observer_secret",
            "retired_migration_secret",
            "workers_release",
        )
        need(all(getattr(args, name) is not None for name in names), "capture_arguments_required")
        plan = boundary.capture_app(**{name: getattr(args, name) for name in names})
        need(
            args.plan.is_absolute()
            and args.plan.parent.resolve() == args.plan.parent
            and args.plan.parent.stat().st_uid == os.getuid()
            and stat.S_IMODE(args.plan.parent.stat().st_mode) == 0o700,
            "private_plan_required",
        )
        raw = rolling.canonical(asdict(plan)) + b"\n"
        descriptor = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(args.plan.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        print(
            json.dumps(
                {
                    "status": "schema26_app_plan_captured",
                    "plan_sha256": hashlib.sha256(raw).hexdigest(),
                    "native_mutations": 0,
                    "migration_authority": False,
                }
            )
        )
        return
    raw = initial._read_pinned(args.plan, args.plan_sha256)
    plan = load(raw)
    if args.phase == "observe":
        boundary.verify_global(plan)
        print(json.dumps({"status": "schema26_app_readonly_observed", "native_mutations": 0}))
        return
    need(args.journal is not None, "owned_journal_required")
    print(json.dumps(execute(args.phase, plan, boundary, rolling.Journal(args.journal, plan))))


if __name__ == "__main__":
    main()
