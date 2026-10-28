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
PRE_NATIVE_FILES = FILES[:5]
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
    if any(name.startswith("native-proof/") for name in files):
        (target / "native-proof").mkdir(mode=0o700)
    for name, raw in files.items():
        fd = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


def pre_native_snapshot(path: Path, build: Any, *, history_root: bool = False) -> dict[str, bytes]:
    """Only a fully acknowledged Build stopped before local/native proof."""
    private(path, build)
    names = {entry.name for entry in path.iterdir()}
    allowed = set(PRE_NATIVE_FILES)
    build.need(names == allowed or history_root and names == allowed | {"pre-native-reconciliations"},
               "document_refresh_pre_native_shape_changed")
    return {name: read(path / name, build) for name in PRE_NATIVE_FILES}


def pre_native_reconciliations(path: Path, build: Any) -> list[tuple[str, dict[str, bytes], dict[str, Any]]]:
    """Read a successor's retained Build-only dispositions without trusting them."""
    root = path / "pre-native-reconciliations"
    if not os.path.lexists(root):
        return []
    private(root, build)
    entries = list(root.iterdir())
    build.need(0 < len(entries) <= 3, "document_refresh_pre_native_reconciliation_shape_changed")
    found = []
    for entry in entries:
        build.need(re.fullmatch(r"[a-f0-9]{32}", entry.name) is not None,
                   "document_refresh_pre_native_reconciliation_shape_changed")
        private(entry, build)
        build.need({child.name for child in entry.iterdir()} == {"original", "reconciliation.json"},
                   "document_refresh_pre_native_reconciliation_shape_changed")
        original = pre_native_snapshot(entry / "original", build)
        receipt = build.decode(read(entry / "reconciliation.json", build))
        build.need(type(receipt) is dict, "document_refresh_pre_native_reconciliation_changed")
        found.append((entry.name, original, receipt))
    return found


def verify_pre_native_history(
    rows: list[tuple[dict[str, bytes], Path]],
    receipts: list[tuple[str, str, dict[str, bytes], dict[str, Any]]],
    clients: Any, probe: Any, build: Any,
) -> tuple[list[tuple[dict[str, Any], dict[str, bytes], Path]], list[dict[str, Any]], set[str]]:
    """Prove each Build and receipt, then leave only unconsumed roots open."""
    by_nonce: dict[str, tuple[dict[str, Any], dict[str, bytes], Path]] = {}
    verified: list[dict[str, Any]] = []
    for files, path in rows:
        fact = verify_pre_native(files, clients, probe, build)
        nonce = fact["nonce"]
        build.need(nonce not in by_nonce, "document_refresh_pre_native_duplicate_history")
        by_nonce[nonce] = (fact, files, path)
        verified.append(fact)
    consumed: dict[str, str] = {}
    used_nonces = set(by_nonce)
    for successor, prior, original, receipt in receipts:
        build.need(type(successor) is str and re.fullmatch(r"[a-f0-9]{32}", successor)
                   and prior != successor and prior not in consumed,
                   "document_refresh_pre_native_reconciliation_changed")
        if prior in by_nonce:
            fact, saved, _ = by_nonce[prior]
            build.need(original == saved, "document_refresh_pre_native_original_changed")
        else:
            # Older artifact may have aged out of the bounded history. Its
            # retained original still needs the same native Build/Job proof.
            fact = verify_pre_native(original, clients, probe, build)
            verified.append(fact)
        build.need(fact["nonce"] == prior and receipt == {**fact, "version": 1,
            "status": "pre_native_candidate_abandoned_not_acceptance", "successor_nonce": successor,
            "old_build_replayed": False, "native_acceptance": False, "promotion_performed": False},
            "document_refresh_pre_native_reconciliation_changed")
        consumed[prior] = successor
        used_nonces.update((prior, successor))
    for origin in consumed:
        seen = set()
        cursor = origin
        while cursor in consumed:
            build.need(cursor not in seen, "document_refresh_pre_native_reconciliation_cycle")
            seen.add(cursor)
            cursor = consumed[cursor]
    open_rows = [row for nonce, row in by_nonce.items() if nonce not in consumed]
    return open_rows, verified, used_nonces


def completed_serving_v3(binding: Any, accepted: Any, transition_accepted: Any,
                         complete: Any, result: Any, build: Any) -> None:
    """Recognize a completed V3 receipt only as consumed-history identity."""
    candidate = build.infra("document_candidate_release")
    profile = candidate.profile
    identity = binding.get("build_identity") if type(binding) is dict else None
    keys = {"version", "kind", "nonce", "build_id", "build_resource", "builder_service_account",
            "source_bucket", "source_object", "source_generation", "source_archive_sha256",
            "refresh_dockerfile_sha256", "fixed_renderer_base", "immutable_image", "policy_sha256",
            "source_sha256"}
    completed_keys = {"status", "image", "prior_image", "native_acceptance_sha256",
                      "all_three_caller_pins_verified", "admission_restored", "synchronous_callers_drained",
                      "current_native_proof_retained", "automatic_replay"}
    added_keys = {"definition_expires_at", "renderer_contract_version", "required_case_count",
                  "renderer_parent", "prior_evidence_retained", "fresh_v2_upgraded"}
    build.need(type(binding) is dict and set(binding) == {"version", "image", "build_identity"}
               and type(binding["version"]) is int and binding["version"] == 3
               and type(identity) is dict and set(identity) == keys
               and type(accepted) is dict and set(accepted) == set(candidate.prior.NativeAcceptance.__dataclass_fields__)
               and transition_accepted == accepted
               and type(complete) is dict and set(complete) == completed_keys
               and type(result) is dict and set(result) == completed_keys | added_keys,
               "document_refresh_prior_completed_v3_receipt_changed")
    nonce = identity["nonce"]
    image = binding["image"]
    build.need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce)
               and type(image) is str and candidate.prior.IMAGE.fullmatch(image)
               and type(identity["version"]) is int and identity["version"] == 1
               and identity["kind"] == "native_cloud_build"
               and identity["immutable_image"] == image and identity["fixed_renderer_base"] == profile.IMAGE
               and identity["builder_service_account"] == build.BUILDER
               and identity["source_bucket"] == build.PROJECT + "_cloudbuild"
               and identity["source_object"] == "hirewiz-document-refresh-source/" + nonce + "/source.tar.gz"
               and type(identity["source_generation"]) is str
               and re.fullmatch(r"[1-9][0-9]{0,19}", identity["source_generation"])
               and type(identity["source_archive_sha256"]) is str
               and re.fullmatch(r"[a-f0-9]{64}", identity["source_archive_sha256"])
               and identity["policy_sha256"] == candidate.POLICY
               and identity["refresh_dockerfile_sha256"] == profile.REFRESH_SHA256
               and identity["source_sha256"] == build.full_source_snapshot()[1]
               and accepted["image"] == complete["image"] == result["image"] == image
               and accepted["build_id"] == identity["build_id"]
               and accepted["build_resource"] == identity["build_resource"]
               and identity["build_resource"] == candidate.prior.PARENT + "/builds/" + identity["build_id"]
               and accepted["source_archive_sha256"] == identity["source_archive_sha256"]
               and accepted["refresh_dockerfile_sha256"] == identity["refresh_dockerfile_sha256"]
               and accepted["source_sha256"] == identity["source_sha256"]
               and accepted["job_name"] == candidate.prior.PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12]
               and accepted["probe_sha256"] == sha(candidate.candidate.rendered_payload(image).encode())
               and accepted["policy_sha256"] == candidate.POLICY
               and accepted["font_manifest_sha256"] == profile.FONT
               and accepted["renderer_version"] == profile.RENDERER
               and type(accepted["contract_version"]) is int and accepted["contract_version"] == 3
               and type(accepted["required_case_count"]) is int and accepted["required_case_count"] == 11
               and sha(canonical(accepted)) == complete["native_acceptance_sha256"]
               and all(result[key] == value for key, value in complete.items())
               and result["definition_expires_at"] == accepted["definition_expires_at"]
               and type(result["renderer_contract_version"]) is int and result["renderer_contract_version"] == 3
               and type(result["required_case_count"]) is int and result["required_case_count"] == 11
               and result["renderer_parent"] == profile.IMAGE
               and result["prior_evidence_retained"] is True
               and type(result["fresh_v2_upgraded"]) is bool
               and complete["status"] == "document_refresh_serving"
               and type(complete["prior_image"]) is str and candidate.prior.IMAGE.fullmatch(complete["prior_image"])
               and complete["prior_image"] != image
               and complete["admission_restored"] is True
               and complete["all_three_caller_pins_verified"] is True
               and complete["synchronous_callers_drained"] is False
               and complete["current_native_proof_retained"] is True
               and complete["automatic_replay"] is False,
               "document_refresh_prior_completed_v3_receipt_changed")


def completed_serving(run: Path, result: dict[str, Any], expected_journal: Path,
                      build: Any, probe: Any, *, allow_v3: bool = False) -> None:
    """The retained serving artifact is identity evidence, never fresh acceptance."""
    private(run, build)
    native = run / "native-proof"
    transition = run / "transition"
    build.need(native == expected_journal and native.resolve() == native,
               "document_rescue_prior_completed_journal_changed")
    for directory in (native, transition):
        private(directory, build)
    binding = build.decode(read(native / "binding.json", build))
    accepted = build.decode(read(native / "acceptance.json", build))
    transition_accepted = build.decode(read(transition / "native-acceptance.json", build))
    complete = build.decode(read(transition / "complete.json", build))
    if type(binding) is dict and binding.get("version") == 3:
        build.need(allow_v3 and not os.path.lexists(run / "prior-native-retirement"),
                   "document_refresh_prior_completed_v3_receipt_changed")
        completed_serving_v3(binding, accepted, transition_accepted, complete, result, build)
        return
    prior = run / "prior-native-retirement"
    private(prior, build)
    cleanup = build.decode(read(prior / "cleanup.json", build))
    identity = binding.get("build_identity") if type(binding) is dict else None
    build.need(type(identity) is dict and set(binding) == {"image", "build_identity", "probe_sha256"}
               and type(accepted) is dict and set(accepted) == set(probe.NativeAcceptance.__dataclass_fields__)
               and transition_accepted == accepted and type(complete) is dict
               and type(cleanup) is dict and type(result) is dict,
               "document_rescue_prior_completed_receipt_changed")
    nonce = identity.get("nonce")
    build.need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce)
               and identity.get("kind") == "native_cloud_build"
               and binding["image"] == accepted["image"] == complete.get("image") == result.get("image")
               and binding["probe_sha256"] == accepted["probe_sha256"]
               and identity.get("immutable_image") == binding["image"]
               and identity.get("build_id") == accepted["build_id"]
               and accepted["job_name"] == probe.PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12]
               and accepted["policy_sha256"] == probe.POLICY
               and type(accepted["contract_version"]) is int and accepted["contract_version"] == 2
               and type(accepted["required_case_count"]) is int and accepted["required_case_count"] == 10
               and sha(canonical(accepted)) == result.get("native_acceptance_sha256") == complete.get("native_acceptance_sha256")
               and result.get("definition_expires_at") == accepted["definition_expires_at"]
               and result.get("prior_cleanup") == cleanup and cleanup.get("owned_resources_absent") is True
               and set(result) == set(complete) | {"definition_expires_at", "prior_cleanup"}
               and all(result[key] == value for key, value in complete.items())
               and complete.get("status") == "document_refresh_serving"
               and complete.get("admission_restored") is True
               and complete.get("all_three_caller_pins_verified") is True
               and complete.get("current_native_proof_retained") is True
               and complete.get("automatic_replay") is False,
               "document_rescue_prior_completed_receipt_changed")


def verify_pre_native(files: dict[str, bytes], clients: Any, probe: Any, build: Any) -> dict[str, Any]:
    """Reconcile a terminal Build and absent Job; never accept its image."""
    values = {name: build.decode(raw) for name, raw in files.items()}
    identity = values["native-build-identity.json"]
    keys = {"version", "kind", "nonce", "build_id", "build_resource", "builder_service_account",
            "source_bucket", "source_object", "source_generation", "source_archive_sha256",
            "refresh_dockerfile_sha256", "fixed_renderer_base", "immutable_image"}
    build.need(type(identity) is dict and set(identity) == keys
               and type(identity["version"]) is int and identity["version"] == 1
               and identity["kind"] == "native_cloud_build"
               and all(type(value) is str for key, value in identity.items() if key != "version")
               and re.fullmatch(r"[a-f0-9]{32}", identity["nonce"]),
               "document_refresh_pre_native_identity_changed")
    # Frozen V1 bytes may establish abandonment only. They never select weaker
    # acceptance for a new candidate; all current local/native gates still run.
    recipe_sha = identity["refresh_dockerfile_sha256"]
    if (identity["fixed_renderer_base"] == build.RENDERER_BASE
            and recipe_sha == sha((build.ROOT / "infra/document-processing/refresh.Dockerfile").read_bytes())):
        historical_v1 = False
    else:
        build.need(identity["fixed_renderer_base"] == probe.LEGACY_BASE_IMAGE
                   and recipe_sha == sha(probe.legacy_source("refresh.Dockerfile")),
                   "document_refresh_pre_native_recipe_changed")
        historical_v1 = True
    nonce = identity["nonce"]
    source = {"bucket": identity["source_bucket"], "object": identity["source_object"],
              "generation": identity["source_generation"]}
    encoded = base64.urlsafe_b64encode(identity["build_id"].encode()).decode().rstrip("=")
    build.need(values["source-upload-acknowledged.json"] == source
        and values["build-started.json"] == {"nonce": nonce, "recipe_sha256": recipe_sha,
            "source_archive_sha256": identity["source_archive_sha256"], "automatic_replay": False,
            "unknown_outcome": "reconcile_exact_native_object_and_build_only"}
        and values["build-create-started.json"] == {"nonce": nonce, "automatic_replay": False,
            "request_sha256": sha(canonical(build.build_request(nonce, source)))}
        and values["build-acknowledged.json"] == {"nonce": nonce, "build_id": identity["build_id"],
            "operation_name": "operations/build/" + build.PROJECT + "/" + encoded, "automatic_replay": False},
        "document_refresh_pre_native_ack_changed")
    arguments = {"historical_v1": True} if historical_v1 else {}
    actual = build.verify_current_build(identity["build_id"], nonce, identity["immutable_image"], **arguments)
    build.need(identity == {key: value for key, value in actual.items() if key != "source_sha256"},
               "document_refresh_pre_native_build_changed")
    job = probe.PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12]
    try:
        clients.job(job)
    except clients.errors.HttpError as error:
        build.need(error.status_code == 404, "document_refresh_pre_native_absence_unverified")
    else:
        raise build.RefreshDenied("document_refresh_pre_native_job_exists_reconcile_only")
    return {"nonce": nonce, "build_id": identity["build_id"], "image": identity["immutable_image"],
            "manifest_sha256": manifest(files), "native_build_identity_sha256": sha(canonical(actual)),
            "absent_native_job": job, "native_job_http_status": 404, "historical_v1": historical_v1}


def verify_failed(path: Path, files: dict[str, bytes], clients: Any, probe: Any, build: Any) -> dict[str, Any]:
    """Only the observed cleaned identity-preflight failure is recoverable."""
    values = {name: build.decode(raw) for name, raw in files.items()}
    binding = values["native-proof/binding.json"]
    identity, image = binding["build_identity"], binding["image"]
    nonce = identity["nonce"]
    # Select the historical profile only from the actual exact Job image/policy.
    # Saved success/disposition data does not choose a weaker verifier.
    observed_job = clients.job(probe.PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12])
    historical_v1 = probe.historical_v1_for_serving(
        {"template": observed_job.get("template", {}).get("template", {})}, image)
    actual = (build.verify_native_build(identity, historical_v1=True)
              if historical_v1 else build.verify_native_build(identity))
    build.need(image == identity["immutable_image"] and binding == {
        "image": image, "build_identity": identity,
        "probe_sha256": sha(probe.rendered_payload(image, historical_v1=historical_v1).encode())},
        "document_refresh_recovery_binding_changed")
    build.need(values["native-build-identity.json"] ==
               {key: value for key, value in identity.items()
                if key not in {"local_native_proof_sha256", "source_sha256"}}
               and sha(files["local-native-proof.json"]) == identity["local_native_proof_sha256"],
               "document_refresh_recovery_build_proof_changed")
    local = values["local-native-proof.json"]
    # Historical local proof freshness is not extended; the new candidate must
    # acquire fresh definitions and pass all 25 current cases itself.
    issued = local["corpus"][0]["scan"]["definitions"].split("/", 1)[1]
    from datetime import datetime

    arguments = {"historical_v1": True} if historical_v1 else {}
    build.proof_contract(local, build.accepted(), identity["source_sha256"], now=datetime.fromisoformat(issued), **arguments)
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
    owner = probe.Journal(path / "native-proof", image, identity, historical_v1=historical_v1)
    job = probe.owned_job(clients, owner, image, nonce, historical_v1=historical_v1)
    build.need(job == observed_job, "document_refresh_recovery_job_changed_during_profile_selection")
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
        and probe.task_matches(execution.get("template"), probe.desired(image, nonce, historical_v1=historical_v1)["template"]["template"])
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
            and probe.task_matches(row.get("template"), probe.desired(image, nonce, historical_v1=historical_v1)["template"]["template"]),
            "document_refresh_recovery_execution_failure_changed")
    payload = probe.native_payload(clients, job, execution, historical_v1=historical_v1)
    parser_path = probe.HERE / ("document_legacy_v1/receipt.txt" if historical_v1 else "document_refresh_receipt.txt")
    parser_raw = probe.legacy_source("receipt.txt") if historical_v1 else parser_path.read_bytes()
    parser = probe.load_source("hirewiz_terminal_failure_parser_v1" if historical_v1 else "hirewiz_terminal_failure_parser", parser_path)
    policy = probe.LEGACY_POLICY if historical_v1 else probe.POLICY
    build.need(sha(parser_raw) == (probe.LEGACY_SOURCE_HASHES["receipt.txt"] if historical_v1 else probe.PARSER_SHA)
        and parser.validate(payload, image, policy, probe.runtime_hashes(historical_v1=historical_v1)) is False
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


def joined(clients: Any, probe: Any, build: Any, failed_jobs: set[str],
           unaccepted_images: set[str] | None = None, *, retire_expired_v2: bool = False,
           rescue_expired_v2: bool = False) -> None:
    build.need(type(retire_expired_v2) is bool and type(rescue_expired_v2) is bool
               and not (retire_expired_v2 and rescue_expired_v2),
               "document_refresh_recovery_mode_invalid")
    snapshot = clients.preflight()
    service = snapshot["service"]
    image = service["template"]["containers"][0]["image"]
    build.need(image not in (unaccepted_images or set()), "document_refresh_pre_native_candidate_serving")
    policy = probe.policy_for_serving(service, image)
    build.need(service.get("labels", {}).get("hirewiz-refresh-job") not in failed_jobs,
               "document_refresh_recovery_failed_job_serving")
    if image != probe.BASE_IMAGE:
        if retire_expired_v2 or rescue_expired_v2:
            # Both explicit paths use the expired predecessor only as historical
            # identity. Its old proof never authorizes a new serving image.
            probe.verify_expired_current_v2_for_retirement(image, service)
        else:
            # Another fresh, genuinely accepted Job may prove the same image bytes.
            # A failed Job's receipt can never establish that serving authority.
            probe.verify_current_successor(image, service)
    transition = build.infra("document_refresh_transition")
    controller = transition.Controller(clients.http, probe, None, rescue_expired_v2=rescue_expired_v2)
    for name in ("hirewiz-document-inspector", "ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-resume-coordinator"):
        value = clients.service(probe.PARENT + "/services/" + name)
        controller.ready(name, value, uid=probe.SERVICE_UID if name == "hirewiz-document-inspector" else None)
    if rescue_expired_v2:
        transition.verify_rescue_caller_pins(clients, probe, image)
    else:
        probe.verify_caller_pins(clients, image, policy_sha256=policy)


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


def prepare(directory: Path, history: Path | None, selected: str | None, *, refresh_module: Any,
            resuming_original: Path | None = None, retire_expired_v2: bool = False,
            rescue_expired_v2: bool = False,
            rescue_prior_journal: Path | None = None) -> str | None:
    build = refresh_module
    build.need(type(retire_expired_v2) is bool and type(rescue_expired_v2) is bool
               and not (retire_expired_v2 and rescue_expired_v2)
               and (not (retire_expired_v2 or rescue_expired_v2)
                    or selected is None and resuming_original is None),
               "document_refresh_recovery_mode_invalid")
    build.need((rescue_expired_v2 and rescue_prior_journal is not None or
                not rescue_expired_v2 and rescue_prior_journal is None),
               "document_rescue_prior_completed_journal_required")
    build.need(selected is None or type(selected) is str and JOB.fullmatch(selected), "document_refresh_recovery_exact_job_required")
    if history is None:
        build.need(selected is None and not rescue_expired_v2, "document_refresh_recovery_history_required")
        return None
    build.need(history.is_absolute() and history.is_dir() and not history.is_symlink(), "document_refresh_prior_history_refused")
    if rescue_expired_v2:
        assert rescue_prior_journal is not None
        build.need(rescue_prior_journal.is_absolute() and not rescue_prior_journal.is_symlink()
                   and rescue_prior_journal.parent.parent == history,
                   "document_rescue_prior_completed_journal_required")
    runs = list(history.iterdir())
    build.need(len(runs) <= 3, "document_refresh_prior_history_bound")
    records: dict[str, Any] = {}
    pending: list[tuple[str, dict[str, bytes], Path]] = []
    pre_native: list[tuple[dict[str, bytes], Path]] = []
    pre_native_receipts: list[tuple[str, str, dict[str, bytes], dict[str, Any]]] = []
    record_roots: list[Path] = []
    resume = None
    if resuming_original is not None:
        resume = build.infra("document_refresh_resume")
        build.need(selected is None, "document_resume_cannot_recover_another_job")
        resume.partial_snapshot(resuming_original, build)
    consumed = None
    known_partials = 0
    completed_count = 0
    rescue_probe = build.infra("document_refresh_probe") if rescue_expired_v2 else None
    for run in runs:
        build.need(run.is_dir() and not run.is_symlink(), "document_refresh_prior_unknown_reconcile_only")
        root = run / "terminal-recoveries"
        if root.exists():
            record_roots.append(root)
        result = run / "result.json"
        if result.exists():
            previous = build.decode(read(result, build))
            build.need(type(previous) is dict and previous.get("status") == "document_refresh_serving"
                       and previous.get("admission_restored") is True,
                       "document_refresh_prior_unknown_reconcile_only")
            if rescue_expired_v2:
                assert rescue_prior_journal is not None and rescue_probe is not None
                completed_serving(run, previous, rescue_prior_journal, build, rescue_probe)
                completed_count += 1
            if os.path.lexists(run / "pre-native-reconciliations"):
                # A completed successor can consume a Build-only predecessor.
                # Its own binding must be a verified serving receipt first.
                if not rescue_expired_v2:
                    completed_serving(run, previous, run / "native-proof", build,
                                      build.infra("document_refresh_probe"), allow_v3=True)
                binding = build.decode(read(run / "native-proof/binding.json", build))
                successor = binding["build_identity"]["nonce"]
                for prior, original, receipt in pre_native_reconciliations(run, build):
                    pre_native_receipts.append((successor, prior, original, receipt))
        else:
            names = {entry.name for entry in run.iterdir()}
            if names in (set(PRE_NATIVE_FILES), set(PRE_NATIVE_FILES) | {"pre-native-reconciliations"}):
                files = pre_native_snapshot(run, build, history_root=True)
                pre_native.append((files, run))
                if "pre-native-reconciliations" in names:
                    successor = build.decode(files["native-build-identity.json"])["nonce"]
                    for prior, original, receipt in pre_native_reconciliations(run, build):
                        pre_native_receipts.append((successor, prior, original, receipt))
                continue
            # Only the known pre-Job shape needs the additive resume reader;
            # ordinary V1/V2 failed journals keep their original verifier path.
            partial_shape = {"build-started.json", "source-upload-acknowledged.json", "build-create-started.json",
                             "build-acknowledged.json", "native-build-identity.json", "terminal-recoveries"}
            if names == partial_shape:
                if resume is None:
                    resume = build.infra("document_refresh_resume")
                known = resume.original_partial(run, build)
            else:
                known = False
            if known:
                assert resume is not None
                known_partials += 1
                build.need(known_partials == 1, "document_resume_duplicate_partial_history")
                if resuming_original is None:
                    if consumed is None:
                        consumed = resume.verify_consumed_partial(runs, build)
                    build.need(consumed is True, "document_refresh_prior_unknown_reconcile_only")
                # This is the same exact acknowledged operation, never a new
                # Build/recovery admission. Its carried old claim stays in roots.
                continue
            try:
                files = snapshot(run, build)
                binding = build.decode(files["native-proof/binding.json"])
                job = "hirewiz-doc-six-" + binding["build_identity"]["nonce"][:12]
                build.need(JOB.fullmatch(job), "document_refresh_recovery_exact_job_required")
            except Exception:  # noqa: BLE001 - partial journals never authorize a new mutation.
                raise build.RefreshDenied("document_refresh_prior_unknown_reconcile_only") from None
            pending.append((job, files, run))
    build.need(not rescue_expired_v2 or completed_count == 1,
               "document_rescue_prior_completed_journal_required")
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
    if not records and selected is None and not pre_native and not pre_native_receipts:
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
        open_rows, verified_pre_native, used_nonces = verify_pre_native_history(
            pre_native, pre_native_receipts, clients, probe, build)
        failed_jobs = set(facts)
        if chosen is not None:
            assert selected is not None
            failed_jobs.add(selected)
        mode = ({"retire_expired_v2": True} if retire_expired_v2 else
                {"rescue_expired_v2": True} if rescue_expired_v2 else {})
        if verified_pre_native:
            failed_jobs.update(fact["absent_native_job"].rsplit("/", 1)[-1] for fact in verified_pre_native)
            joined(clients, probe, build, failed_jobs, {fact["image"] for fact in verified_pre_native},
                   **mode)
        else:
            joined(clients, probe, build, failed_jobs, **mode)
        nonce = uuid.uuid4().hex if chosen is not None or open_rows else None
        build.need(nonce is None or nonce not in used_nonces,
                   "document_refresh_pre_native_successor_nonce_reused")
        if open_rows:
            assert nonce is not None
            root = directory / "pre-native-reconciliations"
            root.mkdir(mode=0o700)
            for fact, files, original in open_rows:
                build.need(pre_native_snapshot(original, build, history_root=True) == files,
                           "document_refresh_pre_native_original_changed")
                target = root / fact["nonce"]
                target.mkdir(mode=0o700)
                retain(files, target / "original")
                build.write_new(target / "reconciliation.json", {**fact, "version": 1,
                    "status": "pre_native_candidate_abandoned_not_acceptance", "successor_nonce": nonce,
                    "old_build_replayed": False, "native_acceptance": False, "promotion_performed": False})
        target_root = directory / "terminal-recoveries"
        if records or chosen is not None:
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
            return nonce
        assert selected is not None
        assert nonce is not None
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
