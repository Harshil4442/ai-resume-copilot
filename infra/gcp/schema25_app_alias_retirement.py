"""Finite traffic-only retirement of retained zero-percent8cb aliases.

Uses the existing native boundary and create-only journal. It never updates a
template, deletes a revision/image, grants access, or replays an old app plan.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import asdict, dataclass

import rolling_release as rolling
import schema25_app_rolling_release as app

CONTRACT = "hirewiz_schema25_alias_retirement_v1"
UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")
CODE_PATHS = ("infra/gcp/schema25_app_alias_retirement.py", "infra/gcp/schema25_app_rolling_release.py",
              "backend/tests/test_schema25_app_alias_retirement.py", "backend/scripts/schema25_app_release_contract_v2.py",
              ".github/workflows/ci.yml")


def need(value, code):
    app.need(value, "aliases_" + code)


@dataclass(frozen=True)
class Revision:
    service: str
    revision: str
    uid: str
    release: str
    template_sha256: str


@dataclass(frozen=True)
class AliasPlan(rolling.RollingPlan):
    schema_contract: str = CONTRACT
    operator_release: str = ""
    operator_ci_run_id: int = 0
    operator_sha256: str = ""
    resources: tuple[app.ResourcePin, ...] = ()
    protected_liabilities: tuple[tuple[str, str, int], ...] = ()
    reachable_revisions: tuple[Revision, ...] = ()

    def disabled_flags(self):
        return app.CLOSED_FLAGS

    def validate(self):
        super().validate()
        need(type(self) is AliasPlan and self.schema_contract == CONTRACT
             and (self.release, self.image_digest, self.build_id, self.ci_run_id) == (
                 app.PRIOR_V2.release, app.PRIOR_V2.image_digest, app.PRIOR_V2.build_id, app.PRIOR_V2.ci_run_id)
             and self.candidate_policies is None and re.fullmatch(r"[a-f0-9]{40}", self.operator_release)
             and type(self.operator_ci_run_id) is int and self.operator_ci_run_id > 0
             and rolling.HASH.fullmatch(self.operator_sha256), "plan_invalid")
        app.validate_extensions(self)
        need(all(pin.prior_release == self.release for pin in self.services)
             and all(row.release in {app.PRIOR.release, app.PRIOR_V2.release}
                     and UUID.fullmatch(row.uid) for row in self.reachable_revisions),
             "reachable_source_invalid")
        need(any(len(desired(self, pin)) < len(pin.traffic) for pin in self.services), "no_retained_aliases")


def desired(plan, pin):
    sources = {row.revision: row.release for row in plan.reachable_revisions if row.service == pin.service}
    return [copy.deepcopy(row) for row in pin.traffic
            if not (row.get("tag") and row.get("percent", 0) == 0 and sources.get(row["revision"]) == app.PRIOR.release)]


def phase(plan, pin, current):
    rolling._ready(current, pin)
    need(rolling.static_sha(current) == pin.static_sha256
         and rolling.template_sha(current["template"], pin.service) == pin.template_sha256
         and all(rolling.short_revision(current[key], pin.service) == pin.prior_revision
                 for key in ("latestCreatedRevision", "latestReadyRevision")), "configuration_changed")
    actual = rolling.pinned_traffic(pin.service, current)
    before, after = list(pin.traffic), desired(plan, pin)
    generation = int(current["generation"])
    if generation == pin.generation and actual == before:
        return "before"
    need(after != before and generation == pin.generation + 1 and actual == after, "traffic_or_generation_changed")
    return "after"


def code_sha(root):
    return rolling.sha({path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in CODE_PATHS})


def revision_pin(boundary, service, name, baseline):
    actual = boundary.revision(service, name)
    source = rolling.environment(actual).get("APP_RELEASE", {}).get("value")
    prior = app.PRIOR if source == app.PRIOR.release else app.PRIOR_V2
    need(source == prior.release and source in {app.PRIOR.release, app.PRIOR_V2.release}, "unknown_source")
    app.reachable_pin(service, name, actual, prior_source=prior)
    app.v2_flags(actual)
    need(type(actual.get("uid")) is str and UUID.fullmatch(actual["uid"]), "revision_uid_missing")
    # Source/image are independently proved above; all other configuration
    # must equal the actual activeab2 revision, allowing only fewer pilot flags.
    left: list[dict] = []
    right: list[dict] = []
    for value, target in ((actual, left), (baseline, right)):
        clean = copy.deepcopy({k: v for k, v in value.items() if k not in app.maintenance.REVISION_READ_OUTPUTS | {"revision"}})
        clean.get("labels", {}).pop("hirewiz-source", None)
        clean["containers"][0].pop("image")
        env = rolling.environment(clean)
        env.pop("APP_RELEASE", None)
        for flag in app.PILOT_FLAGS:
            original = rolling.environment(baseline).get(flag, {"value": "false"})
            current = env.pop(flag, {"value": "false"})
            need(current.get("value") in {"true", "false"} and not (
                original.get("value") != "true" and current.get("value") == "true"), "pilot_expansion")
        clean["containers"][0]["env"] = sorted(env.values(), key=rolling.canonical)
        target.append(clean)
    need(left == right, "retained_configuration_differs")
    template = {k: v for k, v in actual.items() if k not in app.maintenance.REVISION_READ_OUTPUTS}
    return Revision(service, name, actual["uid"], source, rolling.template_sha(template, service))


class NativeAliases(app.NativeApp25BoundaryV2):
    def _operator(self, plan):
        need(code_sha(self.root) == plan.operator_sha256, "operator_changed")
        app.contract_v2.verify_ci(self.root)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True, timeout=10).strip()
        need(head == plan.operator_release and not subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=self.root, timeout=10), "operator_source_uncommitted")
        run = rolling.release._json(self.observer._command("gh", "run", "view", str(plan.operator_ci_run_id),
            "--repo", rolling.release.REPOSITORY, "--json", "headSha,status,conclusion,jobs"))
        jobs = run.get("jobs", [])
        need(run.get("headSha") == plan.operator_release and run.get("status") == "completed"
             and run.get("conclusion") == "success" and len(jobs) == 5
             and {row.get("name") for row in jobs} == rolling.release.CI_JOBS
             and all(row.get("conclusion") == "success" for row in jobs), "operator_ci_unqualified")
        steps = [step for job in jobs if job.get("name") == "backend" for step in job.get("steps", [])
                 if step.get("name") in {app.contract_v2.COMPATIBILITY_STEP, app.contract_v2.PROTOCOL_STEP}]
        need(len(steps) == 2 and {step.get("name") for step in steps} == {
             app.contract_v2.COMPATIBILITY_STEP, app.contract_v2.PROTOCOL_STEP}
             and all(step.get("conclusion") == "success" and step.get("status") == "completed"
             for step in steps), "operator_qualification_unavailable")

    def capture_aliases(self, *, operator_release, operator_ci_run_id, nonce, observer_secret, retired_migration_secret, workers_release):
        pins = tuple(self._pin(name) for name in app.SERVICES)
        refs = {row.environment_name: row.secret + ":" + row.version for row in pins[0].secrets}
        env = rolling.environment(self.service(app.SERVICES[0])["template"])
        policies = rolling.PolicyPins(refs["HIREWIZ_EXPENSE_POLICY_JSON"],
            hashlib.sha256(self.observer._secret(refs["HIREWIZ_EXPENSE_POLICY_JSON"]).encode()).hexdigest(),
            refs["LLM_MODEL_COST_POLICY_JSON"], hashlib.sha256(self.observer._secret(refs["LLM_MODEL_COST_POLICY_JSON"]).encode()).hexdigest(),
            refs["LLM_API_KEY"], env["LLM_MODEL"]["value"], env["LLM_API_BASE"]["value"])
        signature, liabilities = self._database(refs["DATABASE_URL"], observer_secret, retired_migration_secret)
        rows = tuple(revision_pin(self, pin.service, name, self.revision(pin.service, pin.prior_revision))
            for pin in pins for name in sorted({row["revision"] for row in pin.traffic}))
        account = self.observer._cloud("iam", "service-accounts", "describe", rolling.RUNTIME)
        plan = AliasPlan(app.PRIOR_V2.release, app.PRIOR_V2.image_digest, app.PRIOR_V2.build_id,
            app.PRIOR_V2.ci_run_id, nonce, account["uniqueId"], refs["DATABASE_URL"], observer_secret,
            retired_migration_secret, workers_release, signature, policies, pins,
            operator_release=operator_release, operator_ci_run_id=operator_ci_run_id, operator_sha256=code_sha(self.root),
            resources=self._resources(workers_release), protected_liabilities=liabilities, reachable_revisions=rows)
        self.verify_aliases(plan)
        return plan

    def verify_aliases(self, plan):
        plan.validate()
        self._operator(plan)
        self._identity(plan.runtime_uid)
        for source in (app.PRIOR, app.PRIOR_V2):
            self.observer._verify_source_image_provenance(release=source.release, image_digest=source.image_digest,
                build_id=source.build_id, ci_run_id=source.ci_run_id)
        self.observer.verify_policy_configuration(plan.policies)
        signature, _ = self._database(plan.runtime_secret, plan.observer_secret,
            plan.retired_migration_secret, expected=plan.protected_liabilities)
        need(signature == plan.financial_sha256 and self._resources(plan.workers_release) == plan.resources, "resource_or_financial_drift")
        for pin in plan.services:
            current = self.service(pin.service)
            phase(plan, pin, current)
            app.observed_flags(current["template"], pin.service)
            app.v2_flags(current["template"])
            rolling.worker_iam(pin.service, self.iam(pin.service))
            need(rolling.sha(self.iam(pin.service)) == pin.iam_sha256, "iam_changed")
            rolling.verify_policy_bindings(plan.policies, pin, current["template"])
            for secret in pin.secrets:
                need(self._reference(secret.secret, secret.version) == secret.version, "secret_changed")
            baseline = self.revision(pin.service, pin.prior_revision)
            rows = tuple(revision_pin(self, pin.service, row.revision, baseline)
                for row in plan.reachable_revisions if row.service == pin.service)
            need(rows == tuple(row for row in plan.reachable_revisions if row.service == pin.service), "revision_changed")
            health = [row["tag"] for row in desired(plan, pin) if row.get("tag") and row["revision"] == pin.prior_revision]
            need(bool(health), "retained_ab2_health_tag_required")
            self.health(plan, pin.service, pin.prior_revision, plan.release, health[0])


def load(raw):
    value = rolling.release._json(raw)
    need(value.get("schema_contract") == CONTRACT, "wrong_plan_kind")
    value["policies"] = rolling.PolicyPins(**value["policies"])
    value["services"] = tuple(rolling.ServicePin(**(row | {"secrets": tuple(rolling.SecretPin(**s) for s in row["secrets"]),
        "revision_keys": tuple(row["revision_keys"]), "traffic": tuple(row["traffic"])})) for row in value["services"])
    value["resources"] = tuple(app.ResourcePin(**row) for row in value["resources"])
    value["protected_liabilities"] = tuple(tuple(row) for row in value["protected_liabilities"])
    value["reachable_revisions"] = tuple(Revision(**row) for row in value["reachable_revisions"])
    plan = AliasPlan(**value)
    plan.validate()
    return plan


def retained_intent(plan, pin, journal, started):
    need(type(started.get("previous_etag")) is str and bool(started["previous_etag"]), "intent_changed")
    body = {"name": rolling.PARENT + "/services/" + pin.service,
            "etag": started["previous_etag"], "traffic": desired(plan, pin)}
    need(started == {"request_sha256": rolling.sha(body), "previous_etag": started["previous_etag"],
        "service_uid": pin.uid, "expected_generation": pin.generation + 1, "replay_authorized": False,
        "plan_sha256": journal.plan_hash} and started["replay_authorized"] is False
        and type(started["expected_generation"]) is int, "intent_changed")


def retained_ack(journal, started, ack):
    operation = ack.get("operation")
    need(type(operation) is str and rolling.OPERATION.fullmatch(operation)
         and ack == {"operation": operation, "request_sha256": started["request_sha256"],
                     "plan_sha256": journal.plan_hash}, "ack_changed")


def retained_actions(plan, journal):
    """Validate every retained row before any native read or write authority."""
    actions = {}
    operations = []
    for pin in plan.services:
        key = "aliases." + pin.service
        started, ack, checked = (journal.read(key + suffix) for suffix in (".started", ".ack", ".checked"))
        if started is None or desired(plan, pin) == list(pin.traffic):
            need(ack is None and checked is None and started is None, "orphaned_action")
        else:
            retained_intent(plan, pin, journal, started)
            if ack is not None:
                retained_ack(journal, started, ack)
                operations.append(ack["operation"])
            if checked is not None:
                need(checked == {"traffic_sha256": rolling.sha(desired(plan, pin)),
                    "prior_http_health_observed": True, "operation_ack_retained": ack is not None,
                    "replay_authorized": False, "plan_sha256": journal.plan_hash}
                    and checked["prior_http_health_observed"] is True
                    and checked["operation_ack_retained"] is (ack is not None)
                    and checked["replay_authorized"] is False, "checked_changed")
        actions[pin.service] = (started, ack, checked)
    need(len(operations) == len(set(operations)), "operation_reused")
    return actions


def retained_operations(boundary, actions, *, reconcile):
    """Read exact retained LROs before settled proof across all services."""
    need(reconcile or all(started is None or checked is not None
         for started, _, checked in actions.values()), "unknown_send_reconcile_only")
    done, pending = set(), False
    for _, ack, _ in actions.values():
        if ack is None:
            continue
        observed = boundary.operation(ack["operation"])
        need(observed.get("name") == ack["operation"] and "error" not in observed, "operation_failed")
        if observed.get("done", False) is True:
            done.add(ack["operation"])
        else:
            pending = True
    return done, pending


def execute(plan, boundary, journal, *, reconcile=False):
    plan.validate()
    need(type(journal) is rolling.Journal and journal.plan_hash == rolling.sha(asdict(plan)), "journal_changed")
    writes = 0
    with journal.locked():
        actions = retained_actions(plan, journal)
        observed_done, pending = retained_operations(boundary, actions, reconcile=reconcile)
        if pending:
            return {"status": "alias_retirement_pending", "replay_authorized": False, "native_mutations": 0}
        for pin in plan.services:
            if desired(plan, pin) == list(pin.traffic):
                boundary.verify_aliases(plan)
                continue
            key = "aliases." + pin.service
            started, ack, _ = actions[pin.service]
            if started is None:
                boundary.verify_aliases(plan)
                if reconcile:
                    continue
                current = boundary.service(pin.service)
                need(phase(plan, pin, current) == "before", "original_preimage_required")
                body = {"name": current["name"], "etag": current["etag"], "traffic": desired(plan, pin)}
                started = {"request_sha256": rolling.sha(body), "previous_etag": current["etag"],
                    "service_uid": pin.uid, "expected_generation": pin.generation + 1, "replay_authorized": False}
                need(boundary.service(pin.service) == current, "native_preimage_changed")
                journal.record(key + ".started", started)
                started = journal.read(key + ".started")
                writes += 1
                operation = boundary.patch(pin.service, body, "traffic")
                need(type(operation.get("name")) is str and rolling.OPERATION.fullmatch(operation["name"]), "ack_unknown_reconcile_only")
                need(operation["name"] not in observed_done, "operation_reused")
                ack = {"operation": operation["name"], "request_sha256": started["request_sha256"]}
                journal.record(key + ".ack", ack)
                ack = journal.read(key + ".ack")
            retained_intent(plan, pin, journal, started)
            if ack is not None:
                retained_ack(journal, started, ack)
                if ack["operation"] not in observed_done:
                    observed = boundary.operation(ack["operation"])
                    need(observed.get("name") == ack["operation"] and "error" not in observed, "operation_failed")
                    if observed.get("done", False) is not True:
                        return {"status": "alias_retirement_pending", "replay_authorized": False, "native_mutations": writes}
                    observed_done.add(ack["operation"])
            # A retained exact operation can be observed while the service is
            # reconciling. This read grants no new write or health authority;
            # terminal acknowledgement still needs the entire settled proof.
            boundary.verify_aliases(plan)
            if phase(plan, pin, boundary.service(pin.service)) != "after":
                return {"status": "alias_retirement_unknown_reconcile_only", "replay_authorized": False, "native_mutations": writes}
            boundary.verify_aliases(plan)
            if journal.read(key + ".checked") is None:
                journal.record(key + ".checked", {"traffic_sha256": rolling.sha(desired(plan, pin)), "prior_http_health_observed": True,
                    "operation_ack_retained": ack is not None, "replay_authorized": False})
        boundary.verify_aliases(plan)
        complete = all(desired(plan, pin) == list(pin.traffic) or (
            phase(plan, pin, boundary.service(pin.service)) == "after" and journal.read("aliases." + pin.service + ".checked") is not None)
            for pin in plan.services)
    historical = any(phase(plan, pin, boundary.service(pin.service)) == "after" for pin in plan.services
                     if desired(plan, pin) != list(pin.traffic))
    return {"status": "alias_retirement_observed" if complete else "existing_alias_actions_reconciled_only",
        "native_mutations": writes, "replay_authorized": False, "prior_app_plan_historical": historical,
        "revisions_and_images_retained": True, "native_mutation_scope": "traffic_only"}


def main(args):
    boundary = NativeAliases(args.root)
    if args.phase == "aliases-capture":
        need(all(getattr(args, key) is not None for key in ("release", "ci_run_id", "nonce",
            "observer_secret", "retired_migration_secret", "workers_release")), "capture_arguments_required")
        plan = boundary.capture_aliases(operator_release=args.release, operator_ci_run_id=args.ci_run_id, nonce=args.nonce,
            observer_secret=args.observer_secret, retired_migration_secret=args.retired_migration_secret, workers_release=args.workers_release)
        need(args.plan.is_absolute() and args.plan.parent.resolve() == args.plan.parent and
            args.plan.parent.stat().st_uid == os.getuid() and stat.S_IMODE(args.plan.parent.stat().st_mode) == 0o700, "private_plan_required")
        raw = rolling.canonical(asdict(plan)) + b"\n"
        fd = os.open(args.plan, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({"status": "alias_plan_captured", "plan_sha256": hashlib.sha256(raw).hexdigest(), "native_mutations": 0}))
        return
    plan = load(app.initial._read_pinned(args.plan, args.plan_sha256))
    if args.phase == "aliases-observe":
        boundary.verify_aliases(plan)
        print(json.dumps({"status": "alias_plan_readonly_observed", "native_mutations": 0}))
        return
    need(args.journal is not None, "journal_required")
    print(json.dumps(execute(plan, boundary, rolling.Journal(args.journal, plan), reconcile=args.phase == "aliases-reconcile")))

