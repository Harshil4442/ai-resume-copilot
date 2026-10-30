"""One finite continuation of completed native source jobs; never Build/Create/Run.

The original artifact is immutable. New receipt/transition files are observations
and journals, never substitutes for the stock native proof or serving guards.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import time
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode

REPOSITORY = "Harshil4442/ai-resume-copilot"
WORKFLOW = ".github/workflows/document-refresh.yml"
ORIGINAL_RUN = 41108319542
ORIGINAL_ARTIFACT = 13003047976
ORIGINAL_SOURCE = "3cb27683ac884f5861a13d3cead2e3278bc7f3b6"
ORIGINAL_ZIP_SHA = "eb6fec60e019378149ec20670ad6952aa5372765cfdc8f69d73f9f0e4bbf99d6"
ORIGINAL_NAME = "document-refresh-full-source-refresh-41108319542-1"
BUILD_ID = "0d685ff9-d002-4e4b-912d-a8b64c6592ea"
NONCE = "0e31d2e468f2465da7db6ece28534e00"
IMAGE = "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:e2880102f6149cd62c800fbd33c775ed371c98a2ca74dce4935374685488fbef"
JOBS = {
    "inventory": ("06d3cda2-446d-49e0-b8c2-e35423cd528d", "e9fc7922-0d09-47a4-9752-bcc770015669"),
    "corpus": ("5cbfa761-31e4-415c-b03b-986233c7195d", "88a3c5e1-670e-41f0-90b0-dbd6b7776728"),
}
CLAIM_OBJECT = "hirewiz-document-refresh-source/terminal-recovery/" + JOBS["corpus"][0] + ".json"
MANIFEST_FILE = "completed-source-readback.json"
MAX_BYTES = 2 * 1024**2
OPERATOR_PATHS = ("infra/gcp/document_source_readback_resume.py",
                  "backend/scripts/resume_completed_document_source.py",
                  ".github/workflows/document-refresh.yml")
FILES = {'build-acknowledged.json': 'be76e8ef29d9676785e7da401f9683c2a4f2cc55a7e15bae69a3b537c7cdd800', 'build-create-started.json': 'b4f037bd19f33f86c09c2afdbd053e919b98312802621bc06cdf48ce8fba9d1d', 'build-started.json': 'fb28fa94d7dd0ae86afd49b4c69fb46ad5a19079d82d4c5426b869813aa6f811', 'local-native-proof.json': '9b08d966e3331a627541346bbf3c20241cd8ac2204102a5d4c0fe129e1c91e33', 'native-build-identity.json': 'a382942debe7fe395a01d4dbcd573a0636a13d05cf580cd58c5a730f47161ead', 'native-proof/binding.json': '6bc82a29d2cbe14ab830a43f333b405314f70fe0fc826a8d203f5cdcd088fd7c', 'native-proof/corpus.create.acknowledged.json': 'c86e321f51c3dbba66253310dcadd0a0ea6ccd2d4ba40b04c2cf6d956ea841fc', 'native-proof/corpus.create.started.json': '0ef7aad52abd760938ee6d6bc2aa7e1d43f70bf48defb6123ddf20f4c63da888', 'native-proof/corpus.run.acknowledged.json': '190ad8480801cc787e3457faf47705e8e0ee45ce8159ac7c214a597b3af6174a', 'native-proof/corpus.run.started.json': '681e461586eb67850f251495b36a924a21e881955ca74511d07caf94f7101167', 'native-proof/inventory.create.acknowledged.json': 'd5f7505b765859a8f7d0cf050bbc16fbe87e74d93424b217d1e35c3adaa99785', 'native-proof/inventory.create.started.json': '8a43a86087ffd91b8fdf606c5a16d280844fb1bcd775c73ef9ebd4f5737e48f0', 'native-proof/inventory.run.acknowledged.json': 'c7641af64b0a91be745c44151767f68d5ffb39f976df50bb7d2cda42bdaf04e3', 'native-proof/inventory.run.started.json': 'c225772d077e65ac55605184945a2da9e222a88f7626ca1c7946d1993aa03cf4', 'pre-native-reconciliations/40a80b1c8e1b484fb4299fddae9cae1a/original/build-acknowledged.json': '1c46966fac94cfaefc526177668a8c33f335df30478be2ee09fc2151eaf8432f', 'pre-native-reconciliations/40a80b1c8e1b484fb4299fddae9cae1a/original/build-create-started.json': 'aa7e3a40910c87aa869b8d763f6ff21f02b6bb9a6aa91c3f0ed1b6315b19451d', 'pre-native-reconciliations/40a80b1c8e1b484fb4299fddae9cae1a/original/build-started.json': '61b461165c544774ce9e8ad67d7e9057fb96b2435e0b925717f4589a9fa8e7b7', 'pre-native-reconciliations/40a80b1c8e1b484fb4299fddae9cae1a/original/native-build-identity.json': '00571e49ece61303742a652992528d5af2a730d4c288d9366463880d330ff583', 'pre-native-reconciliations/40a80b1c8e1b484fb4299fddae9cae1a/original/source-upload-acknowledged.json': '4bc2137c608af66487599a2b690efbb73601e5874cc297c1232195d4ff44dda7', 'pre-native-reconciliations/40a80b1c8e1b484fb4299fddae9cae1a/reconciliation.json': 'b5f477f3c66e63db61853239ccab23169f754723e8284fda158d4406b37458da', 'source-upload-acknowledged.json': '92fed6484a1a06348a9515091eb679f6ddf11eb208ec60d4a615d1f86b279cac'}


class ResumeDenied(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise ResumeDenied("document_source_readback_" + code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def github(path, *, binary=False):
    result = subprocess.run(["gh", "api", "repos/" + REPOSITORY + "/" + path], stdin=subprocess.DEVNULL,
                            capture_output=True, check=False, timeout=60)
    need(result.returncode == 0 and 0 < len(result.stdout) <= MAX_BYTES, "github_read_unavailable")
    return result.stdout if binary else json.loads(result.stdout)


def original_metadata(artifact, run):
    need(artifact.get("id") == ORIGINAL_ARTIFACT and artifact.get("name") == ORIGINAL_NAME
         and artifact.get("digest") == "sha256:" + ORIGINAL_ZIP_SHA and artifact.get("expired") is False
         and artifact.get("workflow_run", {}).get("id") == ORIGINAL_RUN
         and artifact.get("workflow_run", {}).get("head_sha") == ORIGINAL_SOURCE
         and artifact.get("workflow_run", {}).get("head_branch") == "main"
         and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_BYTES
         and run.get("id") == ORIGINAL_RUN and run.get("run_attempt") == 1
         and run.get("head_sha") == ORIGINAL_SOURCE and run.get("head_branch") == "main"
         and run.get("status") == "completed" and run.get("conclusion") == "failure"
         and run.get("path") == ".github/workflows/document-refresh.yml"
         and run.get("repository", {}).get("full_name") == REPOSITORY, "original_metadata_changed")


def archive(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "archive_bound")
    result: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        need(len(source.infolist()) <= 100, "archive_bound")
        for item in source.infolist():
            name = PurePosixPath(item.filename)
            need(not name.is_absolute() and ".." not in name.parts and "\\" not in item.filename
                 and not stat.S_ISLNK(item.external_attr >> 16) and not item.is_dir()
                 and name.suffix == ".json" and item.filename not in result
                 and 0 < item.file_size <= MAX_BYTES, "archive_path_refused")
            result[item.filename] = source.read(item)
            need(sum(map(len, result.values())) <= MAX_BYTES, "archive_bound")
    return result


def exact(files):
    need({name: sha(raw) for name, raw in files.items()} == FILES, "original_bytes_changed")
    return files


def read_tree(path):
    need(path.is_absolute() and path.resolve() == path and path.is_dir(), "private_directory_required")
    result: dict[str, bytes] = {}
    for entry in [path, *path.rglob("*")]:
        info = entry.lstat()
        need(info.st_uid == os.getuid() and not stat.S_ISLNK(info.st_mode), "private_file_required")
        if stat.S_ISDIR(info.st_mode):
            need(stat.S_IMODE(info.st_mode) == 0o700, "private_directory_required")
        else:
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
                 and 0 < info.st_size <= MAX_BYTES and len(result) < 100, "private_file_required")
            result[str(entry.relative_to(path))] = entry.read_bytes()
            need(sum(map(len, result.values())) <= MAX_BYTES, "private_file_bound")
    return result


def retain(files, target):
    need(target.is_absolute() and not target.exists() and target.parent.resolve() == target.parent,
         "fresh_output_required")
    target.mkdir(mode=0o700)
    for name, raw in files.items():
        destination = target / name
        parent = target
        for part in PurePosixPath(name).parts[:-1]:
            parent = parent / part
            parent.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


def retrieve(target):
    original_metadata(github(f"actions/artifacts/{ORIGINAL_ARTIFACT}"), github(f"actions/runs/{ORIGINAL_RUN}"))
    raw = github(f"actions/artifacts/{ORIGINAL_ARTIFACT}/zip", binary=True)
    need(sha(raw) == ORIGINAL_ZIP_SHA, "original_zip_changed")
    retain(exact(archive(raw)), target)


def modules(root):
    # Same import order as the normal release entrypoint, before SDK third_party.
    from infra.gcp import document_application_context  # noqa: F401
    from infra.gcp import document_refresh_probe as prior
    from infra.gcp import document_source_release as source
    return prior.build_module(), prior, source


def verify_local(files, root, *, historical=False):
    build, _, source = modules(root)
    exact(files)
    binding = json.loads(files["native-proof/binding.json"])
    identity = binding["build_identity"]
    need(identity.get("build_id") == BUILD_ID and identity.get("nonce") == NONCE
         and identity.get("immutable_image") == IMAGE and identity.get("source_commit") == ORIGINAL_SOURCE,
         "candidate_changed")
    probe = source.SourceProbe(identity, root=root)
    if not historical:
        probe.source_contract()
    need(len(probe.source.sources) == 22 and len(probe.source.runtime) == 20
         and probe.source.qualification_sha256 == "61b313bcb98d1d9931efd49ac8e9f8a3d307de0785e97e3c1705760a4c10ae50",
         "source_contract_changed")
    need(binding == {"version": 4, "image": IMAGE, "build_identity": identity}
         and identity["local_native_proof_sha256"] == sha(files["local-native-proof.json"]), "binding_changed")
    local = json.loads(files["local-native-proof.json"])
    issued = datetime.fromisoformat(local["corpus"][0]["scan"]["definitions"].split("/", 1)[1])
    build.proof_contract(local, build.accepted(), probe.source.sources, now=issued if historical else datetime.now(UTC),
                         image=IMAGE, build_identity=identity, source_revision=True)
    need(sum(len(local[key]) for key in ("corpus", "docx_layout_corpus", "pdf_reading_order_corpus", "request_clamd_corpus")) == 32,
         "local_case_count_changed")
    return build, probe, identity


def trusted_context(root):
    require_resume_controls()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    need(os.getenv("GITHUB_ACTIONS") == "true" and os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and os.getenv("GITHUB_REPOSITORY") == REPOSITORY and os.getenv("GITHUB_REF") == "refs/heads/main"
         and os.getenv("GITHUB_SHA") == head and os.getenv("GITHUB_RUN_ATTEMPT") == "1"
         and os.getenv("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW + "@refs/heads/main"
         and re.fullmatch(r"[1-9][0-9]{0,18}", os.getenv("GITHUB_RUN_ID", "")), "trusted_main_workflow_required")
    return {"run": int(os.environ["GITHUB_RUN_ID"]), "attempt": 1, "head": head, "path": WORKFLOW,
            "operator_sha256": operator_contract(root, head, working=True)}


def require_resume_controls():
    need(os.getenv("RESUME_COMPLETED_SOURCE_READBACK") == "true" and os.getenv("SOURCE_REVISION") == "true"
         and os.getenv("REFRESH_MODE") == "full-source-refresh" and not os.getenv("RECOVER_FAILED_NATIVE_JOB", "")
         and all(os.getenv(name, "false") == "false" for name in
                 ("RETIRE_EXPIRED_V2", "RESCUE_EXPIRED_V2", "UPGRADE_CURRENT_V2_TO_V3")), "resume_controls_required")


def operator_contract(root, commit, *, working=False):
    files = {}
    for path in OPERATOR_PATHS:
        raw = subprocess.check_output(["git", "show", commit + ":" + path], cwd=root, stderr=subprocess.DEVNULL)
        if working:
            current = root / path
            need(current.is_file() and not current.is_symlink() and current.read_bytes() == raw,
                 "operator_working_bytes_changed")
        files[path] = sha(raw)
    return sha(canonical(files))


def verify_operations(clients, files, probe):
    from infra.gcp import document_refresh_probe as prior
    observed = {}
    for mode, (job_uid, execution_uid) in JOBS.items():
        prefix = "native-proof/" + mode
        created = prior.operation_result(clients, json.loads(files[prefix + ".create.acknowledged.json"]), "Job")
        started = prior.operation_result(clients, json.loads(files[prefix + ".run.acknowledged.json"]), "Execution")
        need(created is not None and started is not None, "original_operations_not_terminal")
        job, execution, payload = probe._observation(clients, mode, probe.build)
        need(created.get("name") == job["name"] and created.get("uid") == job_uid == job["uid"]
             and started.get("name") == execution["name"] and started.get("uid") == execution_uid == execution["uid"]
             and job.get("executionCount") == 1, "original_native_identity_changed")
        body = probe.desired(IMAGE, probe.handles(probe.build)[mode]["nonce"], mode)
        need(json.loads(files[prefix + ".create.started.json"]) == {"body_sha256": sha(canonical(body)), "automatic_replay": False}
             and json.loads(files[prefix + ".run.started.json"]) == {
                 "name": job["name"], "uid": job_uid,
                 "etag": json.loads(files[prefix + ".run.started.json"])["etag"], "automatic_replay": False},
             "original_native_intent_changed")
        observed[mode] = {"job": job["name"], "job_uid": job_uid, "execution": execution["name"], "execution_uid": execution_uid}
    return observed


def empty_event_retry(action, *, sleep=time.sleep):
    from infra.gcp import document_refresh_probe as prior
    for attempt in range(3):
        try:
            return action()
        except prior.NativeProofDenied as error:
            if str(error) != "document_candidate_event_missing" or attempt == 2:
                raise
            sleep(2)
    raise AssertionError("unreachable")


def claim(http, body, output, build):
    need(body.get("kind") == "completed_native_readback_continuation_v1" and body.get("original_run") == ORIGINAL_RUN
         and body.get("candidate_nonce") == NONCE and body.get("corpus_job_uid") == JOBS["corpus"][0], "claim_body_changed")
    bucket = build.PROJECT + "_cloudbuild"
    encoded = quote(CLAIM_OBJECT, safe="")
    url = "https://storage.googleapis.com/storage/v1/b/" + bucket + "/o/" + encoded
    response, _ = http.request(url, method="GET", redirections=0)
    need(response.status == 404, "claim_exists_or_unknown_reconcile_only")
    build.write_new(output / "continuation-claim.started.json", {"body_sha256": sha(canonical(body)), "automatic_replay": False})
    response, raw = http.request("https://storage.googleapis.com/upload/storage/v1/b/" + bucket + "/o?" + urlencode({
        "uploadType": "media", "name": CLAIM_OBJECT, "ifGenerationMatch": "0"}), method="POST", body=canonical(body),
        headers={"Content-Type": "application/json"}, redirections=0)
    need(response.status in (200, 201), "claim_outcome_unknown_reconcile_only")
    value = json.loads(raw)
    acknowledgment = {key: value.get(key) for key in ("name", "bucket", "generation")}
    need(acknowledgment["name"] == CLAIM_OBJECT and acknowledgment["bucket"] == bucket
         and type(acknowledgment["generation"]) is str
         and re.fullmatch(r"[1-9][0-9]{0,19}", acknowledgment["generation"]), "claim_ack_unknown_reconcile_only")
    build.write_new(output / "continuation-claim.acknowledged.json", acknowledgment)
    verify_claim(http, body, acknowledgment)
    return acknowledgment


def verify_claim(http, body, acknowledgment):
    need(set(acknowledgment) == {"name", "bucket", "generation"} and acknowledgment["name"] == CLAIM_OBJECT
         and acknowledgment["bucket"] == "ai-resume-parser-482412_cloudbuild"
         and type(acknowledgment["generation"]) is str
         and re.fullmatch(r"[1-9][0-9]{0,19}", acknowledgment["generation"]), "claim_ack_changed")
    response, raw = http.request("https://storage.googleapis.com/download/storage/v1/b/" + acknowledgment["bucket"]
        + "/o/" + quote(CLAIM_OBJECT, safe="") + "?" + urlencode({"alt": "media", "generation": acknowledgment["generation"]}),
        method="GET", redirections=0)
    need(response.status == 200 and raw == canonical(body), "claim_readback_unknown_reconcile_only")


def claim_body(identity, native, preimage, context):
    parent = "projects/ai-resume-parser-482412/locations/us-central1/jobs/"
    names = {"inventory": ("1f2186a3d9a0", "7vc5n"), "corpus": ("0e31d2e468f2", "cshdp")}
    expected = {}
    for mode, (short, suffix) in names.items():
        job = parent + "hirewiz-doc-six-" + short
        expected[mode] = {"job": job, "job_uid": JOBS[mode][0],
                          "execution": job + "/executions/hirewiz-doc-six-" + short + "-" + suffix,
                          "execution_uid": JOBS[mode][1]}
    need(native == expected, "claim_native_identity_changed")
    return {"version": 1, "kind": "completed_native_readback_continuation_v1", "original_run": ORIGINAL_RUN,
            "original_artifact": ORIGINAL_ARTIFACT, "original_zip_sha256": ORIGINAL_ZIP_SHA,
            "original_files_sha256": sha(canonical(FILES)), "candidate_nonce": NONCE,
            "corpus_job_uid": JOBS["corpus"][0], "build_identity_sha256": sha(canonical(identity)),
            "native": native, "preimage_sha256": sha(canonical(preimage)), "continuation": context,
            "automatic_replay": False, "new_build_or_job": False}


def resume(original, output, bundle, *, root):
    context = trusted_context(root)
    files = exact(read_tree(original))
    build, probe, identity = verify_local(files, root)
    original_metadata(github(f"actions/artifacts/{ORIGINAL_ARTIFACT}"), github(f"actions/runs/{ORIGINAL_RUN}"))
    retain(files, output)
    from infra.gcp import document_consumed_native as consumed
    from infra.gcp import document_refresh_recovery as recovery
    clients, http = None, None
    try:
        clients = build.infra("document_refresh_probe").Clients()
        http = build.native_http()
        boundary = consumed.NativeAudit(root, bundle, clients, http, build.infra("document_refresh_probe"))
        consumed.history.audit(bundle, boundary)
        before = {"applications": boundary.verify_current_applications(), "document": boundary.verify_current_document(None)}
        need(before["document"]["current_native_acceptance"]["image"] != IMAGE, "candidate_already_serving_reconcile_only")
        receipts = [(NONCE, old, saved, receipt) for old, saved, receipt in recovery.pre_native_reconciliations(original, build)]
        opened, facts, _ = recovery.verify_pre_native_history([], receipts, clients, build.infra("document_refresh_probe"), build)
        need(not opened and len(facts) == 1, "prior_disposition_changed")
        recovery.joined(clients, build.infra("document_refresh_probe"), build, set(), {IMAGE}, application_context=boundary.applications)
        native = empty_event_retry(lambda: verify_operations(clients, files, probe))
        accepted = empty_event_retry(lambda: probe.verify_successor(IMAGE, identity, output / "native-proof"))
        current = {"applications": boundary.verify_current_applications(), "document": boundary.verify_current_document(None)}
        need(current == before and exact({name: (output / name).read_bytes() for name in FILES}) == files,
             "current_control_or_original_changed")
        body = claim_body(identity, native, before, context)
        build.write_new(output / "continuation-preimage.json", before)
        acknowledgment = claim(http, body, output, build)
        build.write_new(output / "native-proof/acceptance.json", asdict(accepted))
        # Exactly one stock call. Never retry this call: it can cross a PATCH boundary.
        result = build.infra("document_source_release").promote(identity, output / "native-proof", output / "transition",
                                                               application_context=boundary.applications, root=root)
        need(json.loads((output / "transition/native-acceptance.json").read_bytes()) == asdict(accepted),
             "transition_acceptance_changed_reconcile_only")
        build.write_new(output / "result.json", result)
        manifest = {"version": 1, "kind": "completed_source_readback_v1", "original_run": ORIGINAL_RUN,
                    "original_artifact": ORIGINAL_ARTIFACT, "original_zip_sha256": ORIGINAL_ZIP_SHA,
                    "original_files": FILES, "candidate_nonce": NONCE, "build_identity_sha256": sha(canonical(identity)),
                    "continuation": context, "claim_body": body, "claim_acknowledgment": acknowledgment,
                    "result_sha256": sha(canonical(result))}
        build.write_new(output / MANIFEST_FILE, manifest)
        return result
    finally:
        if clients is not None:
            clients.close()
        if http is not None:
            http.close()


def canonical_success(run_path, *, root):
    files = read_tree(run_path)
    need(MANIFEST_FILE in files, "canonical_manifest_missing")
    manifest = json.loads(files[MANIFEST_FILE])
    context = manifest.get("continuation", {})
    need(type(context.get("run")) is int and context["run"] > 0 and context.get("attempt") == 1
         and context.get("path") == WORKFLOW and re.fullmatch(r"[a-f0-9]{40}", context.get("head", ""))
         and re.fullmatch(r"[1-9][0-9]{0,18}", run_path.name), "canonical_context_changed")
    need(subprocess.run(["git", "merge-base", "--is-ancestor", context["head"], "HEAD"], cwd=root,
                        capture_output=True, check=False).returncode == 0
         and context.get("operator_sha256") == operator_contract(root, context["head"])
         == operator_contract(root, "HEAD", working=True), "canonical_operator_changed")
    run = github(f"actions/runs/{context['run']}")
    need(run.get("head_sha") == context["head"] and run.get("id") == context["run"] and run.get("run_attempt") == 1
         and run.get("path") == WORKFLOW and run.get("head_branch") == "main" and run.get("status") == "completed"
         and run.get("conclusion") == "success" and run.get("event") == "workflow_dispatch"
         and run.get("repository", {}).get("full_name") == REPOSITORY, "canonical_run_not_successful")
    artifacts = github(f"actions/runs/{context['run']}/artifacts?per_page=100").get("artifacts", [])
    candidates = [a for a in artifacts if a.get("name") == f"document-refresh-full-source-refresh-{context['run']}-1"]
    need(len(candidates) == 1, "canonical_artifact_required")
    artifact = candidates[0]
    need(type(artifact.get("id")) is int and run_path.name == str(artifact["id"])
         and artifact.get("expired") is False and artifact.get("workflow_run", {}).get("id") == context["run"]
         and artifact.get("workflow_run", {}).get("head_sha") == context["head"]
         and artifact.get("workflow_run", {}).get("head_branch") == "main"
         and re.fullmatch(r"sha256:[a-f0-9]{64}", artifact.get("digest", "")), "canonical_artifact_changed")
    raw = github(f"actions/artifacts/{artifact['id']}/zip", binary=True)
    need("sha256:" + sha(raw) == artifact["digest"] and archive(raw) == files, "canonical_artifact_bytes_changed")
    need({name: sha(files.get(name, b"")) for name in FILES} == FILES, "canonical_original_changed")
    build, _, identity = verify_local({name: files[name] for name in FILES}, root, historical=True)
    result = json.loads(files["result.json"])
    body = manifest.get("claim_body", {})
    expected = {"version": 1, "kind": "completed_source_readback_v1", "original_run": ORIGINAL_RUN,
                "original_artifact": ORIGINAL_ARTIFACT, "original_zip_sha256": ORIGINAL_ZIP_SHA, "original_files": FILES,
                "candidate_nonce": NONCE, "build_identity_sha256": sha(canonical(identity)), "continuation": context,
                "claim_body": body, "claim_acknowledgment": manifest.get("claim_acknowledgment"), "result_sha256": sha(canonical(result))}
    need(manifest == expected and body == claim_body(identity, body.get("native"), json.loads(files["continuation-preimage.json"]), context)
         and json.loads(files["continuation-claim.started.json"]) == {"body_sha256": sha(canonical(body)), "automatic_replay": False}
         and json.loads(files["continuation-claim.acknowledged.json"]) == manifest["claim_acknowledgment"],
         "canonical_manifest_changed")
    http = build.native_http()
    try:
        verify_claim(http, body, manifest["claim_acknowledgment"])
    finally:
        http.close()
    recovery = build.infra("document_refresh_recovery")
    recovery.completed_serving(run_path, result, run_path / "native-proof", build, build.infra("document_refresh_probe"), allow_v3=True)
    return manifest


def select_history(history, target, *, root):
    need(history.is_absolute() and history.is_dir() and not target.exists(), "history_path_refused")
    runs = list(history.iterdir())
    need(len(runs) <= 3 and all(p.is_dir() and not p.is_symlink()
        and re.fullmatch(r"[1-9][0-9]{0,18}", p.name) for p in runs), "history_bound")
    originals = [p for p in runs if p.name == str(ORIGINAL_ARTIFACT)]
    canonical = [p for p in runs if (p / MANIFEST_FILE).is_file()]
    need(len(originals) <= 1 and len(canonical) <= 1, "duplicate_history_refused")
    selected = None
    if not originals:
        return {"history_directory": str(history), "original_retained_unchanged": True,
                "excluded_original_run": None, "canonical_success_run": None}
    if originals:
        original_metadata(github(f"actions/artifacts/{ORIGINAL_ARTIFACT}"), github(f"actions/runs/{ORIGINAL_RUN}"))
        exact(read_tree(originals[0]))
        need(len(canonical) == 1, "completed_consumption_required")
        selected = canonical_success(canonical[0], root=root)
        need(selected["original_files"] == FILES, "completed_consumption_changed")
    target.mkdir(mode=0o700)
    for run in runs:
        if originals and run == originals[0]:
            continue
        retain(read_tree(run), target / run.name)
    return {"history_directory": str(target), "original_retained_unchanged": True,
            "excluded_original_run": ORIGINAL_RUN if selected else None,
            "canonical_success_run": selected["continuation"]["run"] if selected else None}
