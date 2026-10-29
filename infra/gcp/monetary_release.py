"""Schema0016 release sequence with live observations and no assertion override.

The native adapter currently refuses external consumer/provider fencing: no
authenticated verifier for that scope exists in this project. Its sequencer and
bounded migration command are implemented; production execution remains unavailable.
"""

from __future__ import annotations

import argparse
import ast
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

import document_network as document_routing
import document_worker as documents
import native_tex_worker as native_tex
import release_preflight as source_guard
import yaml

PROJECT = "ai-resume-parser-482412"
REGION = "us-central1"
REPOSITORY = "Harshil4442/ai-resume-copilot"
SERVICES = ("ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-employer-worker")
QUEUES = (
    "hirewiz-analysis",
    "hirewiz-employer-search",
    "hirewiz-employer-ingestion",
    "hirewiz-employer-application",
)
CI_JOBS = {
    "backend",
    "frontend",
    "security-and-container",
    "browser-companion",
    "cold-browser-journey",
}
CUTOVER_CHAIN = (
    "20261008_0009",
    "20261009_0010",
    "20261009_0011",
    "20261009_0012",
    "20261009_0013",
    "20261010_0014",
    "20261011_0015",
    "20261011_0016",
    "20261014_0017",
    "20261015_0018",
)
CHAIN = (*source_guard.ALLOWED_CHAIN[:-1], *CUTOVER_CHAIN)
SAFE_ENV = {
    "APP_ENV": "production",
    "PGSSLROOTCERT": "/etc/ssl/certs/ca-certificates.crt",
    "AUTO_DB_MIGRATE": "false",
    "OPTIONAL_AI_GENERATION_ENABLED": "false",
    "EMPLOYER_AUTO_SUBMIT_ENABLED": "false",
    "RAZORPAY_CHECKOUT_ENABLED": "false",
    "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED": "false",
    "BROWSER_ASSISTANCE_ENABLED": "false",
    "EMPLOYER_SEARCH_CREDITS_PER_JOB": "1",
    "EMPLOYER_APPLY_CREDITS_PER_JOB": "20",
}
NATIVE_ENV = (
    "NATIVE_TEX_IMAGE",
    "NATIVE_TEX_COMPILER_URL",
    "NATIVE_TEX_IMAGE_DIGEST",
    "NATIVE_TEX_ISOLATION_POLICY_SHA256",
)
MODEL_API_BASE = "https://generativelanguage.googleapis.com"
MODEL_ENV = ("LLM_MODEL", "LLM_API_BASE", "LLM_MODEL_COST_POLICY_JSON", "LLM_API_KEY")
# Retire legacy/vendor selectors without touching project, task or payment settings.
MODEL_ALIASES = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "GOOGLE_GENAI_USE_VERTEXAI",
)
MAX_JSON = 131072
ERASABLE_TABLES = {
    "users",
    "user_profiles",
    "resumes",
    "job_matches",
    "skill_coverage",
    "opportunities",
    "application_events",
    "resume_versions",
    "evidence_items",
    "reminders",
    "opportunity_contacts",
    "career_memory_entries",
    "notification_outbox",
    "model_call_events",
    "dispatch_outbox",
    "analysis_request_keys",
    "usage_events",
    "analysis_runs",
    "candidate_password_accounts",
    "employer_application_approvals",
    "employer_application_attempts",
    "employer_applications",
    "employer_application_batches",
    "sealed_application_artifacts",
    "employer_job_deliveries",
    "employer_searches",
    "employer_artifact_uploads",
    "employer_paid_delivery_history",
    "employer_paid_opening_registry",
    "employer_paid_history_progress",
    "employer_search_history_progress",
    "employer_search_history_seen",
    "employer_search_history_nodes",
    "employer_search_preparations",
    "employer_catalogue_snapshots",
    "employer_catalogue_snapshot_items",
    "employer_catalogue_winners",
    # Ordinary revision pruning also needs DELETE; financial history does not.
    "employer_catalogue_source_revisions",
    "employer_catalogue_posting_revisions",
    "browser_assistance_devices",
    "browser_assistance_reviews",
    "browser_assistance_requests",
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
    observer_secret: str
    database_owner_oid: int
    expense_secret: str
    expense_sha256: str
    model_policy_secret: str
    model_policy_sha256: str
    model_key_secret: str
    model_primary: str
    model_api_base: str
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
                   for ref in (self.runtime_secret, self.migration_secret, self.observer_secret, self.expense_secret,
                               self.model_policy_secret, self.model_key_secret))
            or self.runtime_secret.split(":")[0] == self.migration_secret.split(":")[0]
            or not re.fullmatch(r"[a-f0-9]{64}", self.expense_sha256)
            or not re.fullmatch(r"[a-f0-9]{64}", self.model_policy_sha256)
            or not re.fullmatch(r"gemini-[A-Za-z0-9_.-]{1,113}", self.model_primary)
            or self.model_api_base != MODEL_API_BASE
            or len({ref.split(":")[0] for ref in (self.runtime_secret, self.migration_secret, self.observer_secret,
                        self.expense_secret, self.model_policy_secret, self.model_key_secret)}) != 6
            or type(self.database_owner_oid) is not int or not 0 < self.database_owner_oid <= 4294967295
            or not re.fullmatch(r"[a-f0-9]{64}", self.inventory_sha256)
            or self.runtime_service_account == self.migration_service_account
            or any(not re.fullmatch(r"[a-z][a-z0-9-]{4,28}@" + PROJECT + r"\.iam\.gserviceaccount\.com", value)
                   for value in (self.runtime_service_account, self.migration_service_account))
            or not re.fullmatch(r"gs://" + PROJECT + r"-hirewiz-application-artifacts/releases/[a-zA-Z0-9_.-]+\.dump", self.backup_uri)
        ):
            raise ReleaseDenied("release_plan_identity_invalid")


@dataclass(frozen=True)
class FreshTargetPlan:
    """Separate numeric destination sources; never reinterpret the source plan."""

    runtime_secret: str
    migration_secret: str
    observer_secret: str

    def validate(self, source: Plan) -> None:
        source.validate()
        refs = (self.runtime_secret, self.migration_secret, self.observer_secret)
        if any(
            type(ref) is not str
            or re.fullmatch(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}", ref) is None
            for ref in refs
        ):
            raise ReleaseDenied("fresh_target_secret_identity_invalid")
        names = {ref.split(":")[0] for ref in refs}
        old_names = {
            ref.split(":")[0]
            for ref in (
                source.runtime_secret,
                source.migration_secret,
                source.observer_secret,
                source.expense_secret,
                source.model_policy_secret,
                source.model_key_secret,
            )
            if ref
        }
        if len(names) != 3 or names & old_names:
            raise ReleaseDenied("source_and_fresh_secret_classes_overlap")


def runtime_environment(plan: Plan, service: str) -> tuple[dict[str, str], dict[str, str], tuple[str, ...]]:
    """One service-scoped contract for staging and exact native readback."""
    if service not in SERVICES:
        raise ReleaseDenied("joined_service_identity_invalid")
    env = {**SAFE_ENV, "APP_RELEASE": plan.release,
           "SERVICE_ROLE": "api" if service == SERVICES[0] else "worker"}
    secrets = {"DATABASE_URL": plan.runtime_secret, "HIREWIZ_EXPENSE_POLICY_JSON": plan.expense_secret}
    removed: tuple[str, ...] = (*NATIVE_ENV, *MODEL_ALIASES)
    if service != SERVICES[0]:
        env["WORKER_LABEL"] = "analysis-worker" if service == SERVICES[1] else "employer-worker"
        env["WORKER_ALLOWED_TOPICS"] = ("analysis.run" if service == SERVICES[1]
                                       else "employer.search,employer.refresh,employer.apply,employer.artifact-delete")
    if service in SERVICES[:2]:
        try:
            tex_env = native_tex.api_environment(
                getattr(native_tex, "ACCEPTED_ORIGIN", ""),
                getattr(native_tex, "ACCEPTED_IMAGE", ""),
            )
        except ValueError:
            raise ReleaseDenied("native_tex_accepted_worker_binding_missing") from None
        removed = tuple(name for name in removed if name not in tex_env)
        env.update(tex_env)
        env.update(documents.api_environment(documents.ACCEPTED_ORIGIN))
        env.update(LLM_MODEL=plan.model_primary, LLM_API_BASE=plan.model_api_base)
        secrets.update(LLM_MODEL_COST_POLICY_JSON=plan.model_policy_secret, LLM_API_KEY=plan.model_key_secret)
    else:
        removed = (*removed, *MODEL_ENV, *documents.api_environment(documents.ACCEPTED_ORIGIN))
    return env, secrets, removed


def _gemini_fallbacks(root: Path) -> tuple[str, ...]:
    """Read the committed literal without initializing the provider/app modules."""
    tree = ast.parse((root / "backend/app/services/llm_client.py").read_bytes())
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "GEMINI_FALLBACK_MODELS"
                           for target in node.targets)]
    if len(assignments) != 1:
        raise ReleaseDenied("model_fallback_source_contract_invalid")
    models = ast.literal_eval(assignments[0].value)
    if (not isinstance(models, tuple) or not 1 <= len(models) <= 100
            or any(not isinstance(model, str) or not re.fullmatch(r"gemini-[A-Za-z0-9_.-]{1,113}", model)
                   for model in models) or len(models) != len(set(models))):
        raise ReleaseDenied("model_fallback_source_contract_invalid")
    return models


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
    for expected, target in zip(CUTOVER_CHAIN[:-1], CUTOVER_CHAIN[1:], strict=True):
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
        import certifi

        # Keep existing authentication inputs, but do not inherit transport
        # redirects, HTTP/body logging or alternate trust configuration. SDK
        # environment properties override persisted configuration without edits.
        environment = {
            key: value for key, value in os.environ.items()
            if not key.upper().startswith(("GIT_", "CLOUDSDK_API_ENDPOINT_OVERRIDES_",
                                           "CLOUDSDK_PROXY_", "CLOUDSDK_CORE_LOG_HTTP"))
            and key.upper() not in {
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "GRPC_PROXY",
                "NO_GRPC_PROXY", "SSLKEYLOGFILE",
                "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                "GRPC_TRACE", "GRPC_VERBOSITY", "GH_DEBUG", "PYTHONINSPECT", "PYTHONVERBOSE",
                "CLOUDSDK_CORE_VERBOSITY", "CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE",
                "CLOUDSDK_CORE_DISABLE_SSL_VALIDATION", "GOOGLE_API_USE_MTLS_ENDPOINT",
                "GOOGLE_API_USE_CLIENT_CERTIFICATE",
            }
        }
        ca_bundle = certifi.where()
        environment.update({
            "GH_HOST": "github.com", "CLOUDSDK_CORE_LOG_HTTP": "false",
            "CLOUDSDK_CORE_VERBOSITY": "error",
            "CLOUDSDK_CORE_DISABLE_SSL_VALIDATION": "false",
            "CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE": ca_bundle,
            "SSL_CERT_FILE": ca_bundle, "REQUESTS_CA_BUNDLE": ca_bundle,
            "CURL_CA_BUNDLE": ca_bundle, "GOOGLE_API_USE_MTLS_ENDPOINT": "never",
            "GOOGLE_API_USE_CLIENT_CERTIFICATE": "false",
            "NO_PROXY": "*", "NO_GRPC_PROXY": "*",
            # With no configured proxy type these are unset-equivalent. A
            # persisted type now has incomplete settings and fails closed.
            "CLOUDSDK_PROXY_ADDRESS": "", "CLOUDSDK_PROXY_PORT": "0",
        })
        for api in ("run", "secretmanager", "cloudbuild", "iam", "iamcredentials",
                    "cloudtasks", "cloudscheduler", "cloudresourcemanager", "storage", "compute", "dns"):
            environment["CLOUDSDK_API_ENDPOINT_OVERRIDES_" + api.upper()] = f"https://{api}.googleapis.com/"
        try:
            result = subprocess.run([executable, *args], cwd=self.root, check=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=600, env=environment)
            if len(result.stdout) > MAX_JSON:
                raise ReleaseDenied("observation_size_bound")
            return result.stdout
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise ReleaseDenied("cloud_operation_unavailable_or_outcome_unknown") from None

    def _cloud(self, *args: str) -> Any:
        # Execution templates can repeat large environment/configuration bodies;
        # this inventory needs only identity and completion, never those bodies.
        output = ("json(metadata.name,status.completionTime)"
                  if args[:4] == ("run", "jobs", "executions", "list") else "json")
        return _json(self._command("gcloud", *args, "--project", PROJECT, "--format=" + output, "--quiet"))

    def _module(self, relative: str, name: str) -> Any:
        spec = importlib.util.spec_from_file_location(name, self.root / relative)
        if spec is None or spec.loader is None:
            raise ReleaseDenied("reviewed_source_module_unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def _verify_secret_version(self, reference: str) -> None:
        name, version = reference.split(":")
        metadata = self._cloud("secrets", "versions", "describe", version, "--secret", name)
        if metadata.get("state") != "ENABLED" or metadata.get("name", "").split("/")[-1] != version:
            raise ReleaseDenied("exact_secret_version_not_enabled")

    def _secret(self, reference: str) -> str:
        self._verify_secret_version(reference)
        name, version = reference.split(":")
        return self._command("gcloud", "secrets", "versions", "access", version, "--secret", name,
                             "--project", PROJECT, "--quiet").decode()

    def describe_release_secret(self, reference: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}", reference):
            raise ReleaseDenied("release_database_secret_reference_invalid")
        name, version = reference.split(":")
        metadata = self._cloud("secrets", "versions", "describe", version, "--secret", name)
        if type(metadata) is not dict:
            raise ReleaseDenied("release_database_secret_metadata_invalid")
        return metadata

    def access_release_secret(self, reference: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}", reference):
            raise ReleaseDenied("release_database_secret_reference_invalid")
        name, version = reference.split(":")
        return self._command("gcloud", "secrets", "versions", "access", version,
                             "--secret", name, "--project", PROJECT, "--quiet").decode()

    def _verify_model_policy(self, plan: Plan, expense: dict[str, Any]) -> None:
        raw = self._secret(plan.model_policy_secret)
        if hashlib.sha256(raw.encode()).hexdigest() != plan.model_policy_sha256:
            raise ReleaseDenied("model_policy_secret_hash_mismatch")
        authority = self._module("backend/app/services/model_cost_policy.py", "hirewiz_release_model_policy")
        try:
            policy = authority.ModelCostPolicy.model_validate(_json(raw.encode()))
            if (policy.expense_funding is not None or policy.expense_policy_version is not None
                    or policy.expense_paid_units is not None):
                raise ReleaseDenied("model_policy_runtime_funding_not_configuration")
            limits = expense["operation_model_ceilings_micros"]
            if any(limit > limits.get(operation, 0) for operation, limit in policy.operation_limits_micros.items()):
                raise ReleaseDenied("model_policy_exceeds_funded_operation_ceiling")
            models = tuple(dict.fromkeys((plan.model_primary, *_gemini_fallbacks(self.root))))
            now = datetime.now(UTC)
            for model in models:
                quotes = [quote for quote in policy.pricing_quotes
                          if (quote.provider, quote.model, quote.api_base) == ("google", model, plan.model_api_base)]
                if len(quotes) != 1 or not quotes[0].valid_from <= now < quotes[0].expires_at:
                    raise ReleaseDenied("exact_model_fallback_price_authority_unavailable")
        except ReleaseDenied:
            raise
        except (ValueError, TypeError, KeyError):
            raise ReleaseDenied("model_policy_invalid") from None
        # Observe only the selected numeric key version's metadata. Never read or
        # serialize its credential; provider retirement remains a separate gate.
        self._verify_secret_version(plan.model_key_secret)

    def verify_source_image(self, *, release: str, image_digest: str, build_id: str,
                            ci_run_id: int) -> None:
        """Current Git/CI/native app-image proof, independent of database cutover."""
        tracked = source_guard.checked_files(self.root, release)
        if source_guard.migration_chain(self.root, tracked, allow_literal_sql=True) != CHAIN:
            raise ReleaseDenied("reviewed_schema16_chain_required")
        self._verify_source_image_provenance(release=release, image_digest=image_digest,
                                             build_id=build_id, ci_run_id=ci_run_id)

    def _verify_source_image_provenance(self, *, release: str, image_digest: str,
                                       build_id: str, ci_run_id: int) -> None:
        """Shared immutable Git/CI/build proof; callers admit their exact schema first."""
        image = f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/hirewiz@{image_digest}"
        verify_ci_checkout_contract(self.root)
        ci = _json(self._command("gh", "run", "view", str(ci_run_id), "--repo", REPOSITORY,
                                 "--json", "headSha,status,conclusion,jobs,workflowName"))
        workflow = _json(self._command("gh", "api", f"repos/{REPOSITORY}/actions/runs/{ci_run_id}"))
        jobs = ci.get("jobs", [])
        if (workflow.get("path") != ".github/workflows/ci.yml"
                or workflow.get("head_sha") != release
                or workflow.get("event") not in {"push", "pull_request"}
                or workflow.get("repository", {}).get("full_name") != REPOSITORY
                or len(jobs) != len(CI_JOBS)
                or ci.get("headSha") != release or ci.get("workflowName") != "CI"
                or ci.get("status") != "completed" or ci.get("conclusion") != "success"
                or {item.get("name") for item in jobs} != CI_JOBS
                or any(item.get("status") != "completed" or item.get("conclusion") != "success" for item in jobs)):
            raise ReleaseDenied("exact_release_five_ci_jobs_not_successful")
        build = self._cloud("builds", "describe", build_id, "--region", REGION)
        # Only a native Cloud Build Git-source attestation is supported. Uploaded
        # local archives, labels or caller source-hash files are not provenance.
        provenance = build.get("sourceProvenance", {}).get("resolvedGitSource", {})
        tag = image.split("@")[0] + ":" + release
        steps = build.get("steps", [])
        images = build.get("results", {}).get("images", [])
        expected_steps = (["build", "--tag", tag, "."], ["push", tag])
        if steps and steps[0].get("args") == ["build", "-t", tag, "."]:
            expected_steps = (["build", "-t", tag, "."], ["push", tag])
        elif steps and steps[0].get("args") == ["build", "--tag=" + tag, "."]:
            expected_steps = (["build", "--tag=" + tag, "."], ["push", tag])
        if (build.get("status") != "SUCCESS" or build.get("projectId") != PROJECT
                or build.get("id") != build_id
                or provenance.get("revision") != release
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
                or images[0].get("digest") != image_digest
                or build.get("availableSecrets") or build.get("secrets")):
            raise ReleaseDenied("native_exact_source_image_provenance_not_verified")

    def verify_policy_configuration(self, plan: Any) -> None:
        """Reuse the actual expense/model authority without enabling any actor."""
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
            self._verify_model_policy(plan, rates["cost_policy_snapshot"]["policy"])
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def verify_identity_and_policy(self, plan: Plan) -> None:
        self.verify_source_image(release=plan.release, image_digest=plan.image_digest,
                                 build_id=plan.build_id, ci_run_id=plan.ci_run_id)
        self.verify_policy_configuration(plan)
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
        if not isinstance(executions, list) or len(executions) > 256 or any(
            not isinstance(item, dict) or not isinstance(item.get("status"), dict)
            or not item["status"].get("completionTime") for item in executions
        ):
            raise ReleaseDenied("job_execution_inventory_unresolved")

    def verify_fences(self, plan: Plan) -> Fence:
        # Secret Manager DISABLED, Cloud Run zero traffic, queue PAUSED and a SQL
        # snapshot cannot revoke already-loaded provider credentials or prove all
        # external consumers. There is no trusted native verifier in this repo.
        # Never read inventory.provider_fencing_verified or accept an override.
        raise ReleaseDenied("native_external_consumer_and_provider_fence_verifier_missing")

    def _database_context(self, plan: Plan, schema: str) -> tuple[Any, Any, dict[str, str], tuple[str, ...]]:
        """Retained source context; fresh target/phase orchestration is separate."""
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
        binding, target = module.neon_identity.collect_source_release_target(
            self, plan.observer_secret, alias="observer",
            database_owner_oid=plan.database_owner_oid, expected_schema=schema,
        )
        if any(roles.get(alias) != module.neon_identity.RELEASE_ACTORS[alias][0]
               for alias in ("replacement_runtime", "replacement_migration")):
            raise ReleaseDenied("replacement_role_prepared_identity_mismatch")
        return module, (target.engine(), inventory, binding, target), roles, operators

    def verify_database(self, plan: Plan, schema: str) -> None:
        module, (engine, inventory, binding, _), roles, operators = self._database_context(plan, schema)
        try:
            observed = module.observe(engine, inventory, roles, operators, neon_binding=binding)
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
            with engine.connect() as connection:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                verify_runtime_permissions(connection, inventory.database_namespace, roles["replacement_runtime"], roles["replacement_migration"])
            # Pinned numeric secrets must actually connect as distinct observed
            # roles into this database; a DSN username is not credential proof.
            for alias, ref in (("replacement_runtime", plan.runtime_secret), ("replacement_migration", plan.migration_secret)):
                secret_binding, secret_target = module.neon_identity.collect_release_target(
                    self, ref, alias=alias, database_owner_oid=plan.database_owner_oid,
                    expected_schema=schema,
                )
                credential_engine = secret_target.engine()
                try:
                    with credential_engine.connect() as connection:
                        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                        connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                        module.neon_identity.verify_transport(connection, secret_binding)
                        module.neon_identity.verify_native_target(connection, secret_binding, inventory.expected_database_oid)
                        # Preserve genuine native session/PID/cancellation-key
                        # proof for these distinct authenticated role sources too.
                        module._native_session_ids(connection, inventory.expected_database_oid,
                                                   neon_binding=secret_binding)
                finally:
                    credential_engine.dispose()
        finally:
            engine.dispose()

    def verify_fresh_database_phase(
        self, source: Plan, target: FreshTargetPlan, schema: str, *, phase: str,
    ) -> None:
        """Read-only native identity/privilege slice, not full release admission.

        Source remains independently bound to schema9. Phase is explicit; schema16
        alone cannot imply migrator retirement. Legacy backup/fence/financial gates
        and run_release remain unchanged and cannot select this route implicitly.
        """
        if (type(target) is not FreshTargetPlan or type(phase) is not str
                or phase not in {"migration", "serving"}):
            raise ReleaseDenied("fresh_target_phase_invalid")
        if type(schema) is not str or schema not in CUTOVER_CHAIN:
            raise ReleaseDenied("fresh_target_schema_invalid")
        source.validate()
        target.validate(source)
        module, (source_engine, inventory, source_binding, _), _, _ = self._database_context(
            source, CUTOVER_CHAIN[0])
        neon = module.neon_identity
        observer_engine = None
        try:
            if (source_binding.purpose != "release_capture"
                    or inventory.expected_database_oid != neon.DATABASE_OID
                    or inventory.expected_system_identifier_sha256 != neon.CLUSTER
                    or (neon.DATABASE_OID, neon.CLUSTER) == (neon.FRESH_DATABASE_OID, neon.FRESH_CLUSTER)):
                raise ReleaseDenied("source_and_fresh_native_classes_overlap")
            with source_engine.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL search_path = pg_catalog")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                neon.verify_transport(connection, source_binding)
                neon.verify_native_target(connection, source_binding, neon.DATABASE_OID)
                module._native_session_ids(connection, neon.DATABASE_OID, neon_binding=source_binding)
            binding, observed_target = neon.collect_fresh_release_target(
                self, target.observer_secret, alias="observer",
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=schema)
            observer_engine = observed_target.engine()
            with observer_engine.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL search_path = pg_catalog")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                neon.verify_transport(connection, binding)
                neon.verify_native_target(connection, binding, neon.FRESH_DATABASE_OID)
                sessions, _ = module._native_session_ids(
                    connection, neon.FRESH_DATABASE_OID, neon_binding=binding)
                verify_fresh_role_phase(connection, neon.FRESH_RELEASE_ACTORS, phase, sessions)
                runtime = neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0]
                migration = neon.FRESH_RELEASE_ACTORS["replacement_migration"][0]
                verify_runtime_permissions(connection, neon.NAMESPACE, runtime, migration)
            # Authenticate serving identities separately; never authenticate the
            # retired migration credential during the serving phase.
            aliases = [("replacement_runtime", target.runtime_secret)]
            if phase == "migration":
                aliases.append(("replacement_migration", target.migration_secret))
            else:
                metadata = self.describe_release_secret(target.migration_secret)
                name, version = target.migration_secret.split(":")
                expected = f"projects/{neon.PROJECT_NUMBER}/secrets/{name}/versions/{version}"
                if (type(metadata) is not dict or metadata.get("name") != expected
                        or metadata.get("state") != "DISABLED"):
                    raise ReleaseDenied("fresh_migration_numeric_version_not_disabled")
            for alias, reference in aliases:
                actor_binding, actor_target = neon.collect_fresh_release_target(
                    self, reference, alias=alias,
                    database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=schema)
                actor_engine = actor_target.engine()
                try:
                    with actor_engine.connect() as connection, connection.begin():
                        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                        connection.exec_driver_sql("SET LOCAL search_path = pg_catalog")
                        connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                        neon.verify_transport(connection, actor_binding)
                        neon.verify_native_target(connection, actor_binding, neon.FRESH_DATABASE_OID)
                        module._native_session_ids(
                            connection, neon.FRESH_DATABASE_OID, neon_binding=actor_binding)
                finally:
                    actor_engine.dispose()
        finally:
            if observer_engine is not None:
                observer_engine.dispose()
            source_engine.dispose()

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
            observer_module, (engine, inventory, binding, _target), _roles, _operators = self._database_context(plan, "20261008_0009")
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = '610000ms'")
                observer_module.neon_identity.verify_transport(connection, binding)
                observer_module.neon_identity.verify_native_target(connection, binding, inventory.expected_database_oid)
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

        _, (engine, inventory, _, dump_target), _, _ = self._database_context(plan, "20261008_0009")
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
        object_retention = bucket.get("per_object_retention")
        if type(object_retention) is not dict or object_retention.get("mode") != "Enabled":
            raise ReleaseDenied("protected_backup_bucket_object_retention_not_enabled")
        bucket_policy = self._cloud("storage", "buckets", "get-iam-policy", bucket_uri)
        if any(member in {"allUsers", "allAuthenticatedUsers"}
               for binding in bucket_policy.get("bindings", []) for member in binding.get("members", [])):
            raise ReleaseDenied("protected_backup_bucket_is_public")
        environment = {**{k: v for k, v in os.environ.items() if not k.startswith("PG")},
                       **dump_target.dump_environment()}
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
        # gcloud's object serializer names the wire API's retention field
        # retention_settings. Accept either complete native representation,
        # but never resolve competing aliases by silently preferring one.
        retention_fields = [backup[key] for key in ("retention_settings", "retention") if key in backup]
        if len(retention_fields) != 1 or type(retention_fields[0]) is not dict:
            raise ReleaseDenied("fresh_post_fence_protected_backup_not_verified")
        retention = retention_fields[0]
        until_raw = retention.get("retain_until_time", retention.get("retainUntilTime", ""))
        if type(until_raw) is not str:
            raise ReleaseDenied("fresh_post_fence_protected_backup_not_verified")
        try:
            until = datetime.fromisoformat(until_raw.replace("Z", "+00:00"))
        except ValueError:
            raise ReleaseDenied("fresh_post_fence_protected_backup_not_verified") from None
        if until.tzinfo is None:
            raise ReleaseDenied("fresh_post_fence_protected_backup_not_verified")
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
        _, (engine, inventory, _binding, _target), roles, _ = self._database_context(plan, expected)
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
        env, secrets, removed = runtime_environment(plan, service)
        # Literal key/policy remnants are removed before the secret-reference
        # update. Secret selectors for literal model/base fields refuse safely
        # in gcloud rather than silently changing an unsupported source kind.
        literal_removals = (*removed, *secrets.keys())
        auth_args = () if service == SERVICES[0] else ("--no-allow-unauthenticated",)
        network_args = tuple(document_routing.caller_deploy_args()) if service in SERVICES[:2] else ()
        staged = self._cloud("run", "deploy", service, *auth_args, "--region", REGION, "--image", plan.image,
                             *network_args,
                             "--service-account", plan.runtime_service_account, "--no-traffic", "--tag", "monetary-candidate",
                             "--command", "", "--args", "", "--remove-env-vars", ",".join(literal_removals),
                             "--remove-secrets", ",".join(removed),
                             "--update-env-vars", "^|^" + "|".join(f"{k}={v}" for k, v in env.items()),
                             "--update-secrets", ",".join(f"{name}={reference}" for name, reference in secrets.items()))
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
        expected_env, expected_secrets, removed = runtime_environment(plan, service)
        if (observed.get("metadata", {}).get("labels", {}).get("serving.knative.dev/service") != service
                or observed.get("status", {}).get("imageDigest") != plan.image
                or observed.get("spec", {}).get("serviceAccountName") != plan.runtime_service_account
                or len(env) != len(environment) or container.get("command") or container.get("args")
                or any(env.get(name) != {"name": name, "value": value} for name, value in expected_env.items())
                or any(name in env for name in removed)
                or any(env.get(name) != {"name": name, "valueFrom": {"secretKeyRef": dict(zip(
                    ("name", "key"), reference.split(":"), strict=True))}}
                       for name, reference in expected_secrets.items())
                or not any(condition.get("type") == "Ready" and condition.get("status") == "True" for condition in observed.get("status", {}).get("conditions", []))):
            raise ReleaseDenied("joined_revision_identity_or_safety_mismatch")
        if service in SERVICES[:2]:
            try:
                document_routing.verify_caller(observed)
            except document_routing.NetworkConfigurationDenied:
                raise ReleaseDenied("joined_document_caller_network_mismatch") from None
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

    def _health_id_token(self, audience: str) -> str:
        """Mint only the Tasks identity token; never impersonate an access token.

        gcloud's impersonated print-identity-token first obtains an access token,
        requiring a broader grant than iam.serviceAccounts.getOpenIdToken. Use
        the official endpoint with the existing normal retained SDK identity.
        """
        from urllib.parse import urlsplit

        import certifi

        session = None
        try:
            parsed = urlsplit(audience)
            if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".run.app")
                    or parsed.username or parsed.password or parsed.port not in {None, 443}
                    or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                    or os.environ.get("SSLKEYLOGFILE")):
                raise ReleaseDenied("exact_candidate_id_token_unavailable")
            from document_refresh_auth import RELEASE_ACCOUNT
            from document_refresh_probe import sdk_library
            from native_fresh_fence import SDKGoogleReads

            # A separate stock manager preserves its canonical account, project,
            # TLS, proxy, logging and non-impersonated credential checks.
            http = SDKGoogleReads(sdk_library(), RELEASE_ACCOUNT)._http()
            session = http.session
            session.trust_env, session.max_redirects, session.verify = False, 0, certifi.where()
            for adapter in session.adapters.values():
                adapter.max_retries = adapter.max_retries.__class__(
                    total=0, connect=0, read=0, redirect=0, status=0)
            destination = ("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                           f"hirewiz-tasks@{PROJECT}.iam.gserviceaccount.com:generateIdToken")
            body = {"audience": audience, "includeEmail": True}
            send, issued = session.send, 0

            def send_once(request: Any, **kwargs: Any) -> Any:
                nonlocal issued
                # A normal OAuth refresh may run, but an authentication challenge
                # must not silently repeat this token-issuance POST.
                if request.url == "https://oauth2.googleapis.com/token" and request.method == "POST":
                    pass
                elif (request.url == destination and request.method == "POST"
                      and json.loads(request.body) == body and issued == 0):
                    issued += 1
                else:
                    raise ReleaseDenied("exact_candidate_id_token_unavailable")
                kwargs["allow_redirects"] = False
                response = send(request, **kwargs)
                if request.url == destination:
                    try:
                        raw = response.raw.read(MAX_JSON + 1, decode_content=True)
                        if type(raw) is not bytes or len(raw) > MAX_JSON:
                            raise ReleaseDenied("exact_candidate_id_token_unavailable")
                        # Stock SDK response handlers materialize .content even
                        # for streamed requests. Bound it before those handlers.
                        response._content, response._content_consumed = raw, True
                    except Exception:
                        response.close()
                        raise
                return response

            session.send = send_once
            response = session.request("POST", destination, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, timeout=(5, 15),
                allow_redirects=False, stream=True)
            try:
                if response.status_code != 200 or issued != 1:
                    raise ReleaseDenied("exact_candidate_id_token_unavailable")
                value = _json(response.content)
                token = value.get("token")
                if (set(value) != {"token"} or type(token) is not str or not 1 <= len(token) <= 16384
                        or any(not 33 <= ord(character) <= 126 for character in token)):
                    raise ReleaseDenied("exact_candidate_id_token_unavailable")
                return token
            finally:
                response.close()
        except Exception:  # noqa: BLE001 - SDK/HTTP failures may contain bearer tokens.
            raise ReleaseDenied("exact_candidate_id_token_unavailable") from None
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:  # noqa: BLE001 - cleanup must not expose SDK diagnostics.
                    pass

    def _verify_http_health(self, plan: Plan, service: str, revision: str, *,
                            candidate_tag: str = "monetary-candidate") -> None:
        import ssl
        import urllib.request
        from urllib.parse import urlsplit

        import certifi
        if type(candidate_tag) is not str or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", candidate_tag):
            raise ReleaseDenied("exact_candidate_health_tag_invalid")
        observed = self._cloud("run", "services", "describe", service, "--region", REGION)
        tagged = [item for item in observed.get("status", {}).get("traffic", [])
                  if item.get("tag") == candidate_tag and item.get("revisionName") == revision]
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
        # Redirects are refused: a token cannot escape to an unobserved host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            # create_default_context can honor SSLKEYLOGFILE before it can be
            # disabled. Only this explicit CA bundle may establish server trust.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(cafile=certifi.where())
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), NoRedirect(),
                urllib.request.HTTPSHandler(context=context),
            )
            # Cloud Run requires the service URL audience even for traffic-tag URLs.
            token = self._health_id_token(audience)
            request = urllib.request.Request(url.rstrip("/") + "/api/health",
                                             headers={"Authorization": "Bearer " + token})
            with opener.open(request, timeout=30) as response:
                health = _json(response.read(MAX_JSON + 1))
        except ReleaseDenied:
            raise
        except Exception:  # noqa: BLE001 - transport errors can contain URLs or tokens.
            raise ReleaseDenied("exact_candidate_http_health_unavailable") from None
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
    # Resolve the venv parser before the coordinator SDK prepends bundled modules.
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_bytes())
    jobs = workflow.get("jobs", {})
    if set(jobs) != CI_JOBS:
        raise ReleaseDenied("reviewed_ci_checkout_contract_mismatch")
    for job in jobs.values():
        checkouts = [step for step in job.get("steps", []) if str(step.get("uses", "")).startswith("actions/checkout@")]
        if len(checkouts) != 1 or checkouts[0].get("with", {}).get("ref") != "${{ github.event.pull_request.head.sha || github.sha }}":
            raise ReleaseDenied("reviewed_ci_checkout_contract_mismatch")


def verify_fresh_role_phase(
    connection: Any, actors: dict[str, tuple[str, int]], phase: str,
    sessions: list[tuple[int, int | None, int | None]],
) -> None:
    """Finite native role constraints; this is not the old source retirement gate."""
    from sqlalchemy import text

    if type(phase) is not str or phase not in {"migration", "serving"} or set(actors) != {
            "observer", "replacement_runtime", "replacement_migration"}:
        raise ReleaseDenied("fresh_target_phase_invalid")
    names = [actors[key][0] for key in ("observer", "replacement_runtime", "replacement_migration")]
    rows = connection.execute(text(
        "SELECT r.oid,r.rolname,r.rolcanlogin,r.rolinherit,"
        "(r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls) AS elevated,"
        "has_schema_privilege(r.oid,'public','CREATE') AS schema_create,"
        "has_database_privilege(r.oid,current_database(),'CREATE') AS database_create,"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_roles a WHERE "
        "(a.rolsuper OR a.rolcreatedb OR a.rolcreaterole OR a.rolreplication OR a.rolbypassrls) "
        "AND (pg_has_role(r.oid,a.oid,'USAGE') OR pg_has_role(r.oid,a.oid,'SET'))) AS elevated_membership,"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid AND m.admin_option) AS administration "
        "FROM pg_catalog.pg_roles r WHERE r.rolname IN (:observer,:runtime,:migration) ORDER BY r.oid LIMIT 4"
    ), {"observer": names[0], "runtime": names[1], "migration": names[2]}).mappings().all()
    if len(rows) != 3:
        raise ReleaseDenied("fresh_target_role_inventory_mismatch")
    observed = {}
    booleans = {"rolcanlogin", "rolinherit", "elevated", "schema_create", "database_create", "elevated_membership", "administration"}
    for row in rows:
        if (set(row) != booleans | {"oid", "rolname"} or type(row["oid"]) is not int
                or row["rolname"] not in names or row["rolname"] in observed
                or any(type(row[key]) is not bool for key in booleans)
                or row["rolinherit"] or row["elevated"] or row["elevated_membership"] or row["administration"]):
            raise ReleaseDenied("fresh_target_role_authority_mismatch")
        observed[row["rolname"]] = row
    for alias, (name, oid) in actors.items():
        row = observed[name]
        if row["oid"] != oid:
            raise ReleaseDenied("fresh_target_role_oid_mismatch")
        migration = alias == "replacement_migration"
        expected = migration and phase == "migration"
        if (row["schema_create"] != expected or row["rolcanlogin"] != (not migration or expected)
                or row["database_create"] and not expected):
            raise ReleaseDenied("fresh_target_role_phase_mismatch")
    if phase == "serving" and any(role == actors["replacement_migration"][1] for _, role, _ in sessions):
        raise ReleaseDenied("fresh_migration_session_remains")


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
    if any(row["relname"] == "employer_host_pacing" and row["can_delete"] for row in relations):
        raise ReleaseDenied("runtime_global_host_pacing_delete_forbidden")
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
    if source_guard.migration_chain(boundary.root, tracked, allow_literal_sql=True) != CHAIN:
        raise ReleaseDenied("reviewed_schema16_chain_required")
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
            "planned_schema_steps": list(CUTOVER_CHAIN[1:]),
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
