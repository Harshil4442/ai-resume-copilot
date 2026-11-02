"""Offline V2 source, input-retention and isolation contracts; no native authority.

These checks create no account, object, Job, grant, receipt or funding approval.
In particular, an exact 8Gi Job template is not measured process/tmpfs safety.
"""
from __future__ import annotations

import hashlib
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from evaluation.native_acquisition_v2 import channel as c
from evaluation.native_acquisition_v2 import engine as e
from evaluation.native_acquisition_v2 import job as j

ROOT = Path(__file__).resolve().parents[3]
PROJECT = j.PROJECT
REGION = "us-central1"
PROJECT_NUMBER = "157225724590"
EVALUATOR = f"hirewiz-quality-dev@{PROJECT}.iam.gserviceaccount.com"
BUCKET = "hirewiz-quality-dev-482412-20261031"
ROLE = f"projects/{PROJECT}/roles/hirewizQualityDevelopmentObjects"
RUNNER_REPOSITORY = f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/hirewiz"
VERSION: Literal["held-out-native-isolation-v2"] = "held-out-native-isolation-v2"
ENTRY = "backend/evaluation/native_acquisition_v2/job.py"
SHA = r"[a-f0-9]{64}"
MAX_COMMON_FILES = 1837
MAX_INPUT_OBJECTS = MAX_COMMON_FILES + 66
RESERVED_VARIABLE_INPUT_BYTES = 33 * (e.MAX_BUNDLE_BYTES + e.MAX_METADATA)


class IsolationDenied(ValueError):
    pass


def need(value: object, code: str) -> None:
    if not value:
        raise IsolationDenied("held_out_isolation_" + code)


def digest(value: Any) -> str:
    return e.packet.digest(e.packet.canonical(value))


class Closed(e.Closed):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


class QualifiedRunner(Closed):
    """Untrusted retained data until the separate fresh verifier succeeds."""
    schema_version: Literal["held-out-native-qualified-runner-v2"]
    repository: Literal["https://github.com/Harshil4442/ai-resume-copilot.git"]
    release: str = Field(pattern=r"^[a-f0-9]{40}$")
    ci_run_id: int = Field(gt=0)
    build_id: str = Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    image: str = Field(pattern="^" + re.escape(RUNNER_REPOSITORY) + r"@sha256:" + SHA + "$")
    qualification_contract: Literal["hirewiz_schema26_app_rolling_v3"]
    qualification_sha256: str = Field(pattern="^" + SHA + "$")
    app_files: int = Field(gt=0, le=10000)
    app_sha256: str = Field(pattern="^" + SHA + "$")
    entry_path: Literal["backend/evaluation/native_acquisition_v2/job.py"]
    entry_sha256: str = Field(pattern="^" + SHA + "$")
    runtime_dependencies_sha256: str = Field(pattern="^" + SHA + "$")
    proof_inputs_sha256: str = Field(pattern="^" + SHA + "$")
    component_sha256: str = Field(pattern="^" + SHA + "$")
    observed_at: datetime
    ci_metadata_sha256: str = Field(pattern="^" + SHA + "$")
    build_metadata_sha256: str = Field(pattern="^" + SHA + "$")

    @model_validator(mode="after")
    def aware(self) -> QualifiedRunner:
        need(self.observed_at.tzinfo is not None, "runner_receipt_time")
        return self


class Plan(Closed):
    schema_version: Literal["held-out-native-isolation-v2"] = VERSION
    purpose: Literal["unreviewed_held_out_capture_no_model_no_retail"]
    evaluator: Literal["hirewiz-quality-dev@ai-resume-parser-482412.iam.gserviceaccount.com"]
    evaluator_uid: str = Field(pattern=r"^[0-9]{19,21}$")
    bucket: Literal["hirewiz-quality-dev-482412-20261031"]
    bucket_metageneration: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{7,79}$")
    valid_from: datetime
    expires_at: datetime
    controller_sha256: str = Field(pattern="^" + SHA + "$")
    job_name: str = Field(pattern=r"^hirewiz-quality-held-out-[a-z0-9-]{8,32}$")
    qualified_runner_receipt_sha256: str = Field(pattern="^" + SHA + "$")
    inspector_service_uid: str = Field(min_length=1, max_length=128)
    inspector_revision_uid: str = Field(min_length=1, max_length=128)
    compiler_service_uid: str = Field(min_length=1, max_length=128)
    compiler_revision_uid: str = Field(min_length=1, max_length=128)
    inspector_image: str = Field(pattern=r"^us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:" + SHA + "$")
    compiler_image: str = Field(pattern=r"^us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-native-tex@sha256:" + SHA + "$")
    inspector_release: str = Field(pattern=r"^[a-f0-9]{40}$")
    compiler_release: str = Field(pattern=r"^[a-f0-9]{40}$")
    funding_evidence_sha256: str = Field(pattern="^" + SHA + "$")
    acquisition_authorization_sha256: str = Field(pattern="^" + SHA + "$")
    native_development_lock_sha256: str = Field(pattern="^" + SHA + "$")
    native_dispatch_review_sha256: str = Field(pattern="^" + SHA + "$")
    input_catalog_sha256: str = Field(pattern="^" + SHA + "$")
    input_budget_sha256: str = Field(pattern="^" + SHA + "$")
    bundle_name: str = Field(pattern=r"^input\.bundle\.(?:[0-2][0-9]|3[0-2])\.json$")
    bundle_generation: str = Field(pattern=r"^[1-9][0-9]{0,20}$")
    bundle_sha256: str = Field(pattern="^" + SHA + "$")

    @model_validator(mode="after")
    def finite(self) -> Plan:
        need(all(value.tzinfo is not None for value in (self.valid_from, self.expires_at))
             and self.valid_from < self.expires_at <= self.valid_from + timedelta(days=1), "window")
        return self

    @property
    def namespace(self) -> str:
        return f"gs://{self.bucket}/quality-held-out/{self.experiment_id}/"

    @property
    def condition(self) -> dict[str, str]:
        prefix = f"projects/_/buckets/{self.bucket}/objects/quality-held-out/{self.experiment_id}/"
        start, end = (value.astimezone(UTC).isoformat().replace("+00:00", "Z")
                      for value in (self.valid_from, self.expires_at))
        return {"title": "quality-held-out-" + self.experiment_id,
            "expression": ('resource.type == "storage.googleapis.com/Object" && '
                f'resource.name.startsWith("{prefix}") && request.time >= timestamp("{start}") && '
                f'request.time < timestamp("{end}")')}


def checked_plan(value: Plan) -> Plan:
    need(type(value) is Plan, "plan_type")
    return Plan.model_validate(value)


def bound_runner(plan: Plan, raw: bytes) -> QualifiedRunner:
    plan = checked_plan(plan)
    need(type(raw) is bytes and 0 < len(raw) <= 16 * 1024
         and hashlib.sha256(raw).hexdigest() == plan.qualified_runner_receipt_sha256, "runner_receipt_bytes")
    e._json(raw)
    return QualifiedRunner.model_validate_json(raw)


class CommonInput(Closed):
    group: Literal["native", "authority"]
    path: str = Field(pattern="^" + e.PATH + "$", max_length=512)
    kind: str = Field(min_length=1, max_length=32)
    object: c.ObjectRef

    @model_validator(mode="after")
    def finite(self) -> CommonInput:
        if self.group == "native":
            maximum = e.MAX_METADATA if self.path == "materialization-manifest.json" else e.inspection.MAX_SOURCE_BYTES
            need(self.kind == "source" and self.object.size <= maximum, "catalog_native_bound")
        else:
            need(self.kind in e.KINDS and self.object.size <= e.KINDS[self.kind][1], "catalog_authority_bound")
        return self


class InputCatalog(Closed):
    """One mirrored input union; origin references remain inside exact raw bytes."""
    schema_version: Literal["held-out-native-input-catalog-v2"]
    provenance: Literal["mirrored_bytes_original_references_retained"]
    namespace: str = Field(pattern=r"^gs://[a-z0-9][a-z0-9._-]{2,221}/quality-held-out/[a-z0-9][a-z0-9-]{7,79}/$")
    native_development_lock_sha256: str = Field(pattern="^" + SHA + "$")
    common_files: list[CommonInput] = Field(min_length=162, max_length=MAX_COMMON_FILES)

    @model_validator(mode="after")
    def finite(self) -> InputCatalog:
        rows = self.common_files
        keys = [(row.group, row.path) for row in rows]
        need(keys == sorted(keys) and len(set(keys)) == len(keys), "catalog_order_or_alias")
        need(all(row.object.name == f"input.common.{index:04}" for index, row in enumerate(rows)), "catalog_fixed_names")
        for group in ("native", "authority"):
            paths = [row.path for row in rows if row.group == group]
            need(not any(b.startswith(a + "/") for a in paths for b in paths), "catalog_path_collision")
        native = [row for row in rows if row.group == "native"]
        authority = [row for row in rows if row.group == "authority"]
        need(len(native) == 161 and sum(row.path == "materialization-manifest.json" for row in native) == 1,
             "catalog_complete_native_files")
        for kind, (maximum, _) in e.KINDS.items():
            need(sum(row.kind == kind for row in authority) <= maximum - (kind == "control"), "catalog_authority_count")
        need(sum(row.object.size for row in rows) + RESERVED_VARIABLE_INPUT_BYTES <= e.MAX_INPUT_BYTES,
             "experiment_input_union_bound")
        return self


class InputSlot(Closed):
    batch_index: int = Field(ge=0, le=32)
    object: c.ObjectRef


class InputBudget(Closed):
    schema_version: Literal["held-out-native-input-budget-v2"]
    catalog_sha256: str = Field(pattern="^" + SHA + "$")
    reviews: list[InputSlot] = Field(max_length=33)
    bundles: list[InputSlot] = Field(max_length=33)
    unknown_slots: list[str] = Field(max_length=MAX_INPUT_OBJECTS)

    @model_validator(mode="after")
    def finite(self) -> InputBudget:
        for kind, rows, maximum in (("review", self.reviews, e.MAX_METADATA), ("bundle", self.bundles, e.MAX_BUNDLE_BYTES)):
            indices = [row.batch_index for row in rows]
            need(indices == sorted(set(indices)), "input_slot_order_or_duplicate")
            need(all(row.object.name == f"input.{kind}.{row.batch_index:02}.json"
                     and row.object.size <= maximum for row in rows), "input_slot_name_or_bound")
        known = {row.object.name for row in [*self.reviews, *self.bundles]}
        need(len(set(self.unknown_slots)) == len(self.unknown_slots) and not known.intersection(self.unknown_slots),
             "unknown_slot_collision")
        need(all(re.fullmatch(r"input\.(?:common\.[0-9]{4}|(?:review|bundle)\.(?:[0-2][0-9]|3[0-2])\.json)", name)
                 for name in self.unknown_slots), "unknown_slot_name")
        return self


def check_input_budget(catalog: InputCatalog, budget: InputBudget, *, previous: InputBudget | None = None) -> dict[str, Any]:
    """Pure metadata accounting. No upload, replay, expense release or custody proof."""
    catalog, budget = InputCatalog.model_validate(catalog), InputBudget.model_validate(budget)
    need(budget.catalog_sha256 == digest(catalog.model_dump(mode="json")), "input_catalog_changed")
    common_names = {row.object.name for row in catalog.common_files}
    need(all(not name.startswith("input.common.") or name in common_names for name in budget.unknown_slots), "unknown_common_slot")
    if previous is not None:
        previous = InputBudget.model_validate(previous)
        need(previous.catalog_sha256 == budget.catalog_sha256 and set(previous.unknown_slots) <= set(budget.unknown_slots),
             "input_history_or_unknown_changed")
        for old, current in ((previous.reviews, budget.reviews), (previous.bundles, budget.bundles)):
            now = {row.batch_index: row.object for row in current}
            need(all(now.get(row.batch_index) == row.object for row in old), "input_generation_changed")
    return {"common_objects": len(catalog.common_files), "maximum_input_objects": MAX_INPUT_OBJECTS,
        "maximum_retained_input_bytes": sum(row.object.size for row in catalog.common_files) + RESERVED_VARIABLE_INPUT_BYTES,
        "maximum_logical_input_read_bytes": 33 * e.MAX_INPUT_BYTES,
        "input_bound_authority": "closed_catalog_metadata_only",
        "input_upload_enforcement_proven": False,
        "conservative_unenforced_retention_bytes": 33 * e.MAX_INPUT_BYTES,
        "state": "unknown_no_replay" if budget.unknown_slots else "bounded_unreviewed_metadata",
        "native_approval": False, "processing_authorized": False, "reserved_expense_released": False,
        "memory_qualified": False, "authorizes_retail": False}


def _ports():
    """Only unchanged neutral snapshot helpers; no V1 Plan or grant conversion."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from infra.gcp import document_source_candidate as source
    from infra.gcp import native_quality_acquisition_isolation as old
    for module, path in ((old, "infra/gcp/native_quality_acquisition_isolation.py"),
            (old.documents, "infra/gcp/document_worker.py"), (old.tex, "infra/gcp/native_tex_worker.py"),
            (source, "infra/gcp/document_source_candidate.py")):
        need(Path(module.__file__).resolve() == ROOT / path, "foreign_snapshot_helper")
    need(old.EVALUATOR == EVALUATOR, "neutral_evaluator_semantics")
    return old, old.documents, old.tex, source


def check_identity_bucket(plan: Plan, snapshot: dict[str, Any]) -> None:
    plan = checked_plan(plan)
    _, documents, _, _ = _ports()
    account = snapshot["evaluator_service_account"]
    need(account.get("name") == f"projects/{PROJECT}/serviceAccounts/{EVALUATOR}"
         and account.get("projectId") == PROJECT and account.get("email") == EVALUATOR
         and account.get("uniqueId") == plan.evaluator_uid and account.get("disabled", False) is False, "evaluator_identity")
    keys = snapshot["evaluator_service_account_keys"].get("keys", [])
    need(type(keys) is list and len(keys) <= 100 and all(type(key) is dict and key.get("keyType") == "SYSTEM_MANAGED" for key in keys), "evaluator_keys")
    member = "serviceAccount:" + EVALUATOR
    for row in documents._bindings(snapshot["project_iam"], project_conditions=True):
        need(not ({member, "allUsers", "allAuthenticatedUsers"} & set(row["members"])), "project_grant")
    bucket = snapshot["bucket"]
    iam = bucket.get("iamConfiguration", {})
    need(bucket.get("name") == BUCKET and str(bucket.get("projectNumber")) == PROJECT_NUMBER
         and bucket.get("location") == "US-CENTRAL1" and bucket.get("storageClass") == "STANDARD"
         and str(bucket.get("metageneration")) == plan.bucket_metageneration
         and iam.get("uniformBucketLevelAccess", {}).get("enabled") is True
         and iam.get("publicAccessPrevention") == "enforced" and not bucket.get("acl") and not bucket.get("defaultObjectAcl"), "private_bucket")
    role = snapshot["object_role"]
    need(role.get("name") == ROLE and role.get("stage") == "GA" and role.get("deleted", False) is False
         and type(role.get("includedPermissions")) is list
         and sorted(role["includedPermissions"]) == ["storage.objects.create", "storage.objects.get"], "object_role")
    bindings = documents._bindings(snapshot["bucket_iam"], project_conditions=True)
    need([row for row in bindings if member in row["members"]]
         == [{"role": ROLE, "members": [member], "condition": plan.condition}], "exact_held_out_prefix_window_grant")
    need(not any({"allUsers", "allAuthenticatedUsers", "serviceAccount:" + documents.UPLOAD_COORDINATOR,
                  "serviceAccount:" + documents.API_INVOKER} & set(row["members"]) for row in bindings), "bucket_other_runtime_grant")


def check_evaluation_workers(plan: Plan, inspector: dict[str, Any], compiler: dict[str, Any]) -> None:
    plan = checked_plan(plan)
    old, documents, tex, _ = _ports()
    need(inspector["service"].get("uid") == plan.inspector_service_uid
         and inspector["revision"].get("uid") == plan.inspector_revision_uid
         and compiler["service"].get("uid") == plan.compiler_service_uid
         and compiler["revision"].get("uid") == plan.compiler_revision_uid, "worker_identity")
    old._service_configuration(inspector, documents.render(plan.inspector_image, plan.inspector_release, source_revision=True), documents)
    old._service_configuration(compiler, tex.render(plan.compiler_image, plan.compiler_release, coordinator_phase=True), tex)
    wanted = [{"role": "roles/run.invoker", "members": sorted("serviceAccount:" + name
        for name in (documents.API_INVOKER, documents.UPLOAD_COORDINATOR, EVALUATOR))}]
    bindings, maintenance = documents.service_bindings_without_maintenance(inspector["service_iam"], documents.SERVICE)
    if maintenance:
        documents.validate_maintenance_identity(inspector)
    need(bindings == wanted and documents._bindings(compiler["service_iam"]) == wanted, "exact_worker_invokers")


def expected_job(plan: Plan, raw_runner: bytes, *, batch: int) -> dict[str, Any]:
    plan, runner = checked_plan(plan), bound_runner(plan, raw_runner)
    c.batch_limit(batch)
    need(plan.bundle_name == f"input.bundle.{batch:02}.json", "batch_bundle_slot")
    return {"taskCount": 1, "parallelism": 1, "template": {
        "serviceAccount": EVALUATOR, "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2", "maxRetries": 0,
        "timeout": "240s" if batch == 0 else "1200s",
        "vpcAccess": {"egress": "PRIVATE_RANGES_ONLY", "networkInterfaces": [
            {"network": "hirewiz-private", "subnetwork": "hirewiz-callers-us-central1"}]},
        "containers": [{"image": runner.image, "command": ["/app/backend/.venv/bin/python"],
            "args": ["-I", "-B", "/app/" + ENTRY, "--namespace", plan.namespace,
                "--bundle-name", plan.bundle_name, "--bundle-generation", plan.bundle_generation,
                "--bundle-sha256", plan.bundle_sha256, "--workspace", str(j.WORKSPACE)],
            "env": [{"name": j.NO_BUCKET_METADATA, "value": "true"}],
            "resources": {"limits": {"cpu": "2", "memory": "8Gi"}}}]}}


def check_job(plan: Plan, raw_runner: bytes, value: dict[str, Any], *, batch: int) -> None:
    plan = checked_plan(plan)
    _, documents, _, _ = _ports()
    need(value.get("name") == f"projects/{PROJECT}/locations/{REGION}/jobs/{plan.job_name}"
         and type(value.get("uid")) is str and bool(value["uid"])
         and re.fullmatch(r"[1-9][0-9]{0,18}", str(value.get("generation")))
         and str(value["generation"]) == str(value.get("observedGeneration"))
         and value.get("reconciling", False) is False
         and value.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED", "job_identity")
    need(documents._same(value.get("template"), expected_job(plan, raw_runner, batch=batch)), "job_template")
    need(not any(value.get(key) for key in ("startExecutionToken", "runExecutionToken", "binaryAuthorization"))
         and not any(key in value for key in ("overrides", "runOverrides", "containerOverrides")), "job_extra")


def check_future_bundle(plan: Plan, raw_runner: bytes, raw: bytes, *, batch: int,
                        catalog: InputCatalog, budget: InputBudget) -> None:
    plan, runner = checked_plan(plan), bound_runner(plan, raw_runner)
    c.batch_limit(batch)
    need(type(raw) is bytes and 0 < len(raw) <= e.MAX_BUNDLE_BYTES
         and hashlib.sha256(raw).hexdigest() == plan.bundle_sha256, "bundle_bytes")
    bundle = j.Bundle.model_validate(e._json(raw))
    _, documents, tex, source = _ports()
    need(digest(catalog.model_dump(mode="json")) == plan.input_catalog_sha256
         and digest(budget.model_dump(mode="json")) == plan.input_budget_sha256, "input_union_plan")
    check_input_budget(catalog, budget)
    need(catalog.namespace == plan.namespace and catalog.native_development_lock_sha256 == plan.native_development_lock_sha256,
         "catalog_origin_or_namespace")
    review = next((row.object for row in budget.reviews if row.batch_index == batch), None)
    stored = next((row.object for row in budget.bundles if row.batch_index == batch), None)
    need(not budget.unknown_slots and review is not None and stored is not None
         and stored.name == plan.bundle_name == f"input.bundle.{batch:02}.json"
         and stored.generation == plan.bundle_generation and stored.sha256 == plan.bundle_sha256
         and stored.size == len(raw) and review.sha256 == plan.native_dispatch_review_sha256, "input_union_dispatch")
    assert review is not None
    need(bundle.job_name == plan.job_name and bundle.entry_sha256 == runner.entry_sha256
         and bundle.configuration.namespace == plan.namespace and bundle.authorization.experiment_id == plan.experiment_id
         and bundle.configuration.engine_source_sha256 == e.packet.file_digest(Path(e.__file__))
         and bundle.configuration.channel_source_sha256 == e.packet.file_digest(Path(c.__file__))
         and digest(bundle.authorization.model_dump(mode="json")) == plan.acquisition_authorization_sha256
         and bundle.authorization.native_development_lock.sha256 == plan.native_development_lock_sha256
         and bundle.authorization.funding_evidence.sha256 == plan.funding_evidence_sha256
         and bundle.dispatch.batch_index == batch and bundle.dispatch.maximum_requests == c.batch_limit(batch)
         and bundle.dispatch.external_authority_review.sha256 == plan.native_dispatch_review_sha256, "bundle_authorization")
    auth = bundle.authorization
    need(auth.state == "authorized" and auth.valid_from <= plan.valid_from < plan.expires_at <= auth.expires_at
         and auth.journal_path == str(j.WORKSPACE / "journal") and auth.request_limit == 320
         and auth.inspector.url == documents.ACCEPTED_ORIGIN + "/inspect" and auth.inspector.image == plan.inspector_image
         and auth.inspector.policy == source.POLICY_SHA256
         and auth.compiler.url == tex.ACCEPTED_ORIGIN + "/compile" and auth.compiler.image == plan.compiler_image
         and auth.compiler.policy == tex.POLICY_SHA256, "bundle_native_bindings")
    review_path = bundle.dispatch.external_authority_review.path
    shared = {(row.group, row.path): row for row in catalog.common_files}
    need(("authority", review_path) not in shared, "review_must_not_alias_common")
    for artifact in (auth.native_development_lock, auth.funding_evidence, bundle.runner_funding_evidence):
        retained = shared.get(("authority", artifact.path))
        need(retained is not None and retained.kind == "control" and retained.object.sha256 == artifact.sha256,
             "funding_or_lock_mirror_reference")
    actual = {("native", row.path): ("source", row.object) for row in bundle.native_files}
    for row in bundle.authority_files:
        if row.path == review_path:
            need(row.kind == "control" and row.object == review, "review_mirror_reference")
        else:
            actual[("authority", row.path)] = row.kind, row.object
    need(set(actual) == set(shared) and all(actual[key] == (row.kind, row.object) for key, row in shared.items()),
         "exact_shared_input_generations")
    need(sum(row.path == review_path for row in bundle.authority_files) == 1, "review_mirror_membership")


def check(plan: Plan, snapshot: dict[str, Any], *, controller_sha256: str) -> dict[str, Any]:
    plan = checked_plan(plan)
    need(plan.controller_sha256 == controller_sha256, "controller_source")
    need(plan.valid_from <= datetime.now(UTC) < plan.expires_at, "expired_plan")
    try:
        check_identity_bucket(plan, snapshot)
        check_evaluation_workers(plan, snapshot["inspector"], snapshot["compiler"])
    except (KeyError, TypeError, AttributeError, ValueError, OSError):
        raise IsolationDenied("held_out_isolation_snapshot_refused") from None
    return {"schema_version": VERSION, "configuration_matches": True,
        "plan_sha256": digest(plan.model_dump(mode="json")), "input_authority": "untrusted_offline_snapshot",
        "runner_authority": "not_verified_by_offline_snapshot", "effective_iam_proven": False,
        "native_approval": False, "processing_authorized": False, "memory_qualified": False,
        "provider_calls": 0, "model_calls": 0, "authorizes_retail": False}
