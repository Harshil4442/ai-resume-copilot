"""Versioned offline isolation successor; never grants or attests native authority.

The existing two-caller contracts remain unchanged. This evaluator checks the real
three-caller snapshot directly; it never removes a member to satisfy an old check.
Native owner custody, exact-source CI/Build and actual processing remain separate.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
import document_worker as documents  # noqa: E402
import native_tex_worker as tex  # noqa: E402

PROJECT = documents.PROJECT
REGION = documents.REGION
PROJECT_NUMBER = "157225724590"
HISTORICAL_RELEASE = "7be8cac2a332a5ad19366402013d5f385f97105b"
EVALUATOR = f"hirewiz-quality-dev@{PROJECT}.iam.gserviceaccount.com"
BUCKET = "hirewiz-quality-dev-482412-20261031"
ROLE = f"projects/{PROJECT}/roles/hirewizQualityDevelopmentObjects"
RUNNER_REPOSITORY = f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/hirewiz"
VERSION: Literal["native-quality-isolation-v1"] = "native-quality-isolation-v1"
WORKSPACE = "/app/backend/.quality-development"
SHA = r"[a-f0-9]{64}"


class IsolationDenied(ValueError):
    pass


def need(value: Any, code: str) -> None:
    if not value:
        raise IsolationDenied("quality_isolation_" + code)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class QualifiedRunner(BaseModel):
    """Untrusted external claims until the fixed fresh verifier succeeds."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")
    schema_version: Literal["native-quality-qualified-runner-v1"]
    repository: Literal["https://github.com/Harshil4442/ai-resume-copilot.git"]
    release: str = Field(pattern=r"^[a-f0-9]{40}$")
    ci_run_id: int = Field(gt=0)
    build_id: str = Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    image: str = Field(pattern="^" + re.escape(RUNNER_REPOSITORY) + r"@sha256:" + SHA + "$")
    qualification_contract: Literal["hirewiz_schema26_app_rolling_v3"]
    qualification_sha256: str = Field(pattern="^" + SHA + "$")
    app_files: Literal[289]
    app_sha256: Literal["605015f59cf9143810ec25cb9dd4206b3b7e2e592bc9b205b8d58a5b3d2323fe"]
    entry_path: Literal["backend/scripts/native_acquisition_job.py"]
    entry_sha256: Literal["8eef532ef4d4978e9dda000e47ee694b43b55e16a168f93f9c7da724ea9b8740"]
    runtime_dependencies_sha256: str = Field(pattern="^" + SHA + "$")
    observed_at: datetime
    ci_metadata_sha256: str = Field(pattern="^" + SHA + "$")
    build_metadata_sha256: str = Field(pattern="^" + SHA + "$")

    @model_validator(mode="after")
    def timezone(self) -> QualifiedRunner:
        need(self.observed_at.tzinfo is not None, "runner_receipt_time")
        return self


def bound_runner(plan: Plan, raw: bytes) -> QualifiedRunner:
    """Exact raw-byte Plan binding, explicitly not a verification result."""
    plan = checked_plan(plan)
    need(type(raw) is bytes and 0 < len(raw) <= 16384
         and hashlib.sha256(raw).hexdigest() == plan.qualified_runner_receipt_sha256, "runner_receipt_bytes")
    def pairs(rows):
        need(len({key for key, _ in rows}) == len(rows), "duplicate_runner_receipt_key")
        return dict(rows)
    json.loads(raw, object_pairs_hook=pairs)
    receipt = QualifiedRunner.model_validate_json(raw)
    need(receipt.release != HISTORICAL_RELEASE, "failed_historical_runner_ineligible")
    return receipt


class Plan(BaseModel):
    """Operator-captured pins, not a funding permit or provider receipt."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")
    schema_version: Literal["native-quality-isolation-v1"] = VERSION
    purpose: Literal["unreviewed_development_capture_no_model_no_retail"]
    evaluator: Literal["hirewiz-quality-dev@ai-resume-parser-482412.iam.gserviceaccount.com"]
    evaluator_uid: str = Field(pattern=r"^[0-9]{19,21}$")
    bucket: Literal["hirewiz-quality-dev-482412-20261031"]
    bucket_metageneration: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{7,79}$")
    valid_from: datetime
    expires_at: datetime
    controller_sha256: str = Field(pattern="^" + SHA + "$")
    job_name: str = Field(pattern=r"^hirewiz-quality-dev-[a-z0-9-]{8,32}$")
    qualified_runner_receipt_sha256: str = Field(pattern="^" + SHA + "$")
    inspector_service_uid: str = Field(min_length=1, max_length=128)
    inspector_revision_uid: str = Field(min_length=1, max_length=128)
    compiler_service_uid: str = Field(min_length=1, max_length=128)
    compiler_revision_uid: str = Field(min_length=1, max_length=128)
    inspector_image: str = Field(pattern="^" + re.escape(f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/{documents.SERVICE}") + r"@sha256:" + SHA + "$")
    compiler_image: str = Field(pattern="^" + re.escape(tex.REPOSITORY) + r"@sha256:" + SHA + "$")
    inspector_release: str = Field(pattern=r"^[a-f0-9]{40}$")
    compiler_release: str = Field(pattern=r"^[a-f0-9]{40}$")
    funding_evidence_sha256: str = Field(pattern="^" + SHA + "$")
    acquisition_authorization_sha256: str = Field(pattern="^" + SHA + "$")
    bundle_name: str = Field(pattern=r"^input\.[A-Za-z0-9._-]{1,120}\.json$")
    bundle_generation: str = Field(pattern=r"^[1-9][0-9]{0,20}$")
    bundle_sha256: str = Field(pattern="^" + SHA + "$")
    native_dispatch_review_sha256: str = Field(pattern="^" + SHA + "$")

    @model_validator(mode="after")
    def finite(self) -> Plan:
        need(self.valid_from.tzinfo is not None and self.expires_at.tzinfo is not None
             and self.valid_from < self.expires_at <= self.valid_from + timedelta(days=1), "window")
        return self

    @property
    def namespace(self) -> str:
        return f"gs://{self.bucket}/quality-development/{self.experiment_id}/"

    @property
    def condition(self) -> dict[str, str]:
        prefix = f"projects/_/buckets/{self.bucket}/objects/quality-development/{self.experiment_id}/"
        return {"title": "quality-development-" + self.experiment_id,
                "expression": ('resource.type == "storage.googleapis.com/Object" && '
                               f'resource.name.startsWith("{prefix}")')}


def checked_plan(value: Plan) -> Plan:
    need(type(value) is Plan, "operator_plan_type")
    return Plan.model_validate(value)


def check_identity_bucket(plan: Plan, snapshot: dict[str, Any]) -> None:
    plan = checked_plan(plan)
    account = snapshot["evaluator_service_account"]
    need(account.get("name") == f"projects/{PROJECT}/serviceAccounts/{EVALUATOR}"
         and account.get("projectId") == PROJECT and account.get("email") == EVALUATOR
         and account.get("uniqueId") == plan.evaluator_uid
         and account.get("disabled", False) is False, "evaluator_identity")
    keys = snapshot["evaluator_service_account_keys"].get("keys", [])
    need(type(keys) is list and len(keys) <= 100
         and all(type(key) is dict and key.get("keyType") == "SYSTEM_MANAGED" for key in keys), "evaluator_keys")
    member = "serviceAccount:" + EVALUATOR
    for binding in documents._bindings(snapshot["project_iam"], project_conditions=True):
        need(not ({member, "allUsers", "allAuthenticatedUsers"} & set(binding["members"])), "project_grant")
    bucket = snapshot["bucket"]
    iam = bucket.get("iamConfiguration", {})
    need(bucket.get("name") == BUCKET and str(bucket.get("projectNumber")) == PROJECT_NUMBER
         and bucket.get("location") == "US-CENTRAL1"
         and bucket.get("storageClass") == "STANDARD"
         and str(bucket.get("metageneration")) == plan.bucket_metageneration
         and iam.get("uniformBucketLevelAccess", {}).get("enabled") is True
         and iam.get("publicAccessPrevention") == "enforced"
         and not bucket.get("acl") and not bucket.get("defaultObjectAcl"), "private_regional_bucket")
    role = snapshot["object_role"]
    need(role.get("name") == ROLE and role.get("stage") == "GA"
         and role.get("deleted", False) is False
         and type(role.get("includedPermissions")) is list
         and sorted(role["includedPermissions"]) == ["storage.objects.create", "storage.objects.get"], "object_role")
    bindings = documents._bindings(snapshot["bucket_iam"], project_conditions=True)
    related = [row for row in bindings if member in row["members"]]
    need(related == [{"role": ROLE, "members": [member], "condition": plan.condition}], "exact_prefix_grant")
    need(not any({"allUsers", "allAuthenticatedUsers", "serviceAccount:" + documents.UPLOAD_COORDINATOR,
                  "serviceAccount:" + documents.API_INVOKER} & set(row["members"]) for row in bindings), "bucket_other_runtime_grant")


def _service_configuration(snapshot: dict[str, Any], expected: dict[str, Any], module: Any) -> None:
    """Full current configuration check without replacing any native snapshot field."""
    service, revision = snapshot["service"], snapshot["revision"]
    desired = expected["service"]
    need(service.get("uri") == module.ACCEPTED_ORIGIN, "service_origin")
    need(service.get("name") == module.RESOURCE and type(service.get("uid")) is str and service["uid"]
         and service.get("reconciling", False) is False
         and type(service.get("generation")) in (int, str)
         and type(service.get("observedGeneration")) in (int, str)
         and re.fullmatch(r"[1-9][0-9]{0,18}", str(service.get("generation")))
         and str(service["generation"]) == str(service["observedGeneration"])
         and service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED", "service_ready")
    for key in ("launchStage", "ingress", "invokerIamDisabled", "defaultUriDisabled"):
        default = False if key in {"invokerIamDisabled", "defaultUriDisabled"} else None
        need(documents._same(service.get(key, default), desired[key]), "private_service")
    need(not any(service.get(key) for key in ("iapEnabled", "multiRegionSettings", "sshEnabled", "customAudiences", "buildConfig")), "service_extra")
    documents._service_scaling(service.get("scaling"), desired["scaling"])
    documents._configuration(service["template"], desired["template"])
    ready = service.get("latestReadyRevision")
    need(type(ready) is str and re.fullmatch(re.escape(module.RESOURCE + "/revisions/" + module.SERVICE + "-")
         + r"[a-z0-9-]{1,36}", ready), "revision_name")
    need(revision.get("name") == ready and revision.get("service") in (module.RESOURCE, module.SERVICE)
         and type(revision.get("uid")) is str and bool(revision["uid"]), "revision_identity")
    rows = [row for row in revision.get("conditions", []) if row.get("type") == "Ready"]
    need(len(rows) == 1 and rows[0].get("state") == "CONDITION_SUCCEEDED", "revision_ready")
    need(documents._same(service.get("traffic"), desired["traffic"]), "traffic")
    if service.get("trafficStatuses") is not None:
        documents._traffic_statuses(service["trafficStatuses"], ready)
    documents._configuration(revision, desired["template"])
    documents._coordinator_identity(snapshot)
    account = snapshot["service_account"]
    need(account.get("email") == module.SERVICE_ACCOUNT and account.get("disabled", False) is False, "worker_identity")
    keys = snapshot["service_account_keys"].get("keys", [])
    need(type(keys) is list and len(keys) <= 100
         and all(type(row) is dict and row.get("keyType") == "SYSTEM_MANAGED" for row in keys), "worker_keys")
    for row in documents._bindings(snapshot["project_iam"], project_conditions=True):
        need(not ({"allUsers", "allAuthenticatedUsers", "serviceAccount:" + module.SERVICE_ACCOUNT,
                   "serviceAccount:" + documents.UPLOAD_COORDINATOR,
                   "serviceAccount:" + EVALUATOR} & set(row["members"])), "worker_project_grant")


def check_evaluation_workers(plan: Plan, inspector: dict[str, Any], compiler: dict[str, Any]) -> None:
    plan = checked_plan(plan)
    need(Path(documents.__file__).resolve() == ROOT / "infra/gcp/document_worker.py"
         and Path(tex.__file__).resolve() == ROOT / "infra/gcp/native_tex_worker.py", "historical_module_source")
    need(inspector["service"].get("uid") == plan.inspector_service_uid
         and inspector["revision"].get("uid") == plan.inspector_revision_uid
         and compiler["service"].get("uid") == plan.compiler_service_uid
         and compiler["revision"].get("uid") == plan.compiler_revision_uid, "worker_plan_identity")
    expected = documents.render(plan.inspector_image, plan.inspector_release, source_revision=True)
    _service_configuration(inspector, expected, documents)
    expected_tex = tex.render(plan.compiler_image, plan.compiler_release, coordinator_phase=True)
    _service_configuration(compiler, expected_tex, tex)
    wanted = [{"role": "roles/run.invoker", "members": sorted(
        "serviceAccount:" + name for name in (documents.API_INVOKER, documents.UPLOAD_COORDINATOR, EVALUATOR))}]
    bindings, maintenance = documents.service_bindings_without_maintenance(inspector["service_iam"], documents.SERVICE)
    if maintenance:
        documents.validate_maintenance_identity(inspector)
    need(bindings == wanted and documents._bindings(compiler["service_iam"]) == wanted, "exact_three_invokers")


def check_historical_workers(inspector: dict[str, Any], compiler: dict[str, Any], *,
                             inspector_image: str, inspector_release: str,
                             compiler_image: str, compiler_release: str) -> dict[str, Any]:
    """The old exact-two mode is still the original validator, with no edited policy."""
    return {"inspector": documents.check_snapshot(inspector, inspector_image, inspector_release,
            api_phase=True, coordinator_phase=True, source_revision=True),
            "compiler": tex.check_snapshot(compiler, compiler_image, compiler_release,
            runtime_phase=True, coordinator_phase=True)}


def expected_job(plan: Plan, raw_runner: bytes, *, batch: int) -> dict[str, Any]:
    plan = checked_plan(plan)
    runner = bound_runner(plan, raw_runner)
    need(type(batch) is int and 0 <= batch < 9, "batch")
    return {"taskCount": 1, "parallelism": 1, "template": {
        "serviceAccount": EVALUATOR, "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2", "maxRetries": 0, "timeout": "240s" if batch == 0 else "1200s",
        "vpcAccess": {"egress": "PRIVATE_RANGES_ONLY", "networkInterfaces": [
            {"network": "hirewiz-private", "subnetwork": "hirewiz-callers-us-central1"}]},
        "containers": [{"image": runner.image,
            "command": ["/app/backend/.venv/bin/python"],
            "args": ["-B", "-m", "scripts.native_acquisition_job", "--namespace", plan.namespace,
                     "--bundle-name", plan.bundle_name, "--bundle-generation", plan.bundle_generation,
                     "--bundle-sha256", plan.bundle_sha256, "--workspace", WORKSPACE],
            "resources": {"limits": {"cpu": "2", "memory": "4Gi"}}}]}}


def check_job(plan: Plan, raw_runner: bytes, job: dict[str, Any], *, batch: int) -> None:
    plan = checked_plan(plan)
    need(job.get("name") == f"projects/{PROJECT}/locations/{REGION}/jobs/{plan.job_name}"
         and type(job.get("uid")) is str and bool(job["uid"])
         and str(job.get("generation")) == str(job.get("observedGeneration"))
         and re.fullmatch(r"[1-9][0-9]{0,18}", str(job.get("generation")))
         and job.get("reconciling", False) is False
         and job.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED", "job_identity")
    need(documents._same(job.get("template"), expected_job(plan, raw_runner, batch=batch)), "job_template")
    need(not any(job.get(key) for key in ("startExecutionToken", "runExecutionToken", "binaryAuthorization")), "job_extra")


def check_future_bundle(plan: Plan, raw_runner: bytes, raw: bytes, *, batch: int) -> None:
    """Bind exact future metadata; unchanged entry still checks every source byte."""
    plan = checked_plan(plan)
    sys.path.insert(0, str(ROOT / "backend"))
    from scripts.native_acquisition_job import Bundle

    runner = bound_runner(plan, raw_runner)
    need(type(batch) is int and 0 <= batch < 9, "batch")
    need(type(raw) is bytes and 0 < len(raw) <= 256 * 1024
         and hashlib.sha256(raw).hexdigest() == plan.bundle_sha256, "bundle_bytes")
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        need(len({key for key, _ in rows}) == len(rows), "duplicate_bundle_key")
        return dict(rows)
    bundle = Bundle.model_validate(json.loads(raw, object_pairs_hook=pairs))
    need(bundle.job_name == plan.job_name and bundle.entry_sha256 == runner.entry_sha256 and bundle.configuration.namespace == plan.namespace
         and bundle.authorization.experiment_id == plan.experiment_id
         and bundle.authorization.request_limit == 80
         and bundle.authorization.funding_evidence.sha256 == plan.funding_evidence_sha256
         and bundle.dispatch.external_authority_review.sha256 == plan.native_dispatch_review_sha256
         and digest(bundle.authorization.model_dump(mode="json")) == plan.acquisition_authorization_sha256,
         "bundle_authorization")
    need(bundle.authorization.state == "authorized"
         and bundle.authorization.valid_from <= plan.valid_from < plan.expires_at <= bundle.authorization.expires_at
         and bundle.authorization.journal_path == WORKSPACE + "/journal"
         and bundle.authorization.inspector.url == documents.ACCEPTED_ORIGIN + "/inspect"
         and bundle.authorization.inspector.image == plan.inspector_image
         and bundle.authorization.compiler.url == tex.ACCEPTED_ORIGIN + "/compile"
         and bundle.authorization.compiler.image == plan.compiler_image
         and bundle.authorization.compiler.policy == tex.POLICY_SHA256, "bundle_native_bindings")
    from infra.gcp import document_source_candidate as source
    need(bundle.authorization.inspector.policy == source.POLICY_SHA256, "bundle_inspector_policy")
    need(len(bundle.native_files) == 41 and {row.path for row in bundle.native_files}
         >= {"materialization-manifest.json"}, "complete_development_source_count")
    limit = 1 if batch == 0 else 9 if batch == 8 else 10
    need(bundle.dispatch.maximum_requests == limit, "batch_request_limit")


def check(plan: Plan, snapshot: dict[str, Any], *, controller_sha256: str) -> dict[str, Any]:
    plan = checked_plan(plan)
    need(plan.controller_sha256 == controller_sha256, "controller_source")
    need(plan.valid_from <= datetime.now(UTC) < plan.expires_at, "expired_plan")
    try:
        check_identity_bucket(plan, snapshot)
        check_evaluation_workers(plan, snapshot["inspector"], snapshot["compiler"])
    except (KeyError, TypeError, AttributeError, ValueError, OSError):
        raise IsolationDenied("quality_isolation_snapshot_refused") from None
    return {"schema_version": VERSION, "configuration_matches": True, "plan_sha256": digest(plan.model_dump(mode="json")),
            "input_authority": "untrusted_offline_snapshot",
            "runner_authority": "not_verified_by_offline_snapshot", "native_approval": False,
            "effective_iam_proven": False, "processing_authorized": False, "provider_calls": 0,
            "model_calls": 0, "authorizes_retail": False}


def main() -> int:
    """Check private exact-byte artifacts; printed success is NOT native authority."""
    import argparse
    import os
    import stat

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-runner", type=Path, required=True)
    parser.add_argument("--runner-root", type=Path)
    parser.add_argument("--controller-ci-run-id", type=int)
    parser.add_argument("--verify-runner", action="store_true")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--snapshot-sha256", required=True)
    args = parser.parse_args()
    try:
        def read(path: Path, sha256: str) -> bytes:
            need(path.is_absolute() and path.resolve() == path and not path.is_symlink()
                 and re.fullmatch(SHA, sha256), "private_input")
            st = path.stat()
            need(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()
                 and stat.S_IMODE(st.st_mode) == 0o600 and 0 < st.st_size <= 4 * 1024 * 1024, "private_input")
            raw = path.read_bytes()
            need(hashlib.sha256(raw).hexdigest() == sha256, "input_changed")
            return raw
        def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
            need(len({key for key, _ in rows}) == len(rows), "duplicate_input_key")
            return dict(rows)
        raw_plan = read(args.plan, args.plan_sha256)
        json.loads(raw_plan, object_pairs_hook=pairs)
        plan = Plan.model_validate_json(raw_plan)
        snapshot = json.loads(read(args.snapshot, args.snapshot_sha256), object_pairs_hook=pairs)
        sys.path.insert(0, str(ROOT / "backend"))
        from scripts import qualify_native_quality_acquisition as source
        need(not source.git(ROOT, "status", "--porcelain"), "clean_controller_required")
        identity = source.source_identity(ROOT)
        raw_runner = read(args.qualified_runner, plan.qualified_runner_receipt_sha256)
        bound_runner(plan, raw_runner)
        result = check(plan, snapshot, controller_sha256=identity["controller_sha256"])
        if args.verify_runner:
            need(args.runner_root is not None and args.controller_ci_run_id is not None, "runner_verification_arguments")
            result["runner_observation"] = source.verify_qualified_runner(plan, raw_runner,
                runner_root=args.runner_root, controller_ci_run_id=args.controller_ci_run_id)
        else:
            need(args.runner_root is None and args.controller_ci_run_id is None, "unexpected_runner_verification_arguments")
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"state": "quality_isolation_refused", "native_approval": False,
                          "processing_authorized": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
