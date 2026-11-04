"""Ordinary schema26 application successors; no migration or old-journal authority.

Controller, prior application and target application have independent source/CI
identities. Only this new journal owns actions. Reconciliation never starts one.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend"), str(ROOT)]
import rolling_native as native  # noqa: E402
import rolling_release as rolling  # noqa: E402
import schema26_app_rolling_v3 as app26  # noqa: E402

KIND = "hirewiz_schema26_app_successor_v1"
SCHEMA = "20261031_0026"
REQUIRED_STEPS = frozenset(
    {
        app26.contract.COMPATIBILITY_STEP,
        app26.contract.PROTOCOL_STEP,
        app26.contract.RETAINED_STEP,
        "Qualify retained actual-source schema25 app v1",
        "Run backend tests",
        "Check OpenAPI artifact drift",
    }
)
DATA_PARTS = frozenset(
    {
        "evals",
        "corpus",
        "inputs",
        "outputs",
        "oracle",
        "oracles",
        "selection",
        "heldout",
        "held-out",
    }
)


def need(value, code):
    rolling.need(value, "schema26_successor_" + code)


def _git(root, *args):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_NO_LAZY_FETCH="1",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
    )
    result = subprocess.run(
        ["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        capture_output=True,
        check=False,
        timeout=20,
        env=environment,
        stdin=subprocess.DEVNULL,
    )
    need(result.returncode == 0 and len(result.stdout) <= 16 * 1024**2, "git")
    return result.stdout


def controller_source(root):
    """Committed code/config bytes only; never read evaluation data blobs."""
    root = root.resolve()
    head = _git(root, "rev-parse", "HEAD").decode().strip()
    need(
        re.fullmatch(r"[a-f0-9]{40}", head) and not _git(root, "status", "--porcelain=v1"),
        "controller_clean",
    )
    names = _git(root, "ls-tree", "-r", "-z", head).split(b"\0")
    hashes = {}
    for row in names:
        if not row:
            continue
        metadata, encoded = row.split(b"\t", 1)
        mode, kind, stamp = metadata.split()
        name = os.fsdecode(encoded)
        if set(Path(name).parts) & DATA_PARTS:
            continue
        code = name.startswith(("backend/app/", "backend/scripts/", "infra/")) and Path(
            name
        ).suffix in {".py", ".json"}
        if not code and name not in {
            "backend/pyproject.toml",
            "backend/uv.lock",
            ".github/workflows/ci.yml",
        }:
            continue
        path = root / name
        need(
            mode in {b"100644", b"100755"}
            and kind == b"blob"
            and path.is_file()
            and not path.is_symlink()
            and path.resolve() == path,
            "controller_path",
        )
        raw = path.read_bytes()
        need(
            hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False)
            .hexdigest()
            .encode()
            == stamp,
            "controller_bytes",
        )
        hashes[name] = hashlib.sha256(raw).hexdigest()
    own = "backend/scripts/schema26_app_successor.py"
    need(own in hashes and Path(__file__).resolve() == root / own, "controller_origin")
    for module, name in (
        (rolling, "infra/gcp/rolling_release.py"),
        (native, "infra/gcp/rolling_native.py"),
        (app26, "infra/gcp/schema26_app_rolling_v3.py"),
    ):
        origin = module.__file__
        need(type(origin) is str, "controller_import")
        assert isinstance(origin, str)
        need(Path(origin).resolve() == root / name, "controller_import")
    need(_git(root, "rev-parse", "HEAD").decode().strip() == head, "controller_changed")
    return head, rolling.sha(hashes)


def verify_app_equivalence(controller_root, target_root):
    """Require cached controller app imports to equal the qualified target.

    Adding a sys.path entry does not replace app's package/module cache. These
    source checks establish byte equivalence, not process import isolation.
    The separately qualified prior application is deliberately not compared.
    """
    inventories = []
    for root in (controller_root, target_root):
        need(not _git(root, "status", "--porcelain=v1", "--", "backend/app"), "app_code_dirty")
        entries: dict[str, tuple[bytes, bytes, bytes]] = {}
        total = 0
        for row in _git(root, "ls-tree", "-r", "-z", "HEAD", "--", "backend/app").split(b"\0"):
            if not row:
                continue
            metadata, encoded = row.split(b"\t", 1)
            mode, kind, stamp = metadata.split()
            name = os.fsdecode(encoded)
            if Path(name).suffix not in {".py", ".json"}:
                continue
            path = root / name
            need(
                mode in {b"100644", b"100755"}
                and kind == b"blob"
                and path.is_file()
                and not path.is_symlink()
                and path.resolve() == path,
                "app_code_path",
            )
            need(path.stat().st_size <= 2 * 1024**2, "app_code_size")
            raw = path.read_bytes()
            total += len(raw)
            need(total <= 32 * 1024**2 and len(entries) < 2048, "app_code_bound")
            need(
                hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False)
                .hexdigest()
                .encode()
                == stamp,
                "app_code_bytes",
            )
            entries[name] = (mode, kind, stamp)
        need(bool(entries), "app_code_absent")
        inventories.append(entries)
    need(inventories[0] == inventories[1], "controller_target_app_differ")


def verify_controller_app_imports(root):
    """Bind cached app modules before reusing stock policy guards."""
    app_root = root / "backend/app"
    need("app" in sys.modules, "controller_app_origin")
    for name, module in tuple(sys.modules.items()):
        if name != "app" and not name.startswith("app."):
            continue
        paths = list(getattr(module, "__path__", ()))
        origin = getattr(module, "__file__", None)
        if isinstance(origin, str):
            paths.append(origin)
        need(
            bool(paths)
            and all(
                isinstance(path, str)
                and Path(path).is_relative_to(app_root)
                and Path(path).resolve() == Path(path)
                for path in paths
            ),
            "controller_app_origin",
        )
    policy = importlib.import_module("app.billing.cost_policy")
    origin = policy.__file__
    need(
        isinstance(origin, str) and Path(origin).resolve() == app_root / "billing/cost_policy.py",
        "controller_policy_origin",
    )


@dataclass(frozen=True)
class SourcePin:
    release: str
    image_digest: str
    build_id: str
    ci_run_id: int

    def validate(self):
        need(
            type(self) is SourcePin
            and all(
                type(value) is str for value in (self.release, self.image_digest, self.build_id)
            )
            and re.fullmatch(r"[a-f0-9]{40}", self.release)
            and re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_digest)
            and re.fullmatch(r"[a-f0-9-]{36}", self.build_id)
            and type(self.ci_run_id) is int
            and self.ci_run_id > 0,
            "source_pin",
        )


@dataclass(frozen=True)
class SuccessorPlan(rolling.RollingPlan):
    services: tuple[rolling.ServicePin, ...] = ()
    schema_contract: str = KIND
    database_schema: str = SCHEMA
    prior_source: SourcePin | None = None
    controller_release: str = ""
    controller_source_sha256: str = ""
    controller_ci_run_id: int = 0
    resources: tuple = ()
    protected_liabilities: tuple = ()
    reachable_revisions: tuple = ()
    candidate_employer_cache: rolling.EmployerCacheBinding | None = None

    def disabled_flags(self):
        return app26.CLOSED_FLAGS

    def promoted_aliases(self, pin, rows):
        return app26.App26V3Plan.promoted_aliases(self, pin, rows)

    def validate(self):
        super().validate()
        need(
            type(self) is SuccessorPlan
            and self.schema_contract == KIND
            and self.database_schema == SCHEMA
            and self.candidate_policies is None
            and type(self.prior_source) is SourcePin,
            "plan_kind",
        )
        assert isinstance(self.prior_source, SourcePin)
        self.prior_source.validate()
        need(
            self.prior_source.release != self.release
            and all(pin.prior_release == self.prior_source.release for pin in self.services),
            "prior_source",
        )
        need(
            re.fullmatch(r"[a-f0-9]{40}", self.controller_release)
            and rolling.HASH.fullmatch(self.controller_source_sha256)
            and type(self.controller_ci_run_id) is int
            and self.controller_ci_run_id > 0,
            "controller_pin",
        )
        app26.historical.validate_extensions(self)
        cache = rolling.employer_cache_binding(self)
        if cache is not None:
            source = [
                row
                for row in self.services[0].secrets
                if row.environment_name == "RATE_LIMIT_STORAGE_URL"
            ]
            need(
                len(source) == 1
                and (source[0].secret, source[0].version) == (cache.secret, cache.version),
                "cache_must_reuse_existing_api_reference",
            )
            need(
                not any(row.environment_name == "REDIS_URL" for row in self.services[2].secrets),
                "cache_already_present",
            )


def _native_ci(observer, release, run_id):
    ci = rolling.release._json(
        observer._command(
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            rolling.release.REPOSITORY,
            "--json",
            "headSha,status,conclusion,jobs,workflowName",
        )
    )
    workflow = rolling.release._json(
        observer._command("gh", "api", f"repos/{rolling.release.REPOSITORY}/actions/runs/{run_id}")
    )
    jobs = ci.get("jobs", [])
    need(
        ci.get("headSha") == workflow.get("head_sha") == release
        and ci.get("workflowName") == "CI"
        and workflow.get("path") == ".github/workflows/ci.yml"
        and workflow.get("event") in {"push", "pull_request"}
        and workflow.get("repository", {}).get("full_name") == rolling.release.REPOSITORY
        and ci.get("status") == "completed"
        and ci.get("conclusion") == "success"
        and len(jobs) == len(rolling.release.CI_JOBS)
        and {job.get("name") for job in jobs} == rolling.release.CI_JOBS
        and all(
            job.get("status") == "completed" and job.get("conclusion") == "success" for job in jobs
        ),
        "ci",
    )
    backend = next(job for job in jobs if job["name"] == "backend")
    steps = [step for step in backend.get("steps", []) if step.get("name") in REQUIRED_STEPS]
    need(
        len(steps) == len(REQUIRED_STEPS)
        and {step.get("name") for step in steps} == REQUIRED_STEPS
        and all(
            step.get("status") == "completed" and step.get("conclusion") == "success"
            for step in steps
        ),
        "ci_steps",
    )


class NativeSuccessorBoundary(native.NativeRollingBoundary):
    """Fixed native ports; CLI accepts no transport or verifier plugin."""

    _database = app26.NativeApp26Boundary._database
    _resources = app26.NativeApp26Boundary._resources

    def __init__(self, root, target_root, prior_root):
        super().__init__(root)
        self.target_root, self.prior_root = target_root.resolve(), prior_root.resolve()
        self.observer = app26.Source26Boundary(self.target_root)
        self.prior_observer = app26.Source26Boundary(self.prior_root)
        self.journal = None

    def _sources(self, plan):
        self._verify_sources(
            plan.controller_release,
            plan.controller_source_sha256,
            plan.controller_ci_run_id,
            SourcePin(plan.release, plan.image_digest, plan.build_id, plan.ci_run_id),
            plan.prior_source,
        )

    def _verify_sources(self, controller_release, controller_sha256, controller_ci, target, prior):
        need(
            controller_source(self.root) == (controller_release, controller_sha256),
            "controller_source",
        )
        sources = (
            (self.observer, self.target_root, target),
            (self.prior_observer, self.prior_root, prior),
        )
        for _, root, pin in sources:
            need(_git(root, "rev-parse", "HEAD").decode().strip() == pin.release, "source_checkout")
        verify_app_equivalence(self.root, self.target_root)
        verify_controller_app_imports(self.root)
        _native_ci(self.observer, controller_release, controller_ci)
        for observer, _, pin in sources:
            observer.verify_source_image(**asdict(pin))
            _native_ci(observer, pin.release, pin.ci_run_id)

    def _target_template(self, plan, pin, current, prior):
        # Pure request construction from the freshly verified immutable prior,
        # not a substituted native receipt or a mutation inside patch().
        baseline = {
            **current,
            "template": prior,
            "generation": pin.generation,
            "observedGeneration": pin.generation,
            "trafficStatuses": list(pin.traffic),
            "latestReadyRevision": pin.prior_revision,
            "latestCreatedRevision": pin.prior_revision,
            "reconciling": False,
            "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
        }
        return rolling.stage_body(plan, pin, baseline)["template"]

    def verify_global(self, plan):
        plan.validate()
        self._sources(plan)
        self.observer.verify_policy_configuration(plan.policies)
        self._identity(plan.runtime_uid)
        need(
            self._reachable(plan.services, plan.prior_source) == plan.reachable_revisions,
            "reachable_prior_changed",
        )
        need(self._resources(plan.workers_release) == plan.resources, "resources_changed")
        signature, _ = self._database(
            plan.runtime_secret,
            plan.observer_secret,
            plan.retired_migration_secret,
            expected=plan.protected_liabilities,
        )
        need(signature == plan.financial_sha256, "schema_roles_fence_liabilities")
        for pin in plan.services:
            current = self.service(pin.service)
            rolling.worker_iam(pin.service, self.iam(pin.service))
            need(
                rolling.static_sha(current) == pin.static_sha256
                and rolling.sha(self.iam(pin.service)) == pin.iam_sha256,
                "static_or_iam_changed",
            )
            rolling._old_revision(self, pin)
            revision = self.revision(pin.service, pin.prior_revision)
            prior = {key: revision.get(key) for key in pin.revision_keys}
            if rolling.template_sha(prior, pin.service) != pin.template_sha256:
                prior["revision"] = pin.prior_revision
            need(
                rolling.template_sha(prior, pin.service) == pin.template_sha256
                and revision["containers"][0]["image"].endswith(
                    "@" + plan.prior_source.image_digest
                ),
                "prior_revision",
            )
            target = self._target_template(plan, pin, current, prior)
            started = None
            if self.journal is not None:
                started = next(
                    (
                        row
                        for phase in ("rollback", "promote", "stage")
                        if (row := self.journal.read(phase + "." + pin.service + ".started"))
                        is not None
                    ),
                    None,
                )
            at_prior = (
                int(current["generation"]) == pin.generation
                and rolling.template_sha(current["template"], pin.service) == pin.template_sha256
                and rolling.pinned_traffic(pin.service, current) == list(pin.traffic)
            )
            if not at_prior:
                need(
                    started is not None
                    and int(current["generation"]) == started["expected_generation"]
                    and rolling.template_sha(current["template"], pin.service)
                    == rolling.template_sha(target, pin.service)
                    and rolling.pinned_traffic(pin.service, current) == started["traffic"],
                    "unowned_or_drifted_phase",
                )
            else:
                need(
                    all(
                        rolling.short_revision(current[key], pin.service) == pin.prior_revision
                        for key in ("latestReadyRevision", "latestCreatedRevision")
                    ),
                    "prior_revision_changed",
                )
            for template in (prior, current["template"]):
                env = rolling.environment(template)
                need(
                    all(
                        env.get(name, {"value": "false"}).get("value") == "false"
                        for name in plan.disabled_flags()
                    ),
                    "paid_admission_changed",
                )
                candidate = rolling.template_sha(template, pin.service) != pin.template_sha256
                expected_pin = rolling.candidate_service_pin(plan, pin) if candidate else pin
                rolling.verify_policy_bindings(plan.policies, expected_pin, template)
                for secret in expected_pin.secrets:
                    need(
                        env.get(secret.environment_name)
                        == {
                            "name": secret.environment_name,
                            "valueSource": {
                                "secretKeyRef": {"secret": secret.secret, "version": secret.version}
                            },
                        }
                        and self._reference(secret.secret, secret.version) == secret.version,
                        "secret_changed",
                    )

    def _pin(self, name, source):
        current = self.service(name)
        prior = next(
            row["revision"]
            for row in rolling.pinned_traffic(name, current)
            if row.get("percent") == 100
        )
        need(
            all(
                rolling.short_revision(current[key], name) == prior
                for key in ("latestReadyRevision", "latestCreatedRevision")
            ),
            "settled_prior_required",
        )
        revision = self.revision(name, prior)
        keys = tuple(sorted(set(current["template"]) - {"revision"}))
        need(
            revision is not None
            and all(revision.get(key) == current["template"][key] for key in keys),
            "prior_template",
        )
        env, secrets = rolling.environment(current["template"]), []
        need(
            env.get("APP_RELEASE") == {"name": "APP_RELEASE", "value": source.release}
            and revision["containers"][0]["image"].endswith("@" + source.image_digest),
            "prior_image",
        )
        for environment_name, row in env.items():
            if "valueSource" in row:
                ref = row["valueSource"]["secretKeyRef"]
                need(
                    rolling.REF.fullmatch(ref["secret"] + ":" + ref["version"])
                    and self._reference(ref["secret"], ref["version"]) == ref["version"],
                    "numeric_secret",
                )
                secrets.append(rolling.SecretPin(environment_name, ref["secret"], ref["version"]))
        pin = rolling.ServicePin(
            name,
            current["uid"],
            int(current["generation"]),
            prior,
            source.release,
            rolling.template_sha(current["template"], name),
            rolling.static_sha(current),
            rolling.sha(self.iam(name)),
            rolling.sha({key: revision.get(key) for key in keys}),
            keys,
            tuple(rolling.pinned_traffic(name, current)),
            tuple(sorted(secrets, key=lambda row: row.environment_name)),
        )
        rolling._ready(current, pin)
        return pin

    def _reachable(self, pins, source):
        return tuple(
            app26.historical.reachable_pin(
                pin.service,
                row["revision"],
                self.revision(pin.service, row["revision"]),
                self.revision(pin.service, pin.prior_revision),
                prior_source=source,
            )
            for pin in pins
            for row in pin.traffic
        )

    def capture(
        self,
        source,
        prior,
        *,
        controller_ci_run_id,
        nonce,
        observer_secret,
        retired_migration_secret,
        workers_release,
        employer_cache_secret=None,
    ):
        source.validate()
        prior.validate()
        host, stamp = controller_source(self.root)
        # Validate independent source roots and provenance BEFORE any retained
        # credential payload, helper module, SQL or service capture is used.
        self._verify_sources(host, stamp, controller_ci_run_id, source, prior)
        pins = tuple(self._pin(name, prior) for name in rolling.release.SERVICES)
        refs = {row.environment_name: row.secret + ":" + row.version for row in pins[0].secrets}
        env = rolling.environment(self.service(pins[0].service)["template"])
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
        signature, liabilities = self._database(
            refs["DATABASE_URL"], observer_secret, retired_migration_secret
        )
        runtime = self.observer._cloud("iam", "service-accounts", "describe", rolling.RUNTIME)
        cache = (
            rolling.EmployerCacheBinding(employer_cache_secret)
            if employer_cache_secret is not None
            else None
        )
        plan = SuccessorPlan(
            source.release,
            source.image_digest,
            source.build_id,
            source.ci_run_id,
            nonce,
            runtime["uniqueId"],
            refs["DATABASE_URL"],
            observer_secret,
            retired_migration_secret,
            workers_release,
            signature,
            policies,
            pins,
            prior_source=prior,
            controller_release=host,
            controller_source_sha256=stamp,
            controller_ci_run_id=controller_ci_run_id,
            resources=self._resources(workers_release),
            protected_liabilities=liabilities,
            reachable_revisions=self._reachable(pins, prior),
            candidate_employer_cache=cache,
        )
        # Hash the pure target before validating/persisting a sendable plan.
        if cache is not None:
            pins = tuple(
                replace(
                    pin,
                    candidate_template_sha256=rolling.template_sha(
                        rolling.stage_body(plan, pin, self.service(pin.service))["template"],
                        pin.service,
                    ),
                )
                if pin.service == cache.service
                else pin
                for pin in pins
            )
            plan = replace(plan, services=pins)
        plan.validate()
        self.verify_global(plan)
        need(
            all(self.revision(pin.service, plan.revision(pin.service)) is None for pin in pins),
            "candidate_exists",
        )
        return plan

    def close(self):
        if self._run is not None:
            self._run.stock.session.close()


def execute(mode, plan, boundary, journal):
    need(
        type(plan) is SuccessorPlan
        and type(boundary) is NativeSuccessorBoundary
        and type(journal) is rolling.Journal
        and journal.plan_hash == rolling.sha(asdict(plan)),
        "exact_native_owner",
    )
    plan.validate()
    boundary.journal = journal
    # Observe an already acknowledged operation before asking a still-changing
    # Service for final traffic/Ready metadata. This grants no next mutation or
    # success marker; done operations still require the full fresh global proof.
    unresolved = []
    for pin in plan.services:
        for phase in ("rollback", "promote", "stage"):
            marker = phase + "." + pin.service
            started = journal.read(marker + ".started")
            if started is not None:
                ack = journal.read(marker + ".ack")
                if ack is not None and journal.read(marker + ".checked") is None:
                    unresolved.append((pin, phase, started, ack))
                break
    if unresolved:
        boundary._sources(plan)
        for pin, phase, started, ack in unresolved:
            if app26.historical.join_metadata(plan, boundary, pin, started, ack) is None:
                return {
                    "status": "same_native_operation_pending",
                    "phase": phase,
                    "service": pin.service,
                    "rolling_release_complete": False,
                    "native_mutations": 0,
                    "replay_authorized": False,
                }
    boundary.verify_global(plan)
    if (
        mode == "rollback"
        or mode in {"reconcile", "observe"}
        and journal.read(app26.historical.ROLLBACK_INTENT) is not None
    ):
        result = app26.historical.rollback_app(
            plan, boundary, journal, reconcile=mode != "rollback"
        )
    elif mode in {"advance", "reconcile"}:
        result = {"advance": rolling.advance, "reconcile": rolling.reconcile}[mode](
            plan, boundary, journal
        )
    else:
        need(mode in {"stage", "observe"}, "mode")
        with journal.locked():
            need(journal.read(app26.historical.ROLLBACK_INTENT) is None, "rollback_started")
            if mode == "stage":
                need(
                    not any(
                        journal.read("promote." + pin.service + ".started") for pin in plan.services
                    ),
                    "stage_passed",
                )
                for pin in plan.services:
                    if not rolling.action(plan, boundary, journal, pin, "stage"):
                        return {
                            "status": "same_native_operation_pending",
                            "replay_authorized": False,
                        }
            else:
                for pin in plan.services:
                    observed_phase = next(
                        (
                            p
                            for p in ("rollback", "promote", "stage")
                            if journal.read(p + "." + pin.service + ".started") is not None
                        ),
                        None,
                    )
                    if observed_phase is not None:
                        rolling.action(plan, boundary, journal, pin, observed_phase, reconcile=True)
            boundary.verify_global(plan)
            result = {
                "rolling_release_complete": all(
                    journal.read("promote." + p.service + ".checked") is not None
                    for p in plan.services
                )
            }
    return {
        key: value
        for key, value in result.items()
        if key not in {"status", "schema", "queue_admission"}
    } | {
        "status": "schema26_successor_" + mode,
        "schema": SCHEMA,
        "migrations": 0,
        "data_transfers": 0,
        "credential_changes": 0,
        "iam_changes": 0,
        "replay_authorized": False,
    }


def load(raw):
    def pairs(rows):
        need(len({name for name, _ in rows}) == len(rows), "duplicate_plan_key")
        return dict(rows)

    need(type(raw) is bytes and 0 < len(raw) <= 131072, "plan_size")
    value = json.loads(raw, object_pairs_hook=pairs)
    need(type(value) is dict and value.get("schema_contract") == KIND, "plan_kind")
    value["policies"] = rolling.PolicyPins(**value["policies"])
    need(value.get("candidate_policies") is None, "policy_changes_forbidden")
    value["prior_source"] = SourcePin(**value["prior_source"])
    value["resources"] = tuple(app26.ResourcePin(**row) for row in value["resources"])
    value["protected_liabilities"] = tuple(tuple(row) for row in value["protected_liabilities"])
    value["reachable_revisions"] = tuple(
        app26.ReachablePin(**row) for row in value["reachable_revisions"]
    )
    if value.get("candidate_employer_cache") is not None:
        value["candidate_employer_cache"] = rolling.EmployerCacheBinding(
            **value["candidate_employer_cache"]
        )
    pins = []
    for row in value["services"]:
        row["secrets"] = tuple(rolling.SecretPin(**secret) for secret in row["secrets"])
        row["revision_keys"], row["traffic"] = tuple(row["revision_keys"]), tuple(row["traffic"])
        pins.append(rolling.ServicePin(**row))
    value["services"] = tuple(pins)
    plan = SuccessorPlan(**value)
    plan.validate()
    return plan


def failure_projection(error):
    result = {"status": "schema26_successor_stopped_reconcile_only", "replay_authorized": False}
    if type(error) is rolling.RollingDenied and re.fullmatch(
        r"schema26_successor_[a-z0-9_]{1,96}", str(error)
    ):
        result["fixed_code"] = str(error)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("source", "capture", "stage", "advance", "observe", "reconcile", "rollback"),
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--target-root", type=Path)
    parser.add_argument("--prior-root", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    for name in (
        "release",
        "image-digest",
        "build-id",
        "prior-release",
        "prior-image-digest",
        "prior-build-id",
        "nonce",
        "observer-secret",
        "retired-migration-secret",
        "workers-release",
        "employer-cache-secret",
    ):
        parser.add_argument("--" + name)
    for name in ("ci-run-id", "prior-ci-run-id", "controller-ci-run-id"):
        parser.add_argument("--" + name, type=int)
    args = parser.parse_args()
    if args.mode == "source":
        head, stamp = controller_source(args.root)
        print(
            json.dumps(
                {
                    "status": "source_only",
                    "release": head,
                    "source_sha256": stamp,
                    "native_authority": False,
                    "schema": SCHEMA,
                }
            )
        )
        return
    need(
        args.target_root is not None and args.prior_root is not None and args.plan is not None,
        "paths",
    )
    boundary = NativeSuccessorBoundary(args.root, args.target_root, args.prior_root)
    try:
        if args.mode == "capture":
            plan = boundary.capture(
                SourcePin(args.release, args.image_digest, args.build_id, args.ci_run_id),
                SourcePin(
                    args.prior_release,
                    args.prior_image_digest,
                    args.prior_build_id,
                    args.prior_ci_run_id,
                ),
                controller_ci_run_id=args.controller_ci_run_id,
                nonce=args.nonce,
                observer_secret=args.observer_secret,
                retired_migration_secret=args.retired_migration_secret,
                workers_release=args.workers_release,
                employer_cache_secret=args.employer_cache_secret,
            )
            need(
                args.plan.is_absolute()
                and args.plan.parent.resolve() == args.plan.parent
                and args.plan.parent.stat().st_uid == os.getuid()
                and stat.S_IMODE(args.plan.parent.stat().st_mode) == 0o700,
                "private_plan",
            )
            descriptor = os.open(
                args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(rolling.canonical(asdict(plan)) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            print(json.dumps({"status": "schema26_successor_plan_captured", "native_mutations": 0}))
        else:
            need(args.journal is not None and type(args.plan_sha256) is str, "plan_custody")
            plan = load(app26.initial._read_pinned(args.plan, args.plan_sha256))
            print(
                json.dumps(execute(args.mode, plan, boundary, rolling.Journal(args.journal, plan)))
            )
    finally:
        boundary.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps(failure_projection(error)))
        raise SystemExit(1) from None
