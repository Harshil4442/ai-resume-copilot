"""Explicit recovery of one known pre-transition synthetic failure.

Original journals remain immutable. An exclusive GCS claim binds one fresh build
nonce; neither a failed Job nor an unknown mutation is replayed. Scheduled runs
can reverify retained dispositions, but cannot create a recovery claim.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

FILES = ("build-started.json", "source-upload-acknowledged.json", "build-create-started.json",
         "build-acknowledged.json", "native-build-identity.json", "local-native-proof.json",
         "native-proof/binding.json", "native-proof/create.started.json",
         "native-proof/create.acknowledged.json", "native-proof/run.started.json",
         "native-proof/run.acknowledged.json")
JOB = re.compile(r"hirewiz-doc-six-[a-f0-9]{12}\Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path, build: Any) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        build.need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                   and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= build.MAX_OUTPUT,
                   "document_refresh_recovery_file_refused")
        raw = stream.read(build.MAX_OUTPUT + 1)
    build.need(0 < len(raw) <= build.MAX_OUTPUT, "document_refresh_recovery_file_refused")
    return raw


def private(path: Path, build: Any) -> None:
    info = path.lstat()
    build.need(path.is_absolute() and path.resolve() == path and stat.S_ISDIR(info.st_mode)
               and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
               "document_refresh_recovery_directory_refused")


def snapshot(path: Path, build: Any) -> dict[str, bytes]:
    private(path, build)
    build.need(not os.path.lexists(path / "transition") and not os.path.lexists(path / "result.json"),
               "document_refresh_recovery_transition_or_result_present")
    build.need({entry.name for entry in path.iterdir()} <=
               {name.split("/")[0] for name in FILES} | {"terminal-recoveries"},
               "document_refresh_recovery_unrecognized_journal")
    private(path / "native-proof", build)
    build.need({entry.name for entry in (path / "native-proof").iterdir()} ==
               {name.split("/")[1] for name in FILES if name.startswith("native-proof/")},
               "document_refresh_recovery_unknown_native_marker")
    return {name: read(path / name, build) for name in FILES}


def manifest(files: dict[str, bytes]) -> str:
    return sha(canonical({name: sha(raw) for name, raw in files.items()}))


def retain(files: dict[str, bytes], target: Path) -> None:
    target.mkdir(mode=0o700)
    (target / "native-proof").mkdir(mode=0o700)
    for name, raw in files.items():
        fd = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


def verify_failed(path: Path, files: dict[str, bytes], clients: Any, probe: Any, build: Any) -> dict[str, Any]:
    """Only the observed cleaned identity-preflight failure is recoverable."""
    values = {name: build.decode(raw) for name, raw in files.items()}
    binding = values["native-proof/binding.json"]
    identity, image = binding["build_identity"], binding["image"]
    nonce = identity["nonce"]
    actual = build.verify_native_build(identity)
    build.need(image == identity["immutable_image"] and binding == {
        "image": image, "build_identity": identity,
        "probe_sha256": sha(probe.rendered_payload(image).encode())}, "document_refresh_recovery_binding_changed")
    build.need(values["native-build-identity.json"] ==
               {key: value for key, value in identity.items()
                if key not in {"local_native_proof_sha256", "source_sha256"}}
               and sha(files["local-native-proof.json"]) == identity["local_native_proof_sha256"],
               "document_refresh_recovery_build_proof_changed")
    local = values["local-native-proof.json"]
    # Historical local proof freshness is not extended; the new candidate must
    # acquire fresh definitions and pass all 21 cases itself.
    issued = local["corpus"][0]["scan"]["definitions"].split("/", 1)[1]
    from datetime import datetime

    build.proof_contract(local, build.accepted(), identity["source_sha256"], now=datetime.fromisoformat(issued))
    build.need(values["build-started.json"] == {"nonce": nonce,
        "recipe_sha256": identity["refresh_dockerfile_sha256"],
        "source_archive_sha256": identity["source_archive_sha256"], "automatic_replay": False,
        "unknown_outcome": "reconcile_exact_native_object_and_build_only"}, "document_refresh_recovery_build_intent_changed")
    source = values["source-upload-acknowledged.json"]
    build.need(source == {"bucket": identity["source_bucket"], "object": identity["source_object"],
                          "generation": identity["source_generation"]}, "document_refresh_recovery_source_ack_changed")
    build.need(values["build-create-started.json"] == {"nonce": nonce, "automatic_replay": False,
        "request_sha256": sha(canonical(build.build_request(nonce, source)))}, "document_refresh_recovery_build_request_changed")
    encoded = base64.urlsafe_b64encode(identity["build_id"].encode()).decode().rstrip("=")
    build.need(values["build-acknowledged.json"] == {"nonce": nonce, "build_id": actual["build_id"],
        "operation_name": "operations/build/" + build.PROJECT + "/" + encoded, "automatic_replay": False},
        "document_refresh_recovery_build_ack_changed")
    owner = probe.Journal(path / "native-proof", image, identity)
    job = probe.owned_job(clients, owner, image, nonce)
    marker = owner.read("run.started.json")
    build.need(set(marker) == {"job_name", "job_uid", "etag", "automatic_replay"}
        and marker["job_name"] == job["name"] and marker["job_uid"] == job["uid"]
        and type(marker["etag"]) is str and marker["etag"] and marker["automatic_replay"] is False,
        "document_refresh_recovery_run_intent_changed")
    acknowledged = owner.read("run.acknowledged.json")
    build.need(set(acknowledged) == {"name"}, "document_refresh_recovery_run_ack_changed")
    operation = clients.operation(acknowledged["name"])
    metadata = operation.get("metadata", {})
    build.need(operation.get("name") == acknowledged["name"] and operation.get("done") is True
        and not operation.get("response") and type(operation.get("error", {}).get("code")) is int
        and operation["error"]["code"] == 10
        and metadata.get("@type") == "type.googleapis.com/google.cloud.run.v2.Execution",
        "document_refresh_recovery_run_not_definitely_failed")
    execution = clients.execution(metadata["name"])
    short = job["name"].rsplit("/", 1)[-1]
    build.need(re.fullmatch(re.escape(job["name"] + "/executions/" + short + "-") + r"[a-z0-9]{1,20}", execution.get("name", ""))
        and execution.get("name") == metadata.get("name") and execution.get("uid") == metadata.get("uid")
        and type(execution.get("uid")) is str and probe.UUID.fullmatch(execution["uid"])
        and execution.get("job") in (job["name"], short) and execution.get("reconciling", False) is False
        and bool(execution.get("completionTime")) and probe.integer(execution.get("taskCount"), 1)
        and probe.integer(execution.get("failedCount"), 1)
        and all(probe.integer(execution.get(key, 0), 0) for key in ("succeededCount", "runningCount", "cancelledCount", "retriedCount"))
        and probe.task_matches(execution.get("template"), probe.desired(image, nonce)["template"]["template"])
        and probe.integer(job.get("executionCount"), 1)
        and job.get("latestCreatedExecution", {}).get("name") in (execution["name"], execution["name"].rsplit("/", 1)[-1])
        and job["latestCreatedExecution"].get("completionStatus") == "EXECUTION_FAILED",
        "document_refresh_recovery_execution_not_single_failed")
    for row in (metadata, execution):
        completed = [condition for condition in row.get("conditions", []) if condition.get("type") == "Completed"]
        build.need(len(completed) == 1 and completed[0].get("state") == "CONDITION_FAILED"
            and completed[0].get("executionReason") == "NON_ZERO_EXIT_CODE"
            and probe.integer(row.get("taskCount"), 1) and probe.integer(row.get("failedCount"), 1)
            and all(probe.integer(row.get(key, 0), 0) for key in ("succeededCount", "runningCount", "cancelledCount", "retriedCount"))
            and row.get("completionTime") == execution.get("completionTime")
            and probe.task_matches(row.get("template"), probe.desired(image, nonce)["template"]["template"]),
            "document_refresh_recovery_execution_failure_changed")
    payload = probe.native_payload(clients, job, execution)
    parser = probe.load_source("hirewiz_terminal_failure_parser", probe.HERE / "document_refresh_receipt.txt")
    build.need(sha((probe.HERE / "document_refresh_receipt.txt").read_bytes()) == probe.PARSER_SHA
        and parser.validate(payload, image, probe.POLICY, probe.runtime_hashes()) is False
        and set(payload) == parser.TOP and payload["failure_stage"] == "identity_preflight"
        and payload["outer_runtime_user_verified"] is True
        and all(row == {"case": name, "passed": False, "attempted": False}
                for row, name in zip(payload["cases"], parser.NAMES, strict=True)),
        "document_refresh_recovery_failure_report_changed")
    diagnostic = payload["identity_preflight"].get("diagnostic", {})
    build.need(payload["identity_preflight"] == {"passed": False, "cleanup_passed": True, "diagnostic": diagnostic}
        and diagnostic["launcher"] == "returned" and diagnostic["launcher_code"] == 1
        and diagnostic["cleanup"] == "returned" and diagnostic["cleanup_code"] == 0
        and diagnostic["processor"] == "not_validated" and diagnostic["validation"] == "not_started"
        and diagnostic["failure_phase"] is None, "document_refresh_recovery_cleanup_unverified")
    return {"failed_job": job["name"], "job_uid": job["uid"], "failed_execution": execution["name"],
        "execution_uid": execution["uid"], "build_id": identity["build_id"], "image": image,
        "manifest_sha256": manifest(files), "failure_payload_sha256": sha(canonical(payload))}


def joined(clients: Any, probe: Any, build: Any, failed_jobs: set[str]) -> None:
    snapshot = clients.preflight()
    service = snapshot["service"]
    image = service["template"]["containers"][0]["image"]
    build.need(service.get("labels", {}).get("hirewiz-refresh-job") not in failed_jobs,
               "document_refresh_recovery_failed_job_serving")
    if image != probe.BASE_IMAGE:
        # Another fresh, genuinely accepted Job may prove the same image bytes.
        # A failed Job's receipt can never establish that serving authority.
        probe.verify_current_successor(image, service)
    controller = build.infra("document_refresh_transition").Controller(clients.http, probe, None)
    for name in ("hirewiz-document-inspector", "ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-resume-coordinator"):
        value = clients.service(probe.PARENT + "/services/" + name)
        controller.ready(name, value, uid=probe.SERVICE_UID if name == "hirewiz-document-inspector" else None)
    probe.verify_caller_pins(clients, image)


def object_name(uid: str) -> str:
    return "hirewiz-document-refresh-source/terminal-recovery/" + uid + ".json"


def verify_claim(body: dict[str, Any], acknowledgment: dict[str, Any], build: Any) -> None:
    name = object_name(body["job_uid"])
    build.need(set(acknowledgment) == {"name", "bucket", "generation"}
        and acknowledgment["name"] == name and acknowledgment["bucket"] == build.PROJECT + "_cloudbuild"
        and re.fullmatch(r"[1-9][0-9]{0,19}", acknowledgment["generation"]), "document_refresh_recovery_claim_ack_changed")
    http = build.native_http()
    try:
        response, raw = http.request("https://storage.googleapis.com/download/storage/v1/b/" + acknowledgment["bucket"]
            + "/o/" + quote(name, safe="") + "?" + urlencode({"alt": "media", "generation": acknowledgment["generation"]}),
            method="GET", redirections=0)
        build.need(response.status == 200 and raw == canonical(body), "document_refresh_recovery_claim_unverified")
    finally:
        http.close()


def prepare(directory: Path, history: Path | None, selected: str | None, *, refresh_module: Any) -> str | None:
    build = refresh_module
    build.need(selected is None or type(selected) is str and JOB.fullmatch(selected), "document_refresh_recovery_exact_job_required")
    if history is None:
        build.need(selected is None, "document_refresh_recovery_history_required")
        return None
    build.need(history.is_absolute() and history.is_dir() and not history.is_symlink(), "document_refresh_prior_history_refused")
    runs = list(history.iterdir())
    build.need(len(runs) <= 3, "document_refresh_prior_history_bound")
    records, pending, record_roots = {}, [], []
    for run in runs:
        build.need(run.is_dir() and not run.is_symlink(), "document_refresh_prior_unknown_reconcile_only")
        root = run / "terminal-recoveries"
        if root.exists():
            record_roots.append(root)
        result = run / "result.json"
        if result.exists():
            previous = build.decode(read(result, build))
            build.need(previous.get("status") == "document_refresh_serving" and previous.get("admission_restored") is True,
                       "document_refresh_prior_unknown_reconcile_only")
        else:
            try:
                files = snapshot(run, build)
                binding = build.decode(files["native-proof/binding.json"])
                job = "hirewiz-doc-six-" + binding["build_identity"]["nonce"][:12]
                build.need(JOB.fullmatch(job), "document_refresh_recovery_exact_job_required")
            except Exception:  # noqa: BLE001 - partial journals never authorize a new mutation.
                raise build.RefreshDenied("document_refresh_prior_unknown_reconcile_only") from None
            pending.append((job, files, run))
    # Only failures still present in the bounded retrieved history need a
    # disposition. Completed newer runs must not depend forever on old logs,
    # LROs or source objects reaching the end of their provider retention.
    required = {job for job, _, _ in pending}
    for root in record_roots:
        for job in required:
            record = root / job
            if not os.path.lexists(record):
                continue
            private(root, build)
            private(record, build)
            files = snapshot(record / "original", build)
            raw = {name: read(record / name, build) for name in
                   ("disposition.json", "claim.started.json", "claim.acknowledged.json")}
            current = (files, raw)
            build.need(job not in records or records[job][:2] == current,
                       "document_refresh_recovery_disposition_changed")
            records[job] = (*current, record / "original")
    targets = [row for row in pending if row[0] == selected]
    build.need(selected is None or len(targets) == 1 and selected not in records,
               "document_refresh_recovery_missing_or_consumed_selection")
    for job, files, _ in pending:
        build.need(job == selected or job in records and records[job][0] == files,
                   "document_refresh_prior_unknown_reconcile_only")
    if not records and selected is None:
        return None
    probe = build.infra("document_refresh_probe")
    clients = probe.Clients()
    try:
        facts = {}
        for job, (files, raw, original) in records.items():
            fact = verify_failed(original, files, clients, probe, build)
            body = build.decode(raw["disposition.json"])
            build.need(body == {**fact, "version": 1, "status": "terminal_failure_reconciled_not_acceptance",
                "successor_nonce": body.get("successor_nonce"), "failed_job_replayed": False}
                and JOB.fullmatch(job) and fact["failed_job"].rsplit("/", 1)[-1] == job
                and type(body["successor_nonce"]) is str and re.fullmatch(r"[a-f0-9]{32}", body["successor_nonce"])
                and build.decode(raw["claim.started.json"]) == {"body_sha256": sha(canonical(body)), "automatic_replay": False},
                "document_refresh_recovery_disposition_changed")
            verify_claim(body, build.decode(raw["claim.acknowledged.json"]), build)
            facts[job] = fact
        chosen = None
        if selected is not None:
            _, files, original = targets[0]
            chosen = verify_failed(original, files, clients, probe, build)
        joined(clients, probe, build, set(facts) | ({selected} if chosen else set()))
        target_root = directory / "terminal-recoveries"
        target_root.mkdir(mode=0o700)
        for job, (files, raw, _) in records.items():
            target = target_root / job
            target.mkdir(mode=0o700)
            retain(files, target / "original")
            for name, content in raw.items():
                fd = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
        if chosen is None:
            return None
        nonce = uuid.uuid4().hex
        build.need(nonce != build.decode(targets[0][1]["native-proof/binding.json"])["build_identity"]["nonce"],
                   "document_refresh_recovery_successor_nonce_reused")
        target = target_root / selected
        target.mkdir(mode=0o700)
        retain(targets[0][1], target / "original")
        body = {**chosen, "version": 1, "status": "terminal_failure_reconciled_not_acceptance",
                "successor_nonce": nonce, "failed_job_replayed": False}
        build.write_new(target / "claim.started.json", {"body_sha256": sha(canonical(body)), "automatic_replay": False})
        http = build.native_http()
        try:
            name = object_name(chosen["job_uid"])
            response, _ = http.request("https://storage.googleapis.com/storage/v1/b/" + build.PROJECT + "_cloudbuild/o/"
                + quote(name, safe=""), method="GET", redirections=0)
            build.need(response.status == 404, "document_refresh_recovery_claim_unknown_or_consumed")
            # Retain the exact intent before the only create-only write. An
            # existing/unknown claim never authorizes another candidate.
            response, raw = http.request("https://storage.googleapis.com/upload/storage/v1/b/" + build.PROJECT + "_cloudbuild/o?"
                + urlencode({"uploadType": "media", "name": name, "ifGenerationMatch": "0"}),
                method="POST", body=canonical(body), headers={"Content-Type": "application/json"}, redirections=0)
            build.need(response.status in (200, 201), "document_refresh_recovery_claim_unknown_or_consumed")
            value = build.decode(raw)
            acknowledgment = {key: value.get(key) for key in ("name", "bucket", "generation")}
            build.write_new(target / "claim.acknowledged.json", acknowledgment)
        finally:
            http.close()
        verify_claim(body, acknowledgment, build)
        build.write_new(target / "disposition.json", body)
        return nonce
    finally:
        clients.close()
