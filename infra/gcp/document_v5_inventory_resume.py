"""Continue the exact acknowledged V5 inventory without replaying it or its Build."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from infra.gcp import document_source_readback_resume as readback

REPOSITORY = readback.REPOSITORY
WORKFLOW = readback.WORKFLOW
MODE = "resume-v5-inventory"
ORIGINAL_RUN = 44348967639
ORIGINAL_ARTIFACT = 14446629631
ORIGINAL_SOURCE = "d2cc2212315888967fba2adfe539aa07bd7e5c8e"
ORIGINAL_ZIP_SHA256 = "17cac034d1958b1d7106fe77b021876d0ba66e0d5ad314cfbf8891855b8ab4c9"
ORIGINAL_TREE_SHA256 = "51c3374fb653b88e1512fd31340a2939fdd92ad63c66f6b24cd4646e1c6a0432"
ORIGINAL_NAME = f"document-refresh-recover-source-v5-{ORIGINAL_RUN}-1"
BUILD_ID = "052c992c-f143-4386-8fac-ac24cc2d3fd8"
NONCE = "c63b9773743645f99c438fee603a959b"
IMAGE_DIGEST = "sha256:fe6966c3dac8b7d99d3bdcce16854d100e95450f079422af9b1afc4b691fa153"
INVENTORY_JOB = "hirewiz-doc-six-5dbd0d03d24c"
INVENTORY_JOB_UID = "a108e3b8-775c-4e3a-afc2-34985120fd7f"
INVENTORY_EXECUTION = INVENTORY_JOB + "-2zzhg"
INVENTORY_EXECUTION_UID = "c6253816-8437-4b7f-aec1-2d9c41b37643"
CORPUS_JOB = "hirewiz-doc-six-c63b97737436"
DEFINITIONS_ISSUED = datetime(2026, 11, 18, 6, 28, 39, tzinfo=UTC)
DEFINITIONS_DEADLINE = DEFINITIONS_ISSUED + timedelta(days=1)
CLAIM_DIR = "v5-inventory-continuation"
MANIFEST_FILE = "completed-v5-inventory-continuation.json"
LOG_VISIBILITY_SECONDS = 90


class ResumeDenied(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise ResumeDenied("document_v5_inventory_resume_" + code)


def tree_digest(files):
    return readback.sha(readback.canonical({name: readback.sha(raw) for name, raw in files.items()}))


def exact(files):
    need(len(files) == 25 and tree_digest(files) == ORIGINAL_TREE_SHA256, "original_tree_changed")
    return files


def read_tree(path):
    return exact(readback.read_tree(path))


def original_metadata(artifact, run):
    artifact_run = artifact.get("workflow_run", {})
    need(artifact.get("id") == ORIGINAL_ARTIFACT and artifact.get("name") == ORIGINAL_NAME
         and artifact.get("digest") == "sha256:" + ORIGINAL_ZIP_SHA256 and artifact.get("expired") is False
         and artifact_run.get("id") == ORIGINAL_RUN and artifact_run.get("head_sha") == ORIGINAL_SOURCE
         and artifact_run.get("head_branch") == "main" and type(artifact.get("size_in_bytes")) is int
         and 0 < artifact["size_in_bytes"] <= readback.MAX_BYTES
         and run.get("id") == ORIGINAL_RUN and run.get("run_attempt") == 1
         and run.get("head_sha") == ORIGINAL_SOURCE and run.get("head_branch") == "main"
         and run.get("event") == "workflow_dispatch" and run.get("status") == "completed"
         and run.get("conclusion") == "failure" and run.get("path") == WORKFLOW
         and run.get("repository", {}).get("full_name") == REPOSITORY, "original_metadata_changed")


def verify_original_metadata():
    original_metadata(readback.github(f"actions/artifacts/{ORIGINAL_ARTIFACT}"),
                      readback.github(f"actions/runs/{ORIGINAL_RUN}"))


def retrieve(target: Path) -> None:
    """Retrieve only the reviewed GitHub artifact into an exclusive private directory."""
    verify_original_metadata()
    raw = readback.github(f"actions/artifacts/{ORIGINAL_ARTIFACT}/zip", binary=True)
    need(readback.sha(raw) == ORIGINAL_ZIP_SHA256, "original_zip_changed")
    readback.retain(exact(readback.archive(raw)), target)


def trusted_context(root: Path):
    def git(*arguments):
        return subprocess.run(["git", *arguments], cwd=root, stdin=subprocess.DEVNULL, capture_output=True,
                              check=True, timeout=15).stdout

    head = git("rev-parse", "HEAD").decode().strip()
    run = os.getenv("GITHUB_RUN_ID", "")
    need(os.getenv("GITHUB_ACTIONS") == "true" and os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and os.getenv("GITHUB_REPOSITORY") == REPOSITORY and os.getenv("GITHUB_REF") == "refs/heads/main"
         and os.getenv("GITHUB_SHA") == head and os.getenv("GITHUB_RUN_ATTEMPT") == "1"
         and os.getenv("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW + "@refs/heads/main"
         and re.fullmatch(r"[1-9][0-9]{0,18}", run) and int(run) > ORIGINAL_RUN
         and os.getenv("REFRESH_MODE") == MODE and not os.getenv("RECOVER_FAILED_NATIVE_JOB")
         and all(os.getenv(name, "false") == "false" for name in
                 ("RESUME_COMPLETED_SOURCE_READBACK", "RETIRE_EXPIRED_V2", "RESCUE_EXPIRED_V2",
                  "UPGRADE_CURRENT_V2_TO_V3")), "trusted_main_required")
    git("merge-base", "--is-ancestor", ORIGINAL_SOURCE, head)
    files = {}
    for name in ("infra/gcp/document_v5_inventory_resume.py", WORKFLOW):
        raw = git("show", f"{head}:{name}")
        path = root / name
        need(path.is_file() and not path.is_symlink() and path.read_bytes() == raw, "operator_source_changed")
        files[name] = readback.sha(raw)
    return {"run": int(run), "attempt": 1, "head": head, "path": WORKFLOW,
            "operator_sha256": readback.sha(readback.canonical(files))}


def require_fresh(now):
    need(isinstance(now, datetime) and now.tzinfo is not None and DEFINITIONS_ISSUED <= now < DEFINITIONS_DEADLINE,
         "definitions_outside_admission_window")


def verify_local(files, root: Path, *, now):
    build, prior, source = readback.modules(root)
    exact(files)
    binding = json.loads(files["native-proof/binding.json"])
    identity = binding["build_identity"]
    image = build.REPOSITORY + "@" + IMAGE_DIGEST
    need(binding == {"version": 5, "image": image, "build_identity": identity}
         and identity == {**json.loads(files["native-build-identity.json"]),
                          "local_native_proof_sha256": readback.sha(files["local-native-proof.json"])}
         and identity["build_id"] == BUILD_ID and identity["nonce"] == NONCE
         and identity["immutable_image"] == image and identity["source_commit"] == ORIGINAL_SOURCE
         and identity["kind"] == source.v5_build_kind(), "candidate_binding_changed")
    probe = source.SourceProbe(identity, root=root)
    probe.source_contract()
    require_fresh(now)
    local = json.loads(files["local-native-proof.json"])
    facts = build.proof_contract(local, build.accepted(), probe.source.sources, now=now, image=image,
                                 build_identity=identity, source_revision=True, source_v5=True)
    need(facts["issued_at_utc"] == DEFINITIONS_ISSUED.isoformat(), "definition_identity_changed")
    return build, prior, source, probe, identity


def verify_inventory(clients, prior, files, probe, identity):
    prefix = "native-proof/inventory"
    image, handle = identity["immutable_image"], probe.handles(identity)["inventory"]["nonce"]
    wanted = probe.desired(image, handle, "inventory")
    resource = prior.PARENT + "/jobs/" + INVENTORY_JOB
    execution = resource + "/executions/" + INVENTORY_EXECUTION
    create_marker = json.loads(files[prefix + ".create.started.json"])
    run_marker = json.loads(files[prefix + ".run.started.json"])
    need(wanted["name"] == resource and create_marker == {
        "body_sha256": readback.sha(readback.canonical(wanted)), "automatic_replay": False}
        and run_marker == {"name": resource, "uid": INVENTORY_JOB_UID, "etag": run_marker.get("etag"),
                           "automatic_replay": False}
        and type(run_marker["etag"]) is str and bool(run_marker["etag"]), "inventory_intent_changed")
    created = prior.operation_result(clients, json.loads(files[prefix + ".create.acknowledged.json"]), "Job")
    executed = prior.operation_result(clients, json.loads(files[prefix + ".run.acknowledged.json"]), "Execution")
    job, observed, event = probe._observation(clients, "inventory", identity)
    need(created is not None and executed is not None and created.get("name") == job["name"] == resource
         and created.get("uid") == job.get("uid") == INVENTORY_JOB_UID
         and executed.get("name") == observed["name"] == execution
         and executed.get("uid") == observed.get("uid") == INVENTORY_EXECUTION_UID
         and prior.integer(job.get("executionCount"), 1), "inventory_operation_changed")
    probe.validate_inventory(event, image, probe.source_contract())
    return {"job": resource, "job_uid": INVENTORY_JOB_UID, "execution": execution,
            "execution_uid": INVENTORY_EXECUTION_UID, "receipt_sha256": probe.sha(event)}


def verify_v4_claim(original, files, clients, prior, build, recovery):
    claim = "failed-v4-inventory/"
    predecessor = recovery.failed_v4_snapshot(original / claim / "original", build)
    fact = recovery.verify_failed_v4(predecessor, clients, prior, build)
    body = recovery.failed_v4_claim_body(fact, NONCE)
    need(json.loads(files[claim + "disposition.json"]) == body
         and json.loads(files[claim + "claim.started.json"]) == {
             "body_sha256": readback.sha(readback.canonical(body)), "automatic_replay": False}, "v4_claim_changed")
    recovery.verify_claim(body, json.loads(files[claim + "claim.acknowledged.json"]), build)
    return body


def require_corpus_absent(clients, name):
    try:
        clients.job(name)
    except clients.errors.HttpError as error:
        need(error.status_code == 404, "corpus_absence_unverified")
    else:
        raise ResumeDenied("document_v5_inventory_resume_corpus_exists_reconcile_only")


def claim_body(identity, inventory, preimage, context, v4_claim, wanted):
    need(inventory["job_uid"] == INVENTORY_JOB_UID and identity["nonce"] == NONCE
         and wanted["name"].rsplit("/", 1)[-1] == CORPUS_JOB, "claim_scope_changed")
    return {"version": 1, "status": "v5_inventory_verified_corpus_only_continuation", "job_uid": INVENTORY_JOB_UID,
            "original_run": ORIGINAL_RUN, "original_artifact": ORIGINAL_ARTIFACT,
            "original_zip_sha256": ORIGINAL_ZIP_SHA256, "original_tree_sha256": ORIGINAL_TREE_SHA256,
            "candidate_nonce": NONCE, "build_identity_sha256": readback.sha(readback.canonical(identity)),
            "inventory": inventory, "preimage_sha256": readback.sha(readback.canonical(preimage)),
            "original_v4_claim_sha256": readback.sha(readback.canonical(v4_claim)), "corpus_job": wanted["name"],
            "corpus_request_sha256": readback.sha(readback.canonical(wanted)), "continuation": context,
            "automatic_replay": False, "new_build_or_inventory": False}


def wait_for_corpus_log(action, prior, *, monotonic=time.monotonic, sleep=time.sleep):
    """Repeat only a successful corpus execution's exact Cloud Logging read."""
    deadline = monotonic() + LOG_VISIBILITY_SECONDS
    while True:
        try:
            return action()
        except prior.NativeProofDenied as error:
            remaining = deadline - monotonic()
            if str(error) != "document_candidate_event_missing" or remaining <= 0:
                raise
            sleep(min(5, remaining))


def run_corpus(clients, prior, build, probe, identity, wanted, native):
    image = identity["immutable_image"]
    plan = {"image": image, "mode": "corpus", "job_nonce": probe.handles(identity)["corpus"]["nonce"]}
    clients.preflight()
    require_corpus_absent(clients, wanted["name"])
    require_fresh(datetime.now(UTC))
    build.write_new(native / "corpus.create.started.json", {
        "body_sha256": readback.sha(readback.canonical(wanted)), "automatic_replay": False})
    created = clients.create(wanted)
    build.write_new(native / "corpus.create.acknowledged.json", {"name": created.get("name")})
    deadline = time.monotonic() + 900
    created_job = prior.wait_operation(clients, created, "Job", deadline)
    job = clients.job(wanted["name"])
    probe.validate_job(job, plan)
    need(created_job.get("name") == job["name"] == wanted["name"]
         and created_job.get("uid") == job.get("uid") and isinstance(job.get("uid"), str)
         and prior.UUID.fullmatch(job["uid"]) and type(job.get("etag")) is str and bool(job["etag"])
         and prior.integer(job.get("executionCount", 0), 0)
         and job.get("latestCreatedExecution") in (None, {}), "fresh_corpus_job_unverified")
    require_fresh(datetime.now(UTC))
    build.write_new(native / "corpus.run.started.json", {"name": job["name"], "uid": job["uid"],
                    "etag": job["etag"], "automatic_replay": False})
    executed = clients.start(job)
    build.write_new(native / "corpus.run.acknowledged.json", {"name": executed.get("name")})
    execution = prior.wait_operation(clients, executed, "Execution", deadline)
    observed_job, observed_execution, _ = wait_for_corpus_log(lambda: probe._observation(clients, "corpus", identity), prior)
    need(observed_job.get("uid") == job["uid"] and prior.integer(observed_job.get("executionCount"), 1)
         and execution.get("uid") == observed_execution.get("uid")
         and execution.get("name") == observed_execution.get("name"), "corpus_execution_changed")
    accepted = probe.verify_successor(image, identity, native)
    need(accepted.job_uid == job["uid"] and accepted.execution_uid == observed_execution["uid"],
         "corpus_acceptance_changed")
    return accepted


def resume(original: Path, output: Path, *, root: Path) -> dict:
    """Verify the owned inventory and continue the sole missing V5 corpus once."""
    context = trusted_context(root)
    files = read_tree(original)
    verify_original_metadata()
    build, prior, source, probe, identity = verify_local(files, root, now=datetime.now(UTC))
    from infra.gcp import document_consumed_native as consumed
    from infra.gcp import document_refresh_recovery as recovery

    image = identity["immutable_image"]
    old_image = build.REPOSITORY + "@" + recovery.EXPIRED_SOURCE_PARENT
    clients = prior.Clients()
    http = None
    try:
        http = build.native_http()
        boundary = consumed.NativeAudit(root, None, clients, http, prior, recover_expired_source=True)
        before = {"applications": boundary.verify_current_applications(), "document": boundary.verify_current_document(None)}
        need(before["document"]["expired_structural_predecessor"]["image"] == old_image,
             "serving_predecessor_changed")
        recovery.verify_failed_v4_serving(clients, prior, build)
        v4_claim = verify_v4_claim(original, files, clients, prior, build, recovery)
        need(probe.build_identity(read_only_release=False, build_id=BUILD_ID, nonce=NONCE, image=image) == identity,
             "candidate_build_changed")
        inventory = verify_inventory(clients, prior, files, probe, identity)
        wanted = probe.desired(image, probe.handles(identity)["corpus"]["nonce"], "corpus")
        need(wanted["name"] == prior.PARENT + "/jobs/" + CORPUS_JOB, "corpus_scope_changed")
        require_corpus_absent(clients, wanted["name"])
        after = {"applications": boundary.verify_current_applications(), "document": boundary.verify_current_document(None)}
        need(after == before and read_tree(original) == files, "current_control_or_original_changed")
        recovery.verify_failed_v4_serving(clients, prior, build)
        readback.retain(files, output)
        need(read_tree(output) == files, "copied_original_changed")
        build.write_new(output / "continuation-preimage.json", before)
        claims = output / CLAIM_DIR
        claims.mkdir(mode=0o700)
        body = claim_body(identity, inventory, before, context, v4_claim, wanted)
        require_fresh(datetime.now(UTC))
        recovery.claim_successor(body, claims, build)

        native = output / "native-proof"
        accepted = run_corpus(clients, prior, build, probe, identity, wanted, native)
        build.write_new(native / "acceptance.json", asdict(accepted))
        require_fresh(datetime.now(UTC))
        recovery.verify_failed_v4_serving(clients, prior, build)
        need({"applications": boundary.verify_current_applications(), "document": boundary.verify_current_document(None)}
             == before, "pretransition_control_changed")
        result = source.promote(identity, native, output / "transition", application_context=boundary.applications,
                                root=root, recover_expired_source=True)
        need(json.loads((output / "transition/native-acceptance.json").read_bytes()) == asdict(accepted)
             and result.get("prior_image") == old_image and result.get("definition_expires_at") == accepted.definition_expires_at,
             "transition_acceptance_changed_reconcile_only")
        build.write_new(output / MANIFEST_FILE, {"version": 1, "kind": "completed_v5_inventory_continuation_v1",
            "claim_body": body, "claim_acknowledgment": json.loads((claims / "claim.acknowledged.json").read_bytes()),
            "result_sha256": readback.sha(readback.canonical(result)), "automatic_replay": False})
        build.write_new(output / "result.json", result)
        return result
    finally:
        clients.close()
        if http is not None:
            http.close()
