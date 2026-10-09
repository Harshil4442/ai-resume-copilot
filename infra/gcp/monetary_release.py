"""Schema0013 release sequence with live observations and no assertion override.

The native adapter currently refuses external consumer/provider fencing: no
authenticated verifier for that scope exists in this project. Its sequencer and
bounded migration command are implemented; production execution remains unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import release_preflight as source_guard

PROJECT = "ai-resume-parser-482412"
REGION = "us-central1"
REPOSITORY = "Harshil4442/ai-resume-copilot"
SERVICES = ("ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-employer-worker")
QUEUES = ("hirewiz-analysis", "hirewiz-employer-search", "hirewiz-employer-ingestion", "hirewiz-employer-application")
CI_JOBS = {"backend", "frontend", "security-and-container", "browser-companion", "cold-browser-journey"}
CHAIN = (*source_guard.ALLOWED_CHAIN, "20261009_0010", "20261009_0011", "20261009_0012", "20261009_0013")
SAFE_ENV = {
    "APP_ENV": "production", "AUTO_DB_MIGRATE": "false",
    "OPTIONAL_AI_GENERATION_ENABLED": "false", "EMPLOYER_AUTO_SUBMIT_ENABLED": "false",
    "RAZORPAY_CHECKOUT_ENABLED": "false",
    "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED": "false",
    "EMPLOYER_SEARCH_CREDITS_PER_JOB": "1", "EMPLOYER_APPLY_CREDITS_PER_JOB": "20",
}
NATIVE_ENV = ("NATIVE_TEX_IMAGE", "NATIVE_TEX_COMPILER_URL", "NATIVE_TEX_IMAGE_DIGEST", "NATIVE_TEX_ISOLATION_POLICY_SHA256")
MAX_JSON = 131072
ERASABLE_TABLES = {
    "users", "user_profiles", "resumes", "job_matches", "skill_coverage",
    "opportunities", "application_events", "resume_versions", "evidence_items",
    "reminders", "opportunity_contacts", "career_memory_entries", "notification_outbox",
    "model_call_events", "dispatch_outbox", "analysis_request_keys", "usage_events",
    "analysis_runs", "candidate_password_accounts", "employer_application_approvals",
    "employer_application_attempts", "employer_applications", "employer_application_batches",
    "sealed_application_artifacts", "employer_job_deliveries", "employer_searches",
    "employer_artifact_uploads",
}



class ReleaseDenied(RuntimeError):
    """Fixed nonsecret reason only; never surface vendor stderr."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseDenied("duplicate_input_key")
        result[key] = value
    return result


def _json(raw: bytes) -> Any:
    if len(raw) > MAX_JSON:
        raise ReleaseDenied("observation_size_bound")
    return json.loads(raw, object_pairs_hook=_unique)


@dataclass(frozen=True)
class Plan:
    release: str
    image_digest: str
    build_id: str
    ci_run_id: int
    runtime_secret: str
    migration_secret: str
    expense_secret: str
    expense_sha256: str
    runtime_service_account: str
    migration_service_account: str
    backup_uri: str
    inventory_path: Path
    inventory_sha256: str

    @property
    def image(self) -> str:
        return f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/hirewiz@{self.image_digest}"

    def validate(self) -> None:
        if (
            not re.fullmatch(r"[a-f0-9]{40}", self.release)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_digest)
            or not re.fullmatch(r"[a-f0-9-]{36}", self.build_id)
            or type(self.ci_run_id) is not int or self.ci_run_id <= 0
            or any(not re.fullmatch(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}", ref)
                   for ref in (self.runtime_secret, self.migration_secret, self.expense_secret))
            or self.runtime_secret.split(":")[0] == self.migration_secret.split(":")[0]
            or not re.fullmatch(r"[a-f0-9]{64}", self.expense_sha256)
            or not re.fullmatch(r"[a-f0-9]{64}", self.inventory_sha256)
            or self.runtime_service_account == self.migration_service_account
            or any(not re.fullmatch(r"[a-z][a-z0-9-]{4,28}@" + PROJECT + r"\.iam\.gserviceaccount\.com", value)
                   for value in (self.runtime_service_account, self.migration_service_account))
            or not re.fullmatch(r"gs://" + PROJECT + r"-hirewiz-application-artifacts/releases/[a-zA-Z0-9_.-]+\.dump", self.backup_uri)
        ):
            raise ReleaseDenied("release_plan_identity_invalid")


@dataclass(frozen=True)
class Fence:
    observed_at: datetime
    retired_since: datetime
    # These values may ONLY come from an authenticated native verifier. No CLI
    # certificate file, operator bool, manifest field or preflight report is read.
    complete_external_consumers: bool
    old_provider_credentials_revoked: bool
    old_cloud_writers_retired: bool


class Boundary(Protocol):
    def verify_identity_and_policy(self, plan: Plan) -> None: ...
    def verify_admission_closed(self) -> None: ...
    def verify_fences(self, plan: Plan) -> Fence: ...
    def verify_database(self, plan: Plan, schema: str) -> None: ...
    def create_backup(self, plan: Plan, fence: Fence) -> None: ...
    def verify_backup(self, plan: Plan, fence: Fence) -> None: ...
    def migrate(self, plan: Plan, expected: str, target: str) -> None: ...
    def stage(self, plan: Plan, service: str) -> str: ...
    def verify_revision(self, plan: Plan, service: str, revision: str) -> None: ...
    def promote(self, service: str, revision: str) -> None: ...
    def verify_serving(self, plan: Plan, service: str, revision: str) -> None: ...


def _fresh_fence(boundary: Boundary, plan: Plan, clock: Callable[[], datetime]) -> Fence:
    fence = boundary.verify_fences(plan)
    now = clock()
    if (
        fence.observed_at.tzinfo is None or fence.retired_since.tzinfo is None
        or fence.retired_since > fence.observed_at or not now - timedelta(seconds=30) <= fence.observed_at <= now
        or not fence.complete_external_consumers or not fence.old_provider_credentials_revoked
        or not fence.old_cloud_writers_retired
    ):
        raise ReleaseDenied("external_writer_or_provider_fence_not_verified")
    return fence


def run_release(
    plan: Plan, boundary: Boundary, *, execute: bool,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Strict forward sequence. A lost mutation result is terminal, never retried.

    Admission remains closed at success: release enabling/resuming a queue, AI,
    native lifecycle, compiler or employer submission needs its separate gate.
    """
    plan.validate()
    boundary.verify_identity_and_policy(plan)
    boundary.verify_admission_closed()
    fence = _fresh_fence(boundary, plan, clock)
    boundary.verify_database(plan, "20261008_0009")
    if not execute:
        return {"release": plan.release, "schema": "20261008_0009", "mutation_count": 0,
                "sequence_admitted": False, "post_fence_backup_creation_required": True, "production_released": False}
    boundary.create_backup(plan, fence)
    boundary.verify_backup(plan, fence)
    for expected, target in zip(CHAIN[-5:-1], CHAIN[-4:], strict=True):
        boundary.verify_identity_and_policy(plan)
        boundary.verify_admission_closed()
        _fresh_fence(boundary, plan, clock)
        boundary.verify_database(plan, expected)
        boundary.migrate(plan, expected, target)
        boundary.verify_database(plan, target)
    revisions: dict[str, str] = {}
    for service in SERVICES:
        boundary.verify_identity_and_policy(plan)
        boundary.verify_admission_closed()
        _fresh_fence(boundary, plan, clock)
        revisions[service] = boundary.stage(plan, service)
        boundary.verify_revision(plan, service, revisions[service])
    # Verify the entire joined set before promoting any participant; later
    # drift/fence loss stops the release rather than reopening old credentials.
    for service, revision in revisions.items():
        boundary.verify_revision(plan, service, revision)
    for service in (*SERVICES[1:], SERVICES[0]):
        boundary.verify_identity_and_policy(plan)
        boundary.verify_admission_closed()
        _fresh_fence(boundary, plan, clock)
        boundary.verify_database(plan, CHAIN[-1])
        boundary.promote(service, revisions[service])
        boundary.verify_serving(plan, service, revisions[service])
    boundary.verify_database(plan, CHAIN[-1])
    _fresh_fence(boundary, plan, clock)
    return {"release": plan.release, "image_digest": plan.image_digest, "schema": CHAIN[-1],
            "joined_revisions": revisions, "production_released": True,
            "queue_admission": "closed", "checkout_enabled": False, "optional_generation": False,
            "automatic_submission": False, "native_lifecycle": False,
            "goal_complete": False}


class NativeBoundary:
    """Only this adapter is reachable from the CLI; no transport plugin loading."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._owned_backup: dict[str, Any] | None = None

    def _command(self, executable: str, *args: str) -> bytes:
        try:
            result = subprocess.run([executable, *args], cwd=self.root, check=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=600, env={**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GH_HOST": "github.com"})
            if len(result.stdout) > MAX_JSON:
                raise ReleaseDenied("observation_size_bound")
            return result.stdout
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise ReleaseDenied("cloud_operation_unavailable_or_outcome_unknown") from None

    def _cloud(self, *args: str) -> Any:
        return _json(self._command("gcloud", *args, "--project", PROJECT, "--format=json", "--quiet"))

    def _module(self, relative: str, name: str) -> Any:
        spec = importlib.util.spec_from_file_location(name, self.root / relative)
        if spec is None or spec.loader is None:
            raise ReleaseDenied("reviewed_source_module_unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def _secret(self, reference: str) -> str:
        name, version = reference.split(":")
        metadata = self._cloud("secrets", "versions", "describe", version, "--secret", name)
        if metadata.get("state") != "ENABLED" or metadata.get("name", "").split("/")[-1] != version:
            raise ReleaseDenied("exact_secret_version_not_enabled")
        return self._command("gcloud", "secrets", "versions", "access", version, "--secret", name,
                             "--project", PROJECT, "--quiet").decode()

    def verify_identity_and_policy(self, plan: Plan) -> None:
        tracked = source_guard.checked_files(self.root, plan.release)
        if source_guard.migration_chain(self.root, tracked) != CHAIN:
            raise ReleaseDenied("reviewed_schema13_chain_required")
        verify_ci_checkout_contract(self.root)
        ci = _json(self._command("gh", "run", "view", str(plan.ci_run_id), "--repo", REPOSITORY,
                                 "--json", "headSha,status,conclusion,jobs,workflowName"))
        workflow = _json(self._command("gh", "api", f"repos/{REPOSITORY}/actions/runs/{plan.ci_run_id}"))
        jobs = ci.get("jobs", [])
        if (workflow.get("path") != ".github/workflows/ci.yml"
                or workflow.get("head_sha") != plan.release
                or workflow.get("event") not in {"push", "pull_request"}
                or workflow.get("repository", {}).get("full_name") != REPOSITORY
                or len(jobs) != len(CI_JOBS)
                or ci.get("headSha") != plan.release or ci.get("workflowName") != "CI"
                or ci.get("status") != "completed" or ci.get("conclusion") != "success"
                or {item.get("name") for item in jobs} != CI_JOBS
                or any(item.get("status") != "completed" or item.get("conclusion") != "success" for item in jobs)):
            raise ReleaseDenied("exact_release_five_ci_jobs_not_successful")
        build = self._cloud("builds", "describe", plan.build_id, "--region", REGION)
        # Only a native Cloud Build Git-source attestation is supported. Uploaded
        # local archives, labels or caller source-hash files are not provenance.
        provenance = build.get("sourceProvenance", {}).get("resolvedGitSource", {})
        tag = plan.image.split("@")[0] + ":" + plan.release
        steps = build.get("steps", [])
        images = build.get("results", {}).get("images", [])
        expected_steps = (["build", "--tag", tag, "."], ["push", tag])
        if steps and steps[0].get("args") == ["build", "-t", tag, "."]:
            expected_steps = (["build", "-t", tag, "."], ["push", tag])
        elif steps and steps[0].get("args") == ["build", "--tag=" + tag, "."]:
            expected_steps = (["build", "--tag=" + tag, "."], ["push", tag])
        if (build.get("status") != "SUCCESS" or build.get("projectId") != PROJECT
                or build.get("id") != plan.build_id
                or provenance.get("revision") != plan.release
                or provenance.get("url") != f"https://github.com/{REPOSITORY}.git"
                or provenance.get("dir", ".") not in {"", "."}
                or len(steps) != 2
                or any(step.get("name") != "gcr.io/cloud-builders/docker"
                       or step.get("args") != arguments or step.get("env")
                       or step.get("secretEnv") or step.get("entrypoint") or step.get("script")
                       or step.get("volumes") or step.get("dir", ".") not in {"", "."}
                       for step, arguments in zip(steps, expected_steps, strict=True))
                or build.get("options", {}).get("env") or build.get("options", {}).get("secretEnv")
                or build.get("options", {}).get("volumes") or build.get("options", {}).get("automapSubstitutions")
                or len(images) != 1 or images[0].get("name") != tag
                or images[0].get("digest") != plan.image_digest
                or build.get("availableSecrets") or build.get("secrets")):
            raise ReleaseDenied("native_exact_source_image_provenance_not_verified")
        raw = self._secret(plan.expense_secret)
        if hashlib.sha256(raw.encode()).hexdigest() != plan.expense_sha256:
            raise ReleaseDenied("approved_expense_policy_secret_hash_mismatch")
        # Use the existing authoritative financial guard, including catalog and
        # worst-workload arithmetic. No independent permissive price calculator.
        sys.path.insert(0, str(self.root / "backend"))
        from app.billing.cost_policy import service_prices
        old = {name: os.environ.get(name) for name in ("APP_ENV", "HIREWIZ_EXPENSE_POLICY_JSON")}
        try:
            os.environ.update(APP_ENV="production", HIREWIZ_EXPENSE_POLICY_JSON=raw)
            rates = service_prices()
            if (rates["search_credits_per_job"], rates["apply_credits_per_job"]) != (1, 20):
                raise ReleaseDenied("approved_policy_requires_search1_apply20")
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        for identity in (plan.runtime_service_account, plan.migration_service_account):
            account = self._cloud("iam", "service-accounts", "describe", identity)
            if account.get("email") != identity or account.get("disabled") is True:
                raise ReleaseDenied("separate_service_account_unavailable")
        source_guard.checked_files(self.root, plan.release)

    def verify_admission_closed(self) -> None:
        for queue in QUEUES:
            observed = self._cloud("tasks", "queues", "describe", queue, "--location", REGION)
            tasks = self._cloud("tasks", "list", "--queue", queue, "--location", REGION, "--limit", "257")
            if observed.get("state") != "PAUSED" or tasks != []:
                raise ReleaseDenied("queue_not_paused_and_drained")
        schedules = self._cloud("scheduler", "jobs", "list", "--location", REGION, "--limit", "257")
        if not isinstance(schedules, list) or len(schedules) > 256 or any(item.get("state") != "PAUSED" for item in schedules):
            raise ReleaseDenied("scheduler_admission_not_closed")
        executions = self._cloud("run", "jobs", "executions", "list", "--region", REGION, "--limit", "257")
        if not isinstance(executions, list) or len(executions) > 256 or any(not item.get("status", {}).get("completionTime") for item in executions):
            raise ReleaseDenied("job_execution_inventory_unresolved")

    def verify_fences(self, plan: Plan) -> Fence:
        # Secret Manager DISABLED, Cloud Run zero traffic, queue PAUSED and a SQL
        # snapshot cannot revoke already-loaded provider credentials or prove all
        # external consumers. There is no trusted native verifier in this repo.
        # Never read inventory.provider_fencing_verified or accept an override.
        raise ReleaseDenied("native_external_consumer_and_provider_fence_verifier_missing")

    def _database_context(self, plan: Plan, schema: str) -> tuple[Any, Any, dict[str, str], tuple[str, ...]]:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        module = self._module("backend/scripts/monetary_cutover_preflight.py", "hirewiz_monetary_observer")
        inventory, _ = module.load_inventory(plan.inventory_path, plan.inventory_sha256, datetime.now(UTC))
        inventory = inventory.model_copy(update={"expected_database_schema": schema, "candidate_schema": CHAIN[-1]})
        if inventory.candidate_commit != plan.release or inventory.candidate_image != plan.image_digest:
            raise ReleaseDenied("database_identity_inventory_release_mismatch")
        roles = {str(alias): os.environ[name] for alias, name in inventory.role_env.items()}
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) for role in roles.values()):
            raise ReleaseDenied("database_role_cannot_enter_bounded_cloud_env")
        if (len(set(inventory.database_operator_role_env)) != len(inventory.database_operator_role_env)
                or any(not re.fullmatch(r"HIREWIZ_CUTOVER_OPERATOR_[A-Z_]{1,32}", name)
                       for name in inventory.database_operator_role_env)):
            raise ReleaseDenied("operator_role_alias_invalid")
        operators = tuple(os.environ[name] for name in inventory.database_operator_role_env)
        if set(operators) & set(roles.values()):
            raise ReleaseDenied("operator_role_class_overlap")
        engine = create_engine(os.environ["HIREWIZ_CUTOVER_DATABASE_URL"], poolclass=NullPool,
                               hide_parameters=True, connect_args={"connect_timeout": 5})
        return module, (engine, inventory), roles, operators

    def verify_database(self, plan: Plan, schema: str) -> None:
        module, (engine, inventory), roles, operators = self._database_context(plan, schema)
        try:
            observed = module.observe(engine, inventory, roles, operators)
            result = module.evaluate(inventory, plan.inventory_sha256, observed, datetime.now(UTC))
            # Its booleans are still unverified assertions. Only fresh observed
            # SQL values and the separate native fences authorize this sequence.
            reasons = result["database_gate_reasons"]
            if reasons:
                raise ReleaseDenied("database_observation_gate_refused")
            runtime, migration = (observed["roles"][key] for key in ("replacement_runtime", "replacement_migration"))
            if (not runtime["login_enabled"] or runtime["schema_create"]
                    or not migration["login_enabled"] or not migration["schema_create"]
                    or migration["elevated"] or migration["elevated_role_membership"]
                    or migration["membership_administration"]):
                raise ReleaseDenied("replacement_runtime_or_migration_privileges_unready")
            from sqlalchemy import text
            with engine.connect() as connection:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                verify_runtime_permissions(connection, inventory.database_namespace, roles["replacement_runtime"], roles["replacement_migration"])
            # Pinned numeric secrets must actually connect as distinct observed
            # roles into this database; a DSN username is not credential proof.
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool
            for alias, ref in (("replacement_runtime", plan.runtime_secret), ("replacement_migration", plan.migration_secret)):
                credential_engine = create_engine(self._secret(ref), poolclass=NullPool, hide_parameters=True,
                                                  connect_args={"connect_timeout": 5})
                try:
                    with credential_engine.connect() as connection:
                        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                        connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                        name = connection.execute(text("SELECT current_user")).scalar_one()
                        identifier = connection.execute(text("SELECT system_identifier::text FROM pg_control_system()")).scalar_one()
                        oid = connection.execute(text("SELECT oid FROM pg_database WHERE datname=current_database()")).scalar_one()
                        if name != roles[alias] or hashlib.sha256(str(identifier).encode()).hexdigest() != observed["system_identifier_sha256"] or oid != observed["database_oid"]:
                            raise ReleaseDenied("exact_secret_role_database_identity_mismatch")
                finally:
                    credential_engine.dispose()
        finally:
            engine.dispose()

    @contextmanager
    def _backup_snapshot(self, plan: Plan) -> Iterator[tuple[str, Any]]:
        from sqlalchemy import text
        # Ambient libpq service/hostaddr/options cannot redirect the verified
        # exporter or the separate pg_dump connection.
        inherited = {key: value for key, value in os.environ.items() if key.startswith("PG")}
        for key in inherited:
            os.environ.pop(key)
        engine = None
        try:
            _, (engine, inventory), _, _ = self._database_context(plan, "20261008_0009")
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = '610000ms'")
                identifier = connection.execute(text("SELECT system_identifier::text FROM pg_control_system()")).scalar_one()
                oid = connection.execute(text("SELECT oid FROM pg_database WHERE datname=current_database()")).scalar_one()
                quoted = connection.dialect.identifier_preparer.quote(inventory.database_namespace)
                versions = list(connection.execute(text(f"SELECT version_num FROM {quoted}.alembic_version LIMIT 3")).scalars())
                if (hashlib.sha256(str(identifier).encode()).hexdigest() != inventory.expected_system_identifier_sha256
                        or oid != inventory.expected_database_oid or versions != ["20261008_0009"]):
                    raise ReleaseDenied("backup_exporter_database_or_schema_identity_mismatch")
                snapshot = connection.execute(text("SELECT pg_export_snapshot()")).scalar_one()
                if not isinstance(snapshot, str) or not re.fullmatch(r"[a-fA-F0-9-]{1,96}", snapshot):
                    raise ReleaseDenied("backup_exported_snapshot_identity_invalid")
                yield snapshot, inventory
        finally:
            if engine is not None:
                engine.dispose()
            os.environ.update(inherited)

    def create_backup(self, plan: Plan, fence: Fence) -> None:
        # The authority is this observed native creation sequence, never custom
        # metadata supplied by a caller. A separate process cannot load a receipt
        # file and turn it into release permission.
        import tempfile

        from sqlalchemy.engine import make_url
        _, (engine, inventory), _, _ = self._database_context(plan, "20261008_0009")
        engine.dispose()
        self.verify_database(plan, "20261008_0009")
        fresh = _fresh_fence(self, plan, lambda: datetime.now(UTC))
        started = datetime.now(UTC)
        if fresh.retired_since > started or fresh.retired_since != fence.retired_since:
            raise ReleaseDenied("backup_writer_cut_changed")
        bucket_uri = plan.backup_uri.split("/releases/")[0]
        bucket = self._cloud("storage", "buckets", "describe", bucket_uri)
        if bucket.get("uniform_bucket_level_access") is not True or bucket.get("public_access_prevention") != "enforced":
            raise ReleaseDenied("protected_backup_bucket_privacy_controls_not_verified")
        bucket_policy = self._cloud("storage", "buckets", "get-iam-policy", bucket_uri)
        if any(member in {"allUsers", "allAuthenticatedUsers"}
               for binding in bucket_policy.get("bindings", []) for member in binding.get("members", [])):
            raise ReleaseDenied("protected_backup_bucket_is_public")
        url = make_url(os.environ["HIREWIZ_CUTOVER_DATABASE_URL"])
        if not url.host or not url.username or not url.database or set(url.query) - {"sslmode", "sslrootcert"}:
            raise ReleaseDenied("backup_connection_parameters_unsupported")
        environment = {**{k: v for k, v in os.environ.items() if not k.startswith("PG")}, "PGHOST": url.host, "PGPORT": str(url.port or 5432),
                       "PGUSER": url.username, "PGPASSWORD": url.password or "",
                       "PGDATABASE": url.database, "PGSSLMODE": str(url.query.get("sslmode", "require")),
                       "PGCONNECT_TIMEOUT": "5", "PGAPPNAME": "hirewiz_post_fence_backup"}
        if "sslrootcert" in url.query:
            environment["PGSSLROOTCERT"] = str(url.query["sslrootcert"])
        with tempfile.TemporaryDirectory(prefix="hirewiz-post-fence-backup-") as directory:
            archive = Path(directory) / "backup.dump"
            try:
                with self._backup_snapshot(plan) as (snapshot, inventory):
                    subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                                    "--snapshot", snapshot, "--file", str(archive)],
                                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=environment, timeout=600)
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                raise ReleaseDenied("post_fence_backup_creation_unavailable") from None
            archive.chmod(0o600)
            self._command("pg_restore", "--list", str(archive))
            with archive.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            size = archive.stat().st_size
            if size < 100:
                raise ReleaseDenied("post_fence_backup_archive_invalid")
            self.verify_database(plan, "20261008_0009")
            fresh = _fresh_fence(self, plan, lambda: datetime.now(UTC))
            if fresh.retired_since != fence.retired_since:
                raise ReleaseDenied("backup_writer_cut_changed")
            until = (datetime.now(UTC) + timedelta(days=7)).isoformat()
            metadata = f"format=postgres-custom,release={plan.release},sha256={digest}"
            self._command("gcloud", "storage", "cp", str(archive), plan.backup_uri,
                          "--if-generation-match", "0", "--retention-mode", "Locked", "--retain-until", until,
                          "--custom-metadata", metadata, "--project", PROJECT, "--quiet")
            observed = self._cloud("storage", "objects", "describe", plan.backup_uri)
            generation = str(observed.get("generation", ""))
            if not re.fullmatch(r"[1-9][0-9]{0,20}", generation):
                raise ReleaseDenied("backup_upload_generation_not_observed")
            self._owned_backup = {"uri": plan.backup_uri, "generation": generation,
                                  "sha256": digest, "size": size, "started": started,
                                  "retired_since": fence.retired_since,
                                  "cluster": inventory.expected_system_identifier_sha256,
                                  "database_oid": inventory.expected_database_oid}

    def verify_backup(self, plan: Plan, fence: Fence) -> None:
        import tempfile
        receipt = self._owned_backup
        if receipt is None or receipt["uri"] != plan.backup_uri or receipt["retired_since"] != fence.retired_since:
            raise ReleaseDenied("native_owned_post_fence_backup_creation_missing")
        uri = plan.backup_uri + "#" + receipt["generation"]
        backup = self._cloud("storage", "objects", "describe", uri)
        created = datetime.fromisoformat(backup["creation_time"].replace("Z", "+00:00"))
        retention = backup.get("retention", {})
        until = datetime.fromisoformat(retention.get("retain_until_time", retention.get("retainUntilTime", "")).replace("Z", "+00:00"))
        if (str(backup.get("generation")) != receipt["generation"]
                or not receipt["started"] <= created <= datetime.now(UTC)
                or datetime.now(UTC) - created > timedelta(minutes=60)
                or retention.get("mode") != "Locked" or until < datetime.now(UTC) + timedelta(days=6)
                or int(backup.get("size", 0)) != receipt["size"]):
            raise ReleaseDenied("fresh_post_fence_protected_backup_not_verified")
        with tempfile.TemporaryDirectory(prefix="hirewiz-release-backup-") as directory:
            archive = Path(directory) / "backup.dump"
            self._command("gcloud", "storage", "cp", uri, str(archive), "--project", PROJECT, "--quiet")
            archive.chmod(0o600)
            with archive.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if archive.stat().st_size != receipt["size"] or digest != receipt["sha256"]:
                raise ReleaseDenied("backup_bytes_hash_or_size_mismatch")
            self._command("pg_restore", "--list", str(archive))
        self.verify_database(plan, "20261008_0009")
        fresh = _fresh_fence(self, plan, lambda: datetime.now(UTC))
        if fresh.retired_since != receipt["retired_since"]:
            raise ReleaseDenied("backup_writer_cut_changed")

    def migrate(self, plan: Plan, expected: str, target: str) -> None:
        _, (engine, inventory), roles, _ = self._database_context(plan, expected)
        engine.dispose()
        job = "hirewiz-monetary-" + plan.release[:12] + "-" + target[-4:]
        env = {**SAFE_ENV, "APP_RELEASE": plan.release, "SERVICE_ROLE": "migration",
               "DB_POOL_SIZE": "1", "DB_MAX_OVERFLOW": "0",
               "HIREWIZ_CUTOVER_CLUSTER_SHA256": inventory.expected_system_identifier_sha256,
               "HIREWIZ_CUTOVER_DATABASE_OID": str(inventory.expected_database_oid),
               "HIREWIZ_CUTOVER_NAMESPACE": inventory.database_namespace,
               "HIREWIZ_CUTOVER_MIGRATION_ROLE": roles["replacement_migration"]}
        self._cloud("run", "jobs", "deploy", job, "--region", REGION, "--image", plan.image,
                    "--service-account", plan.migration_service_account, "--tasks", "1", "--parallelism", "1",
                    "--max-retries", "0", "--task-timeout", "360s", "--cpu", "1", "--memory", "1Gi",
                    "--set-env-vars", "^|^" + "|".join(f"{k}={v}" for k, v in env.items()),
                    "--set-secrets", "DATABASE_URL=" + plan.migration_secret,
                    "--command", "python", "--args", f"scripts/monetary_schema_step.py,--expected,{expected},--target,{target}")
        # Never execute a second time after timeout, lost acknowledgement or an
        # unsuccessful execution. Native execution status is reconciled outside
        # this command before any operator starts a fresh reviewed release.
        execution = self._cloud("run", "jobs", "execute", job, "--region", REGION, "--wait")
        status = execution.get("status", {})
        if status.get("succeededCount") != 1 or not status.get("completionTime") or status.get("failedCount", 0):
            raise ReleaseDenied("migration_execution_not_confirmed_reconcile_only")

    def stage(self, plan: Plan, service: str) -> str:
        env = {**SAFE_ENV, "APP_RELEASE": plan.release,
               "SERVICE_ROLE": "api" if service == SERVICES[0] else "worker"}
        if service != SERVICES[0]:
            env["WORKER_LABEL"] = "analysis-worker" if service == SERVICES[1] else "employer-worker"
            env["WORKER_ALLOWED_TOPICS"] = ("analysis.run" if service == SERVICES[1]
                                           else "employer.search,employer.refresh,employer.apply,employer.artifact-delete")
        auth_args = () if service == SERVICES[0] else ("--no-allow-unauthenticated",)
        staged = self._cloud("run", "deploy", service, *auth_args, "--region", REGION, "--image", plan.image,
                             "--service-account", plan.runtime_service_account, "--no-traffic", "--tag", "monetary-candidate",
                             "--command", "", "--args", "", "--remove-env-vars", ",".join(NATIVE_ENV),
                             "--update-env-vars", "^|^" + "|".join(f"{k}={v}" for k, v in env.items()),
                             "--update-secrets", f"DATABASE_URL={plan.runtime_secret},HIREWIZ_EXPENSE_POLICY_JSON={plan.expense_secret}")
        revision = staged.get("status", {}).get("latestCreatedRevisionName", "")
        if not re.fullmatch(re.escape(service) + r"-[a-z0-9-]+", revision):
            raise ReleaseDenied("staged_revision_identity_missing")
        return str(revision)

    def verify_revision(self, plan: Plan, service: str, revision: str) -> None:
        observed = self._cloud("run", "revisions", "describe", revision, "--region", REGION)
        containers = observed.get("spec", {}).get("containers", [])
        if len(containers) != 1:
            raise ReleaseDenied("joined_revision_container_shape_invalid")
        container = containers[0]
        environment = container.get("env", [])
        env = {item["name"]: item for item in environment}
        expected_env = {**SAFE_ENV, "APP_RELEASE": plan.release,
                        "SERVICE_ROLE": "api" if service == SERVICES[0] else "worker"}
        if service != SERVICES[0]:
            expected_env["WORKER_LABEL"] = "analysis-worker" if service == SERVICES[1] else "employer-worker"
            expected_env["WORKER_ALLOWED_TOPICS"] = ("analysis.run" if service == SERVICES[1]
                                                    else "employer.search,employer.refresh,employer.apply,employer.artifact-delete")
        if (observed.get("metadata", {}).get("labels", {}).get("serving.knative.dev/service") != service
                or observed.get("status", {}).get("imageDigest") != plan.image
                or observed.get("spec", {}).get("serviceAccountName") != plan.runtime_service_account
                or len(env) != len(environment) or container.get("command") or container.get("args")
                or any(env.get(name, {}).get("value") != value for name, value in expected_env.items())
                or any(env.get(name) for name in NATIVE_ENV)
                or env.get("DATABASE_URL", {}).get("valueFrom", {}).get("secretKeyRef") != dict(zip(("name", "key"), plan.runtime_secret.split(":"), strict=True))
                or env.get("HIREWIZ_EXPENSE_POLICY_JSON", {}).get("valueFrom", {}).get("secretKeyRef") != dict(zip(("name", "key"), plan.expense_secret.split(":"), strict=True))
                or not any(condition.get("type") == "Ready" and condition.get("status") == "True" for condition in observed.get("status", {}).get("conditions", []))):
            raise ReleaseDenied("joined_revision_identity_or_safety_mismatch")
        if service != SERVICES[0]:
            service_metadata = self._cloud("run", "services", "describe", service, "--region", REGION)
            if service_metadata.get("metadata", {}).get("annotations", {}).get("run.googleapis.com/invoker-iam-disabled", "false") != "false":
                raise ReleaseDenied("private_worker_iam_checks_disabled")
            project_policy = self._cloud("projects", "get-iam-policy", PROJECT)
            if any((binding.get("role") == "roles/run.invoker" and binding.get("condition"))
                   or any(member in {"allUsers", "allAuthenticatedUsers"} for member in binding.get("members", []))
                   for binding in project_policy.get("bindings", [])):
                raise ReleaseDenied("project_invoker_policy_unresolved_or_public")
            policy = self._cloud("run", "services", "get-iam-policy", service, "--region", REGION)
            invokers = {member for binding in policy.get("bindings", []) if binding.get("role") == "roles/run.invoker" for member in binding.get("members", [])}
            expected_invokers = {f"serviceAccount:hirewiz-tasks@{PROJECT}.iam.gserviceaccount.com"}
            if service == SERVICES[1]:
                expected_invokers.add(f"serviceAccount:hirewiz-scheduler@{PROJECT}.iam.gserviceaccount.com")
            if invokers != expected_invokers or any(binding.get("role") == "roles/run.invoker" and binding.get("condition") for binding in policy.get("bindings", [])):
                raise ReleaseDenied("private_worker_invoker_policy_mismatch")
        self._verify_http_health(plan, service, revision)

    def _verify_http_health(self, plan: Plan, service: str, revision: str) -> None:
        import urllib.request
        from urllib.parse import urlsplit
        observed = self._cloud("run", "services", "describe", service, "--region", REGION)
        tagged = [item for item in observed.get("status", {}).get("traffic", [])
                  if item.get("tag") == "monetary-candidate" and item.get("revisionName") == revision]
        if len(tagged) != 1:
            raise ReleaseDenied("exact_candidate_health_destination_missing")
        url = tagged[0].get("url", "")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".run.app")
                or parsed.username or parsed.password or parsed.port not in {None, 443}
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ReleaseDenied("exact_candidate_health_destination_invalid")
        audience = observed.get("status", {}).get("url", "")
        audience_parts = urlsplit(audience)
        if (audience_parts.scheme != "https" or not audience_parts.hostname
                or not audience_parts.hostname.endswith(".run.app") or audience_parts.username
                or audience_parts.password or audience_parts.port not in {None, 443}
                or audience_parts.path not in {"", "/"} or audience_parts.query or audience_parts.fragment):
            raise ReleaseDenied("exact_candidate_health_audience_invalid")
        # Cloud Run requires the service URL audience even for traffic-tag URLs.
        token = self._command("gcloud", "auth", "print-identity-token",
                              "--impersonate-service-account", f"hirewiz-tasks@{PROJECT}.iam.gserviceaccount.com",
                              "--audiences", audience, "--quiet").decode().strip()
        request = urllib.request.Request(url.rstrip("/") + "/api/health",
                                         headers={"Authorization": "Bearer " + token})
        # Redirects are refused: a token cannot escape to an unobserved host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            health = _json(response.read(MAX_JSON + 1))
        if (health.get("ok") is not True or health.get("release") != plan.release
                or (service == SERVICES[0] and health.get("revision") != revision)
                or (service != SERVICES[0] and health.get("role") != ("analysis-worker" if service == SERVICES[1] else "employer-worker"))):
            raise ReleaseDenied("exact_candidate_http_health_identity_mismatch")

    def promote(self, service: str, revision: str) -> None:
        self._cloud("run", "services", "update-traffic", service, "--region", REGION, "--to-revisions", revision + "=100")

    def verify_serving(self, plan: Plan, service: str, revision: str) -> None:
        self.verify_revision(plan, service, revision)
        observed = self._cloud("run", "services", "describe", service, "--region", REGION)
        active = [item for item in observed.get("status", {}).get("traffic", []) if item.get("percent", 0)]
        if len(active) != 1 or active[0].get("revisionName") != revision or active[0].get("percent") != 100:
            raise ReleaseDenied("exact_joined_serving_revision_not_observed")


def verify_ci_checkout_contract(root: Path) -> None:
    import yaml  # type: ignore[import-untyped]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_bytes())
    jobs = workflow.get("jobs", {})
    if set(jobs) != CI_JOBS:
        raise ReleaseDenied("reviewed_ci_checkout_contract_mismatch")
    for job in jobs.values():
        checkouts = [step for step in job.get("steps", []) if str(step.get("uses", "")).startswith("actions/checkout@")]
        if len(checkouts) != 1 or checkouts[0].get("with", {}).get("ref") != "${{ github.event.pull_request.head.sha || github.sha }}":
            raise ReleaseDenied("reviewed_ci_checkout_contract_mismatch")


def verify_runtime_permissions(connection: Any, namespace: str, runtime: str, migration: str) -> None:
    from sqlalchemy import text
    relations = connection.execute(text(
        "SELECT c.oid, c.relname, c.relkind, "
        "has_table_privilege(:runtime,c.oid,'SELECT') AS can_select, "
        "has_table_privilege(:runtime,c.oid,'INSERT') AS can_insert, "
        "has_table_privilege(:runtime,c.oid,'UPDATE') AS can_update, "
        "has_table_privilege(:runtime,c.oid,'DELETE') AS can_delete, "
        "pg_has_role(:migration,c.relowner,'USAGE') AS migration_owns "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=:namespace AND c.relkind IN ('r','p') ORDER BY c.oid LIMIT 257"
    ), {"namespace": namespace, "runtime": runtime, "migration": migration}).mappings().all()
    if not relations or len(relations) > 256:
        raise ReleaseDenied("runtime_relation_permission_inventory_bound")
    if any(not row["can_select"] or not row["migration_owns"]
           or (row["relname"] != "alembic_version" and (not row["can_insert"] or not row["can_update"]))
           or (row["relname"] in ERASABLE_TABLES and not row["can_delete"])
           for row in relations):
        raise ReleaseDenied("runtime_table_or_migration_ownership_permissions_missing")
    sequences = connection.execute(text(
        "SELECT has_sequence_privilege(:runtime,c.oid,'USAGE') FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=:namespace AND c.relkind='S' ORDER BY c.oid LIMIT 257"
    ), {"namespace": namespace, "runtime": runtime}).scalars().all()
    if len(sequences) > 256 or not all(sequences):
        raise ReleaseDenied("runtime_sequence_usage_missing")


def observe_release(plan: Plan, boundary: NativeBoundary) -> dict[str, Any]:
    """Useful read-only readiness inventory; never an executable certificate."""
    plan.validate()
    # Reject unknown local source before even observational cloud access.
    tracked = source_guard.checked_files(boundary.root, plan.release)
    if source_guard.migration_chain(boundary.root, tracked) != CHAIN:
        raise ReleaseDenied("reviewed_schema13_chain_required")
    gates: dict[str, dict[str, Any]] = {}
    checks = (
        ("source_ci_image_policy", lambda: boundary.verify_identity_and_policy(plan)),
        ("queue_scheduler_job_drain", boundary.verify_admission_closed),
        ("database_writer_retirement", lambda: boundary.verify_database(plan, "20261008_0009")),
        ("external_provider_and_consumer_fence", lambda: boundary.verify_fences(plan)),
    )
    for name, check in checks:
        try:
            check()
            gates[name] = {"observed_check_passed": True}
        except (ReleaseDenied, source_guard.PreflightDenied) as error:
            gates[name] = {"observed_check_passed": False, "reason": str(error)}
        except Exception:  # noqa: BLE001 - vendor failures must become fixed nonsecret refusal reasons.
            gates[name] = {"observed_check_passed": False, "reason": "observation_or_required_configuration_unavailable"}
    return {"release": plan.release, "gates": gates, "cloud_mutations": 0,
            "production_released": False, "cutover_authorized": False,
            "native_fence_verifier_available": False,
            "post_fence_backup": "must_be_created_and_verified_in_the_authorized_execution",
            "planned_schema_steps": list(CHAIN[-4:]),
            "joined_services": list(SERVICES), "queue_resume_is_separate": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--plan-sha256", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--observe", action="store_true", help="Read-only gate inventory (the default)")
    args = parser.parse_args()
    try:
        raw = args.plan.read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.plan_sha256:
            raise ReleaseDenied("release_plan_hash_mismatch")
        data = _json(raw)
        data["inventory_path"] = Path(data["inventory_path"])
        plan = Plan(**data)
        boundary = NativeBoundary(Path(__file__).resolve().parents[2])
        result = (run_release(plan, boundary, execute=True) if args.execute
                  else observe_release(plan, boundary))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReleaseDenied, source_guard.PreflightDenied) as error:
        print(json.dumps({"production_released": False, "refused": str(error)}))
    except Exception:  # noqa: BLE001 - never print credential-bearing transport exceptions.
        print('{"production_released":false,"refused":"release_observation_or_configuration_unavailable"}')
    return 65


if __name__ == "__main__":
    sys.exit(main())
