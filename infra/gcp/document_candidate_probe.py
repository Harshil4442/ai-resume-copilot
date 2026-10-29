"""Prepare and freshly verify V3 synthetic document Jobs; never promote services.

The operator submits the generated, exact Job request once and runs it once.
This adapter obtains no mutation credentials: all native observations use the
existing retained-manager read-only transport. V2 contracts remain unchanged.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infra.gcp import document_candidate as candidate  # noqa: E402
from infra.gcp import document_candidate_payload as payload  # noqa: E402
from infra.gcp import document_candidate_receipt as receipt  # noqa: E402
from infra.gcp import document_refresh_probe as prior  # noqa: E402

MODES = {"inventory", "corpus"}
PLACEHOLDER = prior.REPOSITORY + "@sha256:" + "0" * 64


def need(value: Any, code: str) -> None:
    prior.need(value, "document_candidate_" + code)


def sha(value: Any) -> str:
    return hashlib.sha256(prior.canonical(value)).hexdigest()


def rendered_payload(image: str) -> str:
    need(type(image) is str and prior.IMAGE.fullmatch(image) and image != PLACEHOLDER,
         "immutable_image_required")
    source = (ROOT / "infra/gcp/document_candidate_payload.py").read_text()
    marker = "IMMUTABLE_IMAGE = " + repr(PLACEHOLDER)
    need(source.count(marker) == 1, "image_literal_changed")
    rendered = source.replace(marker, "IMMUTABLE_IMAGE = " + repr(image))
    original, updated = ast.parse(source), ast.parse(rendered)
    values = [n for n in updated.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "IMMUTABLE_IMAGE" for t in n.targets)]
    need(len(values) == 1, "payload_shape_changed")
    values[0].value = ast.Constant(value=PLACEHOLDER)
    need(ast.dump(original) == ast.dump(updated), "payload_body_changed")
    return rendered


def source_contract() -> dict[str, str]:
    from infra.gcp.document_source_candidate import frozen_v3

    raw, frozen = frozen_v3(ROOT)
    hashes, policy = frozen["source_sha256"], hashlib.sha256(raw).hexdigest()
    runtime = {destination: hashes[source] for source, destination in candidate.RUNTIME.items()}
    need(policy == candidate.POLICY_SHA256 == payload.POLICY_SHA256
         and runtime == payload.EXPECTED_RUNTIME_HASHES, "runtime_sources_changed")
    return runtime


def desired(image: str, nonce: str, mode: str) -> dict[str, Any]:
    need(mode in MODES and re.fullmatch(r"[a-f0-9]{32}", nonce), "mode_or_nonce_invalid")
    task = {"serviceAccount": prior.ACCOUNT, "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2",
            "maxRetries": 0, "timeout": "600s", "containers": [{
                "name": "document-native-probe", "image": image, "sandboxLauncher": True,
                "command": ["/usr/local/bin/python3"],
                "args": ["-I", "-c", rendered_payload(image)] + (["--inventory"] if mode == "inventory" else []),
                "resources": {"limits": {"cpu": "2", "memory": "4Gi"}},
                "env": [{"name": "DOCUMENT_WORKER_IMAGE_DIGEST", "value": image},
                        {"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": candidate.POLICY_SHA256}]}]}
    return {"name": prior.PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12], "launchStage": "BETA",
            "labels": {"hirewiz-probe": "document-candidate-" + mode, "hirewiz-owned": nonce[:12]},
            "template": {"taskCount": 1, "parallelism": 1, "template": task}}


def v1_job(body: dict[str, Any]) -> dict[str, Any]:
    """Equivalent create-only operator input for stock gcloud jobs replace.

    Only use after native absence is established, with an exclusive create intent.
    An uncertain result must be reconciled, never resubmitted with replace.
    """
    task = body["template"]["template"]
    return {"apiVersion": "run.googleapis.com/v1", "kind": "Job",
            "metadata": {"name": body["name"].rsplit("/", 1)[-1], "namespace": prior.PROJECT,
                         "labels": body["labels"], "annotations": {"run.googleapis.com/launch-stage": "BETA"}},
            "spec": {"template": {"metadata": {"annotations": {"run.googleapis.com/execution-environment": "gen2"}},
                                   "spec": {"taskCount": 1, "parallelism": 1, "template": {"spec": {
                                       "serviceAccountName": task["serviceAccount"], "timeoutSeconds": 600,
                                       "maxRetries": 0, "containers": task["containers"]}}}}}}


def validate_job(job: Any, plan: dict[str, Any]) -> None:
    wanted = desired(plan["image"], plan["job_nonce"], plan["mode"])
    validate_job_template(job, wanted)


def validate_job_template(job: Any, wanted: dict[str, Any]) -> None:
    """Common exact native Job guard; source profiles only supply a typed desired template."""
    need(type(job) is dict and job.get("name") == wanted["name"]
         and type(job.get("uid")) is str and prior.UUID.fullmatch(job["uid"])
         and job.get("launchStage") == "BETA" and job.get("labels") == wanted["labels"]
         and prior.integer(job.get("generation"), 1) and prior.integer(job.get("observedGeneration"), 1)
         and type(job.get("etag")) is str and bool(job["etag"])
         and job.get("reconciling", False) is False
         and job.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
         and type(job.get("template")) is dict
         and prior.integer(job["template"].get("taskCount"), 1)
         and prior.integer(job["template"].get("parallelism"), 1)
         and prior.task_matches(job["template"].get("template"), wanted["template"]["template"]),
         "job_unverified")


def validate_execution(row: Any, job: dict[str, Any], plan: dict[str, Any]) -> None:
    wanted = desired(plan["image"], plan["job_nonce"], plan["mode"])
    validate_execution_template(row, job, wanted)


def validate_execution_template(row: Any, job: dict[str, Any], wanted: dict[str, Any]) -> None:
    prefix = job["name"] + "/executions/"
    need(type(row) is dict and type(row.get("name")) is str and row["name"].startswith(prefix)
         and re.fullmatch(re.escape(job["name"].rsplit("/", 1)[-1]) + r"-[a-z0-9]{1,20}", row["name"].rsplit("/", 1)[-1])
         and type(row.get("uid")) is str and prior.UUID.fullmatch(row["uid"])
         and row.get("job") in {job["name"], job["name"].rsplit("/", 1)[-1]}
         and row.get("reconciling", False) is False
         and prior.integer(row.get("taskCount"), 1) and prior.integer(row.get("succeededCount"), 1)
         and all(prior.integer(row.get(k, 0), 0) for k in ("failedCount", "cancelledCount", "runningCount", "retriedCount"))
         and row.get("completionTime") and row.get("startTime")
         and prior.task_matches(row.get("template"), wanted["template"]["template"])
         and prior.integer(job.get("executionCount"), 1)
         and job.get("latestCreatedExecution", {}).get("name") in {row["name"], row["name"].rsplit("/", 1)[-1]},
         "execution_not_singleton_success")


def native_payload(clients: Any, job: dict[str, Any], execution: dict[str, Any], mode: str, *, event: str | None = None) -> dict[str, Any]:
    allowed = {"hirewiz_document_candidate_inventory_v1", "hirewiz_document_candidate_required_v3",
               "hirewiz_document_source_inventory_v1", "hirewiz_document_source_required_v4"}
    event = event or ("hirewiz_document_candidate_inventory_v1" if mode == "inventory" else "hirewiz_document_candidate_required_v3")
    need(event in allowed, "event_invalid")
    job_id, execution_id = job["name"].rsplit("/", 1)[-1], execution["name"].rsplit("/", 1)[-1]
    begin, end = prior.timestamp(job["createTime"]), prior.timestamp(execution["completionTime"])
    query = (f'resource.type="cloud_run_job" AND resource.labels.project_id="{prior.PROJECT}" '
             f'AND resource.labels.location="{prior.REGION}" AND resource.labels.job_name="{job_id}" '
             f'AND labels."run.googleapis.com/execution_name"="{execution_id}" '
             f'AND timestamp>="{job["createTime"]}" AND timestamp<="{execution["completionTime"]}" '
             f'AND jsonPayload.event="{event}"')
    token, found, seen = None, None, set()
    for _ in range(10):
        response = clients.entries(query, token)
        rows = response.get("entries", [])
        need(type(rows) is list and len(rows) <= 20, "log_bound")
        for row in rows:
            need(type(row) is dict and row.get("resource") == {"type": "cloud_run_job", "labels": {
                "project_id": prior.PROJECT, "location": prior.REGION, "job_name": job_id}}
                 and row.get("labels", {}).get("run.googleapis.com/execution_name") == execution_id
                 and row.get("logName") == "projects/" + prior.PROJECT + "/logs/run.googleapis.com%2Fstdout"
                 and begin <= prior.timestamp(row.get("timestamp")) <= end
                 and type(row.get("jsonPayload")) is dict and len(prior.canonical(row["jsonPayload"])) <= 16384
                 and row["jsonPayload"].get("execution_name") == execution_id
                 and row["jsonPayload"].get("event") == event and found is None, "log_scope_or_uniqueness")
            found = row["jsonPayload"]
        token = response.get("nextPageToken")
        if token in (None, ""):
            need(found is not None, "event_missing")
            assert isinstance(found, dict)
            return found
        need(type(token) is str and 0 < len(token) <= 8192 and token not in seen
             and all(33 <= ord(c) <= 126 for c in token), "pagination_invalid")
        seen.add(token)
    raise prior.NativeProofDenied("document_candidate_logs_incomplete")


def validate_inventory(value: Any, image: str, runtime: dict[str, str], *,
                       policy: str = candidate.POLICY_SHA256, event: str = "hirewiz_document_candidate_inventory_v1") -> dict[str, str]:
    need(type(value) is dict and set(value) == receipt.TOP
         and value.get("event") == event
         and type(value.get("version")) is int and value["version"] == 1
         and value.get("worker_image") == image and value.get("policy_sha256") == policy
         and value.get("activation_flags") == {key: False for key in receipt.ACTIVATION}
         and all(value["activation_flags"][key] is False for key in receipt.ACTIVATION)
         and value.get("all_required_passed") is False and value.get("cases") == []
         and value.get("failure_stage") is None and value.get("outer_runtime_user_verified") is True
         and receipt.integer(value.get("elapsed_ms"), 0, 600000), "inventory_shape")
    identity = value.get("identity_preflight")
    need(type(identity) is dict and set(identity) == receipt.IDENTITY
         and identity.get("passed") is True and identity.get("cleanup_passed") is True
         and identity.get("runtime_hashes") == runtime and identity.get("architecture") == "x86_64"
         and identity.get("readonly_font_manifest") is True and identity.get("font_manifest_process_writable") is False
         and identity.get("courier_bundle_verified") is True
         and receipt.integer(identity.get("runtime_uid"), 0, 4294967295)
         and receipt.integer(identity.get("runtime_gid"), 0, 4294967295)
         and type(identity.get("renderer_version")) is str and len(identity["renderer_version"]) <= 200
         and receipt.VERSION.fullmatch(identity["renderer_version"])
         and receipt.hashed(identity.get("font_manifest_sha256")), "inventory_identity")
    try:
        clean = receipt.diagnostic(identity["diagnostic"])
    except (ValueError, KeyError, TypeError):
        raise prior.NativeProofDenied("document_candidate_inventory_cleanup") from None
    need(clean and identity["diagnostic"]["launcher"] == "returned"
         and identity["diagnostic"]["launcher_code"] == 0, "inventory_cleanup")
    return {key: identity[key] for key in ("renderer_version", "font_manifest_sha256")}


def load_plan(path: Path) -> dict[str, Any]:
    need(path.is_absolute() and not path.is_symlink() and path.is_file() and path.stat().st_size < 65536,
         "plan_invalid")
    value = json.loads(path.read_bytes())
    need(type(value) is dict and value.get("version") == 1 and value.get("mode") in MODES,
         "plan_invalid")
    need(value.get("payload_sha256") == hashlib.sha256(rendered_payload(value["image"]).encode()).hexdigest()
         and value.get("job_request_sha256") == sha(desired(value["image"], value["job_nonce"], value["mode"]))
         and value.get("policy_sha256") == candidate.POLICY_SHA256, "plan_source_changed")
    return value


def verify(plan_path: Path) -> dict[str, Any]:
    plan = load_plan(plan_path)
    runtime = source_contract()
    build = prior.build_module().verify_current_build_release(plan["build_id"], plan["build_nonce"], plan["image"])
    need(build.get("policy_sha256") == candidate.POLICY_SHA256
         and sha(build) == plan["build_identity_sha256"], "build_changed")
    clients = prior.Clients(read_only_release=True)
    try:
        clients.preflight()
        job = clients.job(desired(plan["image"], plan["job_nonce"], plan["mode"])["name"])
        validate_job(job, plan)
        execution = clients.execution(prior.execution_reference(job, job.get("latestCreatedExecution", {}).get("name")))
        validate_execution(execution, job, plan)
        value = native_payload(clients, job, execution, plan["mode"])
        if plan["mode"] == "inventory":
            observed = validate_inventory(value, plan["image"], runtime)
        else:
            inventory = verify(Path(plan["inventory_plan"]))
            need(inventory["mode"] == "inventory" and inventory["image"] == plan["image"]
                 and inventory["receipt_sha256"] == plan["inventory_receipt_sha256"], "inventory_changed")
            observed = inventory["renderer"]
            try:
                complete = receipt.validate(value, plan["image"], candidate.POLICY_SHA256, runtime)
            except (ValueError, TypeError, KeyError, OverflowError):
                raise prior.NativeProofDenied("document_candidate_corpus_receipt_invalid") from None
            need(complete is True and all(value["identity_preflight"][key] == expected for key, expected in observed.items()),
                 "corpus_or_renderer_failed")
            scans = [row["scan"] for row in value["cases"] if "scan" in row]
            need(len(scans) == 5 and all(row == scans[0] for row in scans), "scan_metadata_disagrees")
            number, issued = scans[0]["definitions"].split("/", 1)
            stamp = datetime.fromisoformat(issued)
            need(re.fullmatch(r"[0-9]{1,10}", number) and stamp.tzinfo is not None
                 and timedelta(0) <= datetime.now(UTC) - stamp < timedelta(days=1), "definitions_not_fresh")
        return {"mode": plan["mode"], "image": plan["image"], "build_id": plan["build_id"],
                "job_name": job["name"], "job_uid": job["uid"], "execution_name": execution["name"],
                "execution_uid": execution["uid"], "receipt_sha256": sha(value), "renderer": observed,
                "policy_sha256": candidate.POLICY_SHA256, "runtime_source_hashes_verified": True,
                "required_cases_passed": 11 if plan["mode"] == "corpus" else 0,
                "native_corpus_proven": plan["mode"] == "corpus", "promotion_performed": False}
    finally:
        clients.close()


def ready(plan_path: Path) -> dict[str, Any]:
    """Fresh check of the exact created Job before its one permitted execution."""
    plan = load_plan(plan_path)
    source_contract()
    build = prior.build_module().verify_current_build_release(plan["build_id"], plan["build_nonce"], plan["image"])
    need(sha(build) == plan["build_identity_sha256"], "build_changed")
    clients = prior.Clients(read_only_release=True)
    try:
        clients.preflight()
        job = clients.job(desired(plan["image"], plan["job_nonce"], plan["mode"])["name"])
        validate_job(job, plan)
        need(prior.integer(job.get("executionCount", 0), 0)
             and job.get("latestCreatedExecution") in (None, {}), "job_already_executed_reconcile_only")
        return {"job_name": job["name"], "job_uid": job["uid"], "etag": job["etag"],
                "exact_template_verified": True, "execution_count": 0, "native_calls_mutating": 0}
    finally:
        clients.close()


def prepare(build_id: str, nonce: str, image: str, mode: str, directory: Path,
            inventory_plan: Path | None = None) -> dict[str, Any]:
    need(mode in MODES and directory.is_absolute() and not directory.exists(), "fresh_directory_required")
    source_contract()
    build = prior.build_module().verify_current_build_release(build_id, nonce, image)
    need(build.get("policy_sha256") == candidate.POLICY_SHA256, "build_policy_changed")
    plan = {"version": 1, "mode": mode, "build_id": build_id, "build_nonce": nonce, "image": image,
            "build_identity_sha256": sha(build), "job_nonce": uuid.uuid4().hex,
            "policy_sha256": candidate.POLICY_SHA256,
            "payload_sha256": hashlib.sha256(rendered_payload(image).encode()).hexdigest()}
    if mode == "corpus":
        need(inventory_plan is not None, "actual_inventory_required")
        assert inventory_plan is not None
        inventory = verify(inventory_plan)
        need(inventory["mode"] == "inventory" and inventory["image"] == image, "inventory_image_changed")
        plan.update(inventory_plan=str(inventory_plan), inventory_receipt_sha256=inventory["receipt_sha256"],
                    renderer=inventory["renderer"])
    body = desired(image, plan["job_nonce"], mode)
    plan["job_request_sha256"] = sha(body)
    clients = prior.Clients(read_only_release=True)
    try:
        clients.preflight()
        try:
            clients.job(body["name"])
        except clients.errors.HttpError as error:
            need(error.status_code == 404, "job_absence_unverified")
        else:
            raise prior.NativeProofDenied("document_candidate_job_exists_reconcile_only")
    finally:
        clients.close()
    directory.mkdir(mode=0o700)
    for name, value in (("plan.json", plan), ("job-v2.json", body), ("job-v1.json", v1_job(body)), ("build-identity.json", build)):
        prior.build_module().write_new(directory / name, value)
    return {"plan": str(directory / "plan.json"), "job_name": body["name"], "native_calls_mutating": 0,
            "submit_once": "gcloud run jobs replace " + str(directory / "job-v1.json")
                           + " --project=" + prior.PROJECT + " --region=" + prior.REGION + " --async",
            "run_once_after_exact_native_job_readback": "gcloud run jobs execute " + body["name"].rsplit("/", 1)[-1]
                           + " --project=" + prior.PROJECT + " --region=" + prior.REGION + " --async",
            "automatic_replay": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--build-id", required=True)
    p.add_argument("--nonce", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--mode", choices=sorted(MODES), required=True)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--inventory-plan", type=Path)
    v = commands.add_parser("verify")
    v.add_argument("--plan", type=Path, required=True)
    r = commands.add_parser("ready")
    r.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    result = (verify(args.plan) if args.command == "verify" else ready(args.plan) if args.command == "ready" else
              prepare(args.build_id, args.nonce, args.image, args.mode, args.directory, args.inventory_plan))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - provider bodies and credential-bearing errors must stay private.
        reason = (str(error) if type(error) is prior.NativeProofDenied
                  and re.fullmatch(r"[a-z_]{1,128}", str(error)) else "document_candidate_observation_unavailable")
        print(json.dumps({"status": "not_verified", "reason": reason, "automatic_replay": False}))
        raise SystemExit(2) from None
