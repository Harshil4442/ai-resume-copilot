"""Public-source exact-image Docker QA inside one bounded native Cloud Build.

No private identity/restore journals, account credentials or candidate resumes
belong in this archive. This proof supplements, never replaces, Cloud Run17.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVENT = "hirewiz_document_patched_build_qa_v1"
MAX_PROOF = 196608
PATH = "infra/gcp/document_cloud_build_qa.py"
DRIVER = "infra/document-processing/cloud-build-qa.sh"
CHECKER = "backend/scripts/document_test_image.py"
FILES = (PATH, DRIVER, CHECKER, "infra/gcp/document_candidate.py", "infra/gcp/document_source_candidate.py",
    "infra/gcp/document_source_candidate_v5.py", "infra/gcp/document_source_patched_engine.py",
    "infra/gcp/document_source_payload.py", "infra/document-processing/policy.json",
    "infra/document-processing/policy.source-fixed-renderer.json", "infra/document-processing/policy.source-synchronous.json")
REPOSITORY = "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector"
PROOF_PATH = ".document-build-qa/proof.json"


def need(value):
    if not value:
        raise ValueError("document_cloud_build_qa_unverified")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def helper_hashes(root=ROOT):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in FILES}


def checker():
    spec = importlib.util.spec_from_file_location("hirewiz_build_qa_checker", ROOT / CHECKER)
    if spec is None or spec.loader is None:
        raise ValueError("document_cloud_build_qa_unverified")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shared_scratch(helper, docker, image, workspace_source):
    expected = Path(workspace_source) / ".document-build-qa/tmp"
    need(Path(tempfile.gettempdir()).resolve() == expected and expected.is_dir() and not expected.is_symlink())
    with tempfile.TemporaryDirectory(prefix="native-scratch-", dir=expected) as folder:
        path = Path(folder)
        path.chmod(0o755)
        value = os.urandom(32)
        challenge = path / "challenge"
        challenge.write_bytes(value)
        challenge.chmod(0o444)
        code, raw = helper.container(docker, image, "linux/amd64", ["-c",
            "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('/tmp/challenge').read_bytes()).hexdigest())"],
            [(challenge, "/tmp/challenge")])
        need(code == 0 and raw == hashlib.sha256(value).hexdigest().encode() + b"\n")
    return {"native_daemon_round_trip_verified": True, "restricted_container_verified": True}


def run(mode, image, tag, nonce, commit, qualification, workspace_source):
    need(mode in {"check", "emit"} and re.fullmatch(r"sha256:[a-f0-9]{64}", image)
         and re.fullmatch(r"[a-f0-9]{32}", nonce) and re.fullmatch(r"[a-f0-9]{40}", commit)
         and re.fullmatch(r"[a-f0-9]{64}", qualification) and tag == REPOSITORY + ":source-revision-" + nonce)
    helper = checker()
    docker = helper.docker_command()
    current = json.loads(helper.successful(docker + ["image", "inspect", tag]))
    need(type(current) is list and len(current) == 1 and current[0].get("Id") == image)
    profile = current[0].get("Config", {}).get("Labels", {})
    need(profile.get("hirewiz.document-source.commit") == commit
         and profile.get("hirewiz.document-source.qualification") == qualification)
    if mode == "check":
        hashes = helper_hashes()
        scratch = shared_scratch(helper, docker, image, workspace_source)
        proof = helper.check("linux/amd64", image=image, source_revision=True, source_patched_engine=True)
        need(helper_hashes() == hashes)
        value = {"event": EVENT, "version": 1, "nonce": nonce, "source_commit": commit,
            "qualification_sha256": qualification, "image_id": image, "helper_sha256": hashes,
            "shared_scratch": scratch, "proof": proof, "proof_sha256": hashlib.sha256(canonical(proof)).hexdigest(),
            "cloud_run_isolation_proven": False, "native_acceptance": False}
        raw = canonical(value) + b"\n"
        need(len(raw) <= MAX_PROOF)
        with (ROOT / PROOF_PATH).open("xb") as stream:
            stream.write(raw)
        return
    path = ROOT / PROOF_PATH
    need(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_PROOF)
    value = json.loads(path.read_bytes())
    need(value.get("event") == EVENT and value.get("image_id") == image and value.get("nonce") == nonce
         and value.get("source_commit") == commit and value.get("qualification_sha256") == qualification
         and value.get("helper_sha256") == helper_hashes()
         and value.get("proof_sha256") == hashlib.sha256(canonical(value["proof"])).hexdigest())
    digests = current[0].get("RepoDigests", [])
    selected = [row for row in digests if type(row) is str and re.fullmatch(re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}", row)]
    need(len(selected) == 1)
    value["immutable_image"] = selected[0]
    raw = canonical(value)
    need(len(raw) <= MAX_PROOF)
    print(raw.decode())


def validate(value, identity, *, root=ROOT):
    from infra.gcp import document_source_candidate as source

    expected = {name: hashlib.sha256(source.git(root, "show", identity["source_commit"] + ":" + name)).hexdigest()
                for name in FILES}
    need(type(value) is dict and set(value) == {"event", "version", "nonce", "source_commit", "qualification_sha256",
        "image_id", "helper_sha256", "shared_scratch", "proof", "proof_sha256", "cloud_run_isolation_proven",
        "native_acceptance", "immutable_image"} and value["event"] == EVENT and type(value["version"]) is int
        and value["version"] == 1 and value["immutable_image"] == identity["immutable_image"]
        and value["nonce"] == identity["nonce"] and value["source_commit"] == identity["source_commit"]
        and value["qualification_sha256"] == identity["qualification_sha256"]
        and value["helper_sha256"] == expected and type(value["image_id"]) is str
        and re.fullmatch(r"sha256:[a-f0-9]{64}", value["image_id"])
        and value["shared_scratch"] == {"native_daemon_round_trip_verified": True, "restricted_container_verified": True}
        and value["cloud_run_isolation_proven"] is False and value["native_acceptance"] is False
        and type(value["proof"]) is dict and value["proof"].get("image_id") == value["image_id"]
        and value["proof_sha256"] == hashlib.sha256(canonical(value["proof"])).hexdigest()
        and len(canonical(value)) <= MAX_PROOF)
    return value["proof"]


def _read_retry_delay():
    time.sleep(2)


def read_native(http, identity, request, *, root=ROOT):
    """Read only genuine same-Build structured Logging entries, never local proof."""
    query = ('resource.type="build" AND resource.labels.build_id="' + identity["build_id"]
        + '" AND logName="projects/ai-resume-parser-482412/logs/cloudbuild"'
        + ' AND jsonPayload.event="' + EVENT + '"')
    payload = {"resourceNames": ["projects/ai-resume-parser-482412"], "filter": query,
        "pageSize": 3, "orderBy": "timestamp asc"}
    for attempt in range(3):
        result = request(http, "POST", "https://logging.googleapis.com/v2/entries:list", payload)
        entries = result.get("entries", [])
        if entries or result.get("nextPageToken") or attempt == 2:
            break
        _read_retry_delay()
    need(not result.get("nextPageToken") and type(entries) is list and len(entries) == 1)
    entry = entries[0]
    need(entry.get("resource", {}).get("type") == "build"
         and entry.get("resource", {}).get("labels", {}).get("build_id") == identity["build_id"]
         and entry.get("resource", {}).get("labels", {}).get("project_id") == "ai-resume-parser-482412"
         and entry.get("logName") == "projects/ai-resume-parser-482412/logs/cloudbuild"
         and type(entry.get("insertId")) is str and bool(entry["insertId"])
         and type(entry.get("timestamp")) is str and bool(entry["timestamp"]))
    proof = validate(entry.get("jsonPayload"), identity, root=root)
    return proof, {"build_id": identity["build_id"], "insert_id": entry["insertId"],
        "timestamp": entry["timestamp"], "entry_sha256": hashlib.sha256(canonical(entry)).hexdigest(),
        "proof_sha256": hashlib.sha256(canonical(proof)).hexdigest()}


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("mode", choices=("check", "emit"))
    for name in ("image-id", "tag", "nonce", "commit", "qualification", "workspace-source"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        run(args.mode, args.image_id, args.tag, args.nonce, args.commit, args.qualification, args.workspace_source)
    except Exception:  # noqa: BLE001 - never expose native command output or document bodies.
        print("document_cloud_build_qa_unverified", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
