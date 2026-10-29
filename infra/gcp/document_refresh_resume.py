"""Finite continuation of the one retained, already-built full-source candidate.

No source object or Build is created. Original upload/Build ACKs and consumed
terminal recovery evidence remain byte-exact. A create-only claim makes a new
runner unable to replay an unknown native continuation.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode


def canonical(value: Any) -> bytes:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def partial_snapshot(original: Path, build: Any) -> dict[str, bytes]:
    recovery = build.infra("document_refresh_recovery")
    profile = build.infra("document_renderer_profiles")
    recovery.private(original, build)
    files = {}
    directories = {str(parent) for name in profile.PARTIAL_FILES
                   for parent in Path(name).parents if str(parent) != "."}
    seen_directories = set()
    for path in original.rglob("*"):
        info = path.lstat()
        build.need(not stat.S_ISLNK(info.st_mode), "document_resume_original_custody_changed")
        if stat.S_ISDIR(info.st_mode):
            name = str(path.relative_to(original))
            build.need(name in directories, "document_resume_unknown_or_started_journal")
            seen_directories.add(name)
            recovery.private(path, build)
        else:
            name = str(path.relative_to(original))
            build.need(name in profile.PARTIAL_FILES, "document_resume_unknown_or_started_journal")
            files[name] = recovery.read(path, build)
    build.need(seen_directories == directories
               and {name: sha(raw) for name, raw in files.items()} == profile.PARTIAL_FILES
               and sha(canonical(profile.PARTIAL_FILES)) == profile.PARTIAL_MANIFEST_SHA256,
               "document_resume_original_manifest_changed")
    return files


def retain_partial(files: dict[str, bytes], target: Path) -> None:
    target.mkdir(mode=0o700)
    for name, raw in files.items():
        destination = target / name
        parent = target
        for part in Path(name).parts[:-1]:
            parent = parent / part
            parent.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_carried_recovery(retained: Path, build: Any, probe: Any) -> None:
    """Reverify the consumed old claim; never issue another old recovery claim."""
    recovery = build.infra("document_refresh_recovery")
    root = retained / "terminal-recoveries/hirewiz-doc-six-cebe8490883c"
    files = recovery.snapshot(root / "original", build)
    raw = {name: recovery.read(root / name, build) for name in
           ("disposition.json", "claim.started.json", "claim.acknowledged.json")}
    clients = probe.Clients()
    try:
        fact = recovery.verify_failed(root / "original", files, clients, probe, build)
        body = build.decode(raw["disposition.json"])
        build.need(body == {**fact, "version": 1, "status": "terminal_failure_reconciled_not_acceptance",
            "successor_nonce": body.get("successor_nonce"), "failed_job_replayed": False}
            and fact["failed_job"].rsplit("/", 1)[-1] == "hirewiz-doc-six-cebe8490883c"
            and type(body["successor_nonce"]) is str and re.fullmatch(r"[a-f0-9]{32}", body["successor_nonce"])
            and build.decode(raw["claim.started.json"]) == {"body_sha256": sha(canonical(body)), "automatic_replay": False},
            "document_resume_terminal_disposition_changed")
        recovery.verify_claim(body, build.decode(raw["claim.acknowledged.json"]), build)
        recovery.joined(clients, probe, build, {"hirewiz-doc-six-cebe8490883c"})
    finally:
        clients.close()


def verify_partial_build(files: dict[str, bytes], build: Any) -> dict[str, Any]:
    """Fresh actual Build and generation-bound archive, not adopted saved success."""
    profile = build.infra("document_renderer_profiles")
    identity = build.decode(files["native-build-identity.json"])
    build.need(identity == profile.BUILD, "document_resume_original_build_changed")
    actual = build.verify_current_build(identity["build_id"], identity["nonce"], identity["immutable_image"])
    build.need(profile.matches_build(actual)
               and identity == {key: value for key, value in actual.items() if key != "source_sha256"},
               "document_resume_fresh_build_changed")
    source = {"bucket": identity["source_bucket"], "object": identity["source_object"],
              "generation": identity["source_generation"]}
    encoded = base64.urlsafe_b64encode(identity["build_id"].encode()).decode().rstrip("=")
    build.need(build.decode(files["source-upload-acknowledged.json"]) == source
        and build.decode(files["build-started.json"]) == {"nonce": identity["nonce"],
            "recipe_sha256": identity["refresh_dockerfile_sha256"],
            "source_archive_sha256": identity["source_archive_sha256"], "automatic_replay": False,
            "unknown_outcome": "reconcile_exact_native_object_and_build_only"}
        and build.decode(files["build-create-started.json"]) == {"nonce": identity["nonce"], "automatic_replay": False,
            "request_sha256": sha(canonical(build.full_source_build_request(
                identity["nonce"], source, historical_profile=True)))}
        and build.decode(files["build-acknowledged.json"]) == {"nonce": identity["nonce"],
            "build_id": identity["build_id"], "operation_name": "operations/build/" + build.PROJECT + "/" + encoded,
            "automatic_replay": False}, "document_resume_original_ack_changed")
    return actual


def claim(directory: Path, original: Path, identity: dict[str, Any], manifest: str, build: Any) -> dict[str, Any]:
    profile = build.infra("document_renderer_profiles")
    build.need(profile.matches_build(identity) and manifest == profile.PARTIAL_MANIFEST_SHA256,
               "document_resume_claim_binding_changed")
    names = ("backend/scripts/document_refresh.py", "infra/gcp/document_refresh_resume.py",
             "infra/gcp/document_renderer_profiles.py", "infra/gcp/document_refresh_probe.py",
             "infra/gcp/document_refresh_transition.py", "infra/gcp/document_refresh_recovery.py",
             "infra/gcp/document_refresh_auth.py")
    source = {name: sha((build.ROOT / name).read_bytes()) for name in names}
    name = "hirewiz-document-refresh-source/" + profile.BUILD["nonce"] + "/resume.json"
    body = {"version": 1, "kind": "same_full_source_candidate_resume_v1", "original_manifest_sha256": manifest,
            "build_identity": identity, "maintenance_source_sha256": source, "automatic_replay": False}
    http = build.native_http()
    try:
        metadata_uri = "https://storage.googleapis.com/storage/v1/b/" + build.PROJECT + "_cloudbuild/o/" + quote(name, safe="")
        response, _ = http.request(metadata_uri, method="GET", redirections=0)
        build.need(response.status == 404, "document_resume_claim_exists_or_unknown_read_only_reconcile")
        marker = {"claim_object": name, "body_sha256": sha(canonical(body)), "original_manifest_sha256": manifest,
                  "build_id": identity["build_id"], "immutable_image": identity["immutable_image"],
                  "automatic_replay": False, "output_directory": str(directory)}
        # Append-only original marker survives the caller's local re-entry. The
        # original nineteen files remain unchanged; GCS also guards new runners.
        build.write_new(original / "resume.started.json", marker)
        build.write_new(directory / "resume.started.json", marker)
        for path in (original, directory):
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        response, raw = http.request("https://storage.googleapis.com/upload/storage/v1/b/" + build.PROJECT + "_cloudbuild/o?"
            + urlencode({"uploadType": "media", "name": name, "ifGenerationMatch": "0"}),
            method="POST", body=canonical(body), headers={"Content-Type": "application/json"}, redirections=0)
        build.need(response.status in (200, 201), "document_resume_claim_unknown_read_only_reconcile")
        value = build.decode(raw)
        acknowledgment = {key: value.get(key) for key in ("name", "bucket", "generation")}
        build.need(acknowledgment["name"] == name and acknowledgment["bucket"] == build.PROJECT + "_cloudbuild"
            and type(acknowledgment["generation"]) is str
            and re.fullmatch(r"[1-9][0-9]{0,19}", acknowledgment["generation"]), "document_resume_claim_ack_changed")
        build.write_new(directory / "resume.claim.acknowledged.json", acknowledgment)
        response, raw = http.request("https://storage.googleapis.com/download/storage/v1/b/" + acknowledgment["bucket"]
            + "/o/" + quote(name, safe="") + "?" + urlencode({"alt": "media", "generation": acknowledgment["generation"]}),
            method="GET", redirections=0)
        build.need(response.status == 200 and raw == canonical(body), "document_resume_claim_readback_changed")
        build.write_new(directory / "resume.claim.checked.json", {"body": body, "acknowledgment": acknowledgment})
        return acknowledgment
    finally:
        http.close()


def resume_candidate(directory: Path, original: Path, *, manifest_sha256: str, publish: bool,
                     now: datetime, build: Any) -> dict[str, Any]:
    """Fresh local acceptance of the exact acknowledged candidate; no Build POST."""
    profile = build.infra("document_renderer_profiles")
    build.need(publish is True and manifest_sha256 == profile.PARTIAL_MANIFEST_SHA256,
               "document_resume_exact_reviewed_candidate_required")
    files = partial_snapshot(original, build)
    retained = directory / "original-candidate"
    retain_partial(files, retained)
    build.write_new(directory / "original-artifact-manifest.json", {"sha256": manifest_sha256, "files": profile.PARTIAL_FILES})
    actual = verify_partial_build(files, build)
    probe = build.infra("document_refresh_probe")
    verify_carried_recovery(retained, build, probe)
    sys.path.insert(0, str(build.ROOT / "backend"))
    from scripts import document_test_image as engines

    _, before, policy = build.full_source_snapshot()
    build.need(before == profile.SOURCES and policy == profile.BUILD["policy_sha256"],
               "document_resume_source_profile_changed")
    digest = actual["immutable_image"]
    docker = engines.docker_command()
    build.command(docker + ["pull", digest], seconds=180)
    installed = build.decode(build.command(docker + ["image", "inspect", digest], seconds=15))
    build.need(type(installed) is list and len(installed) == 1 and digest in installed[0].get("RepoDigests", []),
               "document_refresh_pulled_digest_unverified")
    proof = engines.check("linux/amd64", image=installed[0]["Id"])
    facts = build.proof_contract(proof, build.accepted(), before, now=datetime.now(UTC),
                                 image=digest, build_identity=actual)
    build.need(build.full_source_snapshot()[1:] == (before, policy), "document_refresh_source_changed")
    proof_sha = build.write_new(directory / "local-native-proof.json", proof)
    observed = build.definition(build.header(docker, proof["image_id"]), now=datetime.now(UTC))
    build.need(observed["issued_at_utc"] == facts["issued_at_utc"] and observed["status"] == "fresh",
               "document_refresh_built_header_mismatch")
    identity = {**actual, "local_native_proof_sha256": proof_sha}
    build.verify_native_build(identity)
    clients = probe.Clients()
    try:
        clients.preflight()
        name = probe.PARENT + "/jobs/hirewiz-doc-six-" + identity["nonce"][:12]
        try:
            clients.job(name)
        except clients.errors.HttpError as error:
            build.need(error.status_code == 404, "document_resume_native_absence_unverified")
        else:
            raise build.RefreshDenied("document_resume_native_job_exists_read_only_reconcile")
    finally:
        clients.close()
    acknowledgment = claim(directory, original, identity, manifest_sha256, build)
    build.write_new(directory / "resume-build-identity.json", identity)
    return {"mode": "resume-full-source-candidate", "status": "candidate_requires_native_acceptance",
            "candidate_registry_digest": digest, "image_id": proof["image_id"], "build_identity": identity,
            "source_sha256": before, "policy_sha256": policy, "freshness": facts,
            "original_manifest_sha256": manifest_sha256, "resume_claim": acknowledgment,
            "local_proof_sha256": proof_sha, "native_isolation_proven": False,
            "native_corpus_proven": False, "promotion_performed": False, "new_build_created": False}


def original_partial(path: Path, build: Any) -> bool:
    """Only the exact pre-Job nineteen-file artifact is a known partial."""
    try:
        partial_snapshot(path, build)
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def verify_consumed_partial(runs: list[Path], build: Any) -> bool:
    """A completed exact resume can account for this one original partial later."""
    recovery = build.infra("document_refresh_recovery")
    profile = build.infra("document_renderer_profiles")
    matches = []
    for run in runs:
        path = run / "result.json"
        if not path.is_file() or path.is_symlink():
            continue
        result = build.decode(recovery.read(path, build))
        if result.get("original_manifest_sha256") == profile.PARTIAL_MANIFEST_SHA256:
            matches.append((run, result))
    if not matches:
        return False
    build.need(len(matches) == 1, "document_resume_completed_history_conflict")
    run, result = matches[0]
    build.need(result.get("status") == "document_refresh_serving" and result.get("admission_restored") is True
               and result.get("resumed_existing_build") is True and result.get("image") == profile.IMAGE,
               "document_resume_completed_result_changed")
    partial_snapshot(run / "original-candidate", build)
    checked = build.decode(recovery.read(run / "resume.claim.checked.json", build))
    identity = build.decode(recovery.read(run / "resume-build-identity.json", build))
    build.need(profile.matches_build(identity) and checked.get("body", {}).get("build_identity") == identity
               and sha(recovery.read(run / "local-native-proof.json", build)) == identity["local_native_proof_sha256"],
               "document_resume_completed_binding_changed")
    body, ack = checked["body"], checked["acknowledgment"]
    name = "hirewiz-document-refresh-source/" + profile.BUILD["nonce"] + "/resume.json"
    build.need(set(body) == {"version", "kind", "original_manifest_sha256", "build_identity",
                            "maintenance_source_sha256", "automatic_replay"}
        and body["version"] == 1 and body["kind"] == "same_full_source_candidate_resume_v1"
        and body["original_manifest_sha256"] == profile.PARTIAL_MANIFEST_SHA256 and body["automatic_replay"] is False
        and set(ack) == {"name", "bucket", "generation"} and ack["name"] == name
        and ack["bucket"] == build.PROJECT + "_cloudbuild" and type(ack["generation"]) is str
        and re.fullmatch(r"[1-9][0-9]{0,19}", ack["generation"]), "document_resume_completed_claim_changed")
    complete = build.decode(recovery.read(run / "transition/complete.json", build))
    accepted_raw = recovery.read(run / "transition/native-acceptance.json", build)
    accepted = build.decode(accepted_raw)
    build.need(all(result.get(key) == value for key, value in complete.items())
        and complete.get("native_acceptance_sha256") == sha(canonical(accepted))
        and accepted.get("image") == profile.IMAGE and accepted.get("build_id") == profile.BUILD["build_id"]
        and accepted.get("source_sha256") == profile.SOURCES and accepted.get("policy_sha256") == profile.BUILD["policy_sha256"]
        and accepted.get("font_manifest_sha256") == profile.FONT and accepted.get("renderer_version") == profile.RENDERER
        and accepted.get("contract_version") == 2 and accepted.get("required_case_count") == 10,
        "document_resume_completed_transition_changed")
    http = build.native_http()
    try:
        response, raw = http.request("https://storage.googleapis.com/download/storage/v1/b/" + ack["bucket"]
            + "/o/" + quote(name, safe="") + "?" + urlencode({"alt": "media", "generation": ack["generation"]}),
            method="GET", redirections=0)
        build.need(response.status == 200 and raw == canonical(body), "document_resume_completed_claim_unverified")
    finally:
        http.close()
    probe = build.infra("document_refresh_probe")
    clients = probe.Clients()
    try:
        snapshot = clients.preflight()
        worker = snapshot["service"]
        current = worker["template"]["containers"][0]["image"]
        actual = probe.verify_current_successor(current, worker)
        build.need(actual.contract_version == 2 and actual.required_case_count == 10
                   and actual.policy_sha256 == probe.POLICY, "document_resume_current_v2_acceptance_required")
        probe.verify_caller_pins(clients, current)
    finally:
        clients.close()
    return True
