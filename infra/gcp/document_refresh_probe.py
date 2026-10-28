"""Native required-case V2 scanner-successor acceptance; never promote a service.

The payload and finite receipt parser bind the required native formats. Only
the immutable-image literal is rendered anew. Every new run owns a fresh Job and
journal; unknown Create/Run/Delete outcomes never authorize a repeat. Current
accepted native Job/Execution resources remain until a newer serving successor
has been independently verified. Importing this module obtains no credentials.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PROJECT, NUMBER, REGION = "ai-resume-parser-482412", "157225724590", "us-central1"
PARENT = f"projects/{PROJECT}/locations/{REGION}"
REPOSITORY = f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/hirewiz-document-inspector"
BASE_IMAGE = REPOSITORY + "@sha256:624597726ce2fefa2b3574fbf0a8ec4ccdc02a826380d76c5edc8ebbd59aa107"
POLICY = 'd3baf46026934f48b8def219b48d348d7b03bc3b4d0cb713a6f222c7299df7f2'
FONT = "abaf429caeeeb5fbcc000c878a315c89e23ecd3dc912bb74df10761c144f7d60"
RENDERER = "LibreOffice 25.2.3.2 520(Build:2)"
PAYLOAD_SHA = 'eb8908a0dfe58cd00c200f6e35af86e6d89c60192e3f15a441d3c0f459a06790'
PARSER_SHA = 'bcd4fc94d6d671188dc74e754d74149ee2e42e95bafab075e84fa5611aa63521'
LEGACY_POLICY = "dbd15ce3120bb6211bb6c1d7b166aaddbd8b80e00a72d564e6274ef10db3aaf1"
LEGACY_BASE_IMAGE = REPOSITORY + "@sha256:624597726ce2fefa2b3574fbf0a8ec4ccdc02a826380d76c5edc8ebbd59aa107"
LEGACY_FONT = "abaf429caeeeb5fbcc000c878a315c89e23ecd3dc912bb74df10761c144f7d60"
LEGACY_RENDERER = "LibreOffice 25.2.3.2 520(Build:2)"
LEGACY_SOURCE_HASHES = {
    "payload.txt": "eb26e5c53a472a8209112ad92769b00087e2e18ee0618f648680239a31882558",
    "receipt.txt": "38ac8c560c693ca84afd48e71fb3ca5df52c138f6a387254863cadbaa6a0ac34",
    "refresh.Dockerfile": "aeab51c4d7fb774573bf13ff471522c1742d1836dfcab812379770c7142fe6e7",
    "policy.json": LEGACY_POLICY,
}
ACCOUNT = "hirewiz-document-inspector@" + PROJECT + ".iam.gserviceaccount.com"
ACCOUNT_UID = "110725727500711902669"
SERVICE_UID = "7014a5ba-faee-4fbf-8272-a437b1de49ad"
WORKER_URL = "https://hirewiz-document-inspector-mqff62d75a-uc.a.run.app/inspect"
CALLERS = ["serviceAccount:hirewiz-resume-coordinator@" + PROJECT + ".iam.gserviceaccount.com",
           "serviceAccount:hirewiz-runtime-20261014@" + PROJECT + ".iam.gserviceaccount.com"]
MAX_BYTES = 1024 * 1024
UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\Z")
IMAGE = re.compile(re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}\Z")


class NativeProofDenied(RuntimeError):
    """Fixed reason only; vendor bodies/SQL/tokens are never emitted."""


def need(value: Any, code: str) -> None:
    if not value:
        raise NativeProofDenied(code)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def load_source(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path, loader=SourceFileLoader(name, str(path)))
    need(spec is not None and spec.loader is not None, "document_native_source_unavailable")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Execute the bytes just read; never adopt a timestamp/size .pyc cache.
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def build_module() -> Any:
    return load_source("hirewiz_document_refresh_build", ROOT / "backend/scripts/document_refresh.py")


def renderer_identity(image: str, *, historical_v1: bool = False,
                      build_identity: dict[str, Any] | None = None) -> tuple[str, str]:
    """Fixed expected renderer/font pins for a genuinely verified immutable image."""
    need(type(image) is str and IMAGE.fullmatch(image) and type(historical_v1) is bool,
         "document_native_renderer_profile_invalid")
    profile = load_source("hirewiz_document_renderer_profiles", HERE / "document_renderer_profiles.py")
    need(build_identity is None or build_identity.get("immutable_image") == image,
         "document_native_renderer_build_profile_changed")
    if image == profile.IMAGE or profile.uses_profile(build_identity):
        need(not historical_v1, "document_native_historical_renderer_profile_refused")
        return profile.RENDERER, profile.FONT
    return (LEGACY_RENDERER, LEGACY_FONT) if historical_v1 else (RENDERER, FONT)


def verify_renderer_build(build: dict[str, Any]) -> None:
    profile = load_source("hirewiz_document_renderer_profiles", HERE / "document_renderer_profiles.py")
    need((build.get("immutable_image") != profile.IMAGE or profile.matches_build(build))
         and (build.get("fixed_renderer_base") != profile.IMAGE or profile.uses_profile(build)),
         "document_native_renderer_build_profile_changed")
    need(build.get("policy_sha256", POLICY) == POLICY,
         "document_native_candidate_renderer_review_required")


def legacy_source(name: str) -> bytes:
    """Exact frozen V1 evidence for read-only observation, never V2 admission."""
    need(name in LEGACY_SOURCE_HASHES, "document_native_legacy_source_invalid")
    path = HERE / "document_legacy_v1" / name
    need(not path.is_symlink(), "document_native_legacy_source_invalid")
    raw = path.read_bytes()
    need(0 < len(raw) <= MAX_BYTES and hashlib.sha256(raw).hexdigest() == LEGACY_SOURCE_HASHES[name],
         "document_native_legacy_source_changed")
    return raw


def policy_for_serving(metadata: Any, image: str) -> str:
    """Select only an explicitly pinned policy on the exact observed image."""
    need(type(metadata) is dict and type(metadata.get("template")) is dict, "document_native_serving_policy_unverified")
    containers = metadata["template"].get("containers", [])
    need(type(containers) is list and len(containers) == 1
         and type(containers[0]) is dict and containers[0].get("image") == image, "document_native_serving_policy_unverified")
    env = containers[0].get("env", [])
    need(type(env) is list, "document_native_serving_policy_unverified")
    matches = [row for row in env if type(row) is dict and row.get("name") == "DOCUMENT_WORKER_POLICY_SHA256"]
    candidate = load_source("hirewiz_document_candidate_profile", HERE / "document_candidate.py")
    need(len(matches) == 1 and set(matches[0]) == {"name", "value"} and type(matches[0]["value"]) is str,
         "document_native_serving_policy_unverified")
    policy = matches[0]["value"]
    need((policy == candidate.POLICY_SHA256 and type(image) is str and IMAGE.fullmatch(image))
         or (image != candidate.IMAGE and policy in {POLICY, LEGACY_POLICY}),
         "document_native_serving_policy_unverified")
    return matches[0]["value"]


def historical_v1_for_serving(metadata: Any, image: str) -> bool:
    return policy_for_serving(metadata, image) == LEGACY_POLICY and POLICY != LEGACY_POLICY


def runtime_hashes(*, historical_v1: bool = False) -> dict[str, str]:
    need(type(historical_v1) is bool, "document_native_contract_version_invalid")
    # Serving receipts retain their exact deployed source inventory even when
    # the working tree contains a separately reviewed source-only candidate.
    if historical_v1:
        raw = legacy_source("policy.json")
    else:
        path = HERE / "document_accepted_v2/policy.json"
        need(not path.is_symlink(), "document_native_policy_changed")
        raw = path.read_bytes()
    need(0 < len(raw) <= MAX_BYTES
         and hashlib.sha256(raw).hexdigest() == (LEGACY_POLICY if historical_v1 else POLICY),
         "document_native_policy_changed")
    policy = json.loads(raw)
    source = policy["source_sha256"]
    paths = {"infra/document-processing/processor.py": "/opt/document/processor.py",
             "infra/document-processing/service.py": "/opt/document/service.py",
             "infra/document-processing/docx_renderer.py": "/opt/document/docx_renderer.py",
             "infra/document-processing/writer_owner.py": "/opt/document/writer_owner.py"}
    result = {}
    for path, digest in source.items():
        if path == "infra/document-processing/Dockerfile":
            continue
        destination = paths.get(path, "/opt/document/" + path.removeprefix("backend/"))
        need(type(digest) is str and re.fullmatch(r"[a-f0-9]{64}", digest),
             "document_native_runtime_source_changed")
        result[destination] = digest
    need(len(result) == (11 if historical_v1 else 12), "document_native_runtime_inventory_changed")
    return result


def candidate_runtime_hashes() -> dict[str, str]:
    """Offline candidate inventory only; never select it for a native receipt."""
    build = build_module()
    candidate = build.infra("document_candidate")
    _, hashes, _ = build.full_source_snapshot()
    return {destination: hashes[source] for source, destination in candidate.RUNTIME.items()}


def rendered_payload(image: str, *, historical_v1: bool = False) -> str:
    need(type(historical_v1) is bool, "document_native_contract_version_invalid")
    base = LEGACY_BASE_IMAGE if historical_v1 else BASE_IMAGE
    need(type(image) is str and IMAGE.fullmatch(image) and image != base,
         "document_native_successor_image_required")
    raw = legacy_source("payload.txt") if historical_v1 else (HERE / "document_refresh_payload.txt").read_bytes()
    need(hashlib.sha256(raw).hexdigest() == (LEGACY_SOURCE_HASHES["payload.txt"] if historical_v1 else PAYLOAD_SHA), "document_native_payload_changed")
    old = "IMMUTABLE_IMAGE = " + repr(base)
    text = raw.decode()
    need(text.count(old) == 1, "document_native_payload_image_literal_changed")
    result = text.replace(old, "IMMUTABLE_IMAGE = " + repr(image))
    original, updated = ast.parse(raw), ast.parse(result)
    updates = [node for node in updated.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "IMMUTABLE_IMAGE" for target in node.targets)]
    need(len(updates) == 1, "document_native_payload_shape_changed")
    updates[0].value = ast.Constant(value=base)
    need(ast.dump(original, include_attributes=False) == ast.dump(updated, include_attributes=False),
         "document_native_payload_body_changed")
    return result


def desired(image: str, nonce: str, *, historical_v1: bool = False) -> dict[str, Any]:
    need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce), "document_native_nonce_invalid")
    job_id = "hirewiz-doc-six-" + nonce[:12]
    task = {"serviceAccount": ACCOUNT, "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2",
            "maxRetries": 0, "timeout": "600s", "containers": [{
                "name": "document-native-probe", "image": image, "sandboxLauncher": True,
                "command": ["/usr/local/bin/python3"], "args": ["-I", "-c", rendered_payload(image, historical_v1=historical_v1)],
                "resources": {"limits": {"cpu": "2", "memory": "4Gi"}},
                "env": [{"name": "DOCUMENT_WORKER_IMAGE_DIGEST", "value": image},
                        {"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": LEGACY_POLICY if historical_v1 else POLICY}]}]}
    return {"name": PARENT + "/jobs/" + job_id, "launchStage": "BETA",
            "labels": {"hirewiz-probe": "document-six-case", "hirewiz-owned": nonce[:12]},
            "template": {"taskCount": 1, "parallelism": 1, "template": task}}


def task_matches(actual: Any, expected: dict[str, Any]) -> bool:
    if type(actual) is not dict:
        return False
    actual = json.loads(canonical(actual))
    actual.setdefault("maxRetries", 0)
    containers = actual.get("containers")
    if type(containers) is not list or len(containers) != 1:
        return False
    resources = containers[0].get("resources", {})
    for name in ("cpuIdle", "startupCpuBoost"):
        if name in resources and resources[name] is not False:
            return False
        resources.pop(name, None)
    containers[0]["resources"] = resources
    return canonical(actual) == canonical(expected)


def integer(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected or type(value) is str and value == str(expected)


def validate_job(row: Any, image: str, nonce: str, *, uid: str | None = None, historical_v1: bool = False) -> None:
    expected = desired(image, nonce, historical_v1=historical_v1)
    need(type(row) is dict and row.get("name") == expected["name"]
         and type(row.get("uid")) is str and UUID.fullmatch(row["uid"])
         and (uid is None or row["uid"] == uid) and type(row.get("etag")) is str and 0 < len(row["etag"]) <= 512
         and row.get("launchStage") == expected["launchStage"] and row.get("labels") == expected["labels"]
         and integer(row.get("generation"), 1) and integer(row.get("observedGeneration"), 1)
         and row.get("reconciling", False) is False
         and row.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
         and integer(row.get("template", {}).get("taskCount"), 1)
         and integer(row["template"].get("parallelism"), 1)
         and task_matches(row["template"].get("template"), expected["template"]["template"]),
         "document_native_job_changed")


def validate_execution(row: Any, job: dict[str, Any], image: str, nonce: str, *, uid: str | None = None, historical_v1: bool = False) -> None:
    job_id = job["name"].rsplit("/", 1)[-1]
    need(type(row) is dict and type(row.get("name")) is str
         and re.fullmatch(re.escape(job["name"] + "/executions/" + job_id + "-") + r"[a-z0-9]{1,20}", row["name"])
         and type(row.get("uid")) is str and UUID.fullmatch(row["uid"])
         and (uid is None or row["uid"] == uid) and row.get("job") in (job["name"], job_id)
         and row.get("reconciling", False) is False and bool(row.get("completionTime"))
         and integer(row.get("taskCount"), 1)
         and integer(row.get("succeededCount"), 1)
         and all(integer(row.get(key, 0), 0) for key in ("failedCount", "cancelledCount", "runningCount", "retriedCount"))
         and task_matches(row.get("template"), desired(image, nonce, historical_v1=historical_v1)["template"]["template"]),
         "document_native_execution_not_successful")
    latest = job.get("latestCreatedExecution", {})
    need(integer(job.get("executionCount"), 1)
         and latest.get("name") in (row["name"], row["name"].rsplit("/", 1)[-1]),
         "document_native_execution_not_singleton")


def validate_payload(payload: Any, image: str, execution: str, now: datetime, *, require_fresh: bool = True,
                     historical_v1: bool = False, build_identity: dict[str, Any] | None = None) -> dict[str, Any]:
    need(type(historical_v1) is bool, "document_native_contract_version_invalid")
    path = HERE / "document_legacy_v1/receipt.txt" if historical_v1 else HERE / "document_refresh_receipt.txt"
    raw = legacy_source("receipt.txt") if historical_v1 else path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == (LEGACY_SOURCE_HASHES["receipt.txt"] if historical_v1 else PARSER_SHA), "document_native_parser_changed")
    parser = load_source("hirewiz_document_refresh_receipt_v1" if historical_v1 else "hirewiz_document_refresh_receipt", path)
    policy = LEGACY_POLICY if historical_v1 else POLICY
    try:
        valid = parser.validate(payload, image, policy, runtime_hashes(historical_v1=historical_v1))
    except (ValueError, TypeError, KeyError, OverflowError):
        raise NativeProofDenied("document_native_required_receipt_invalid") from None
    need(valid is True and payload.get("execution_name") == execution.rsplit("/", 1)[-1],
         "document_native_required_cases_not_complete")
    identity = payload["identity_preflight"]
    renderer, fonts = renderer_identity(image, historical_v1=historical_v1, build_identity=build_identity)
    need(identity["font_manifest_sha256"] == fonts
         and identity["renderer_version"] == renderer
         and identity["font_manifest_process_writable"] is False,
         "document_native_renderer_or_fonts_changed")
    scans = [row["scan"] for row in payload["cases"] if "scan" in row]
    need(len(scans) == (3 if historical_v1 else 5) and all(row == scans[0] for row in scans), "document_native_scan_metadata_disagrees")
    try:
        number, timestamp = scans[0]["definitions"].split("/", 1)
        issued = datetime.fromisoformat(timestamp)
        need(re.fullmatch(r"[0-9]{1,10}", number) and issued.tzinfo is not None,
             "document_native_definition_invalid")
        age = now - issued
    except (ValueError, TypeError, KeyError):
        raise NativeProofDenied("document_native_definition_invalid") from None
    need(timedelta(0) <= age and (not require_fresh or age < timedelta(days=3)), "document_native_definitions_expired")
    return {"definition_issued_at": issued.isoformat(), "definition_expires_at": (issued + timedelta(days=3)).isoformat(),
            "payload_sha256": hashlib.sha256(canonical(payload)).hexdigest()}


class Journal:
    def __init__(self, path: Path, image: str, build: dict[str, Any], *, create: bool = False, historical_v1: bool = False):
        need(not create or historical_v1 is False, "document_native_historical_create_refused")
        need(path.is_absolute() and path.resolve() == path, "document_native_private_journal_required")
        if create:
            need(not path.exists(), "document_native_existing_journal_reconcile_only")
            path.mkdir(mode=0o700)
        info = path.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
             "document_native_private_journal_required")
        self.path = path
        self.binding = {"image": image, "build_identity": build,
                        "probe_sha256": hashlib.sha256(rendered_payload(image, historical_v1=historical_v1).encode()).hexdigest()}
        if create:
            self.write("binding.json", self.binding)
        else:
            need(self.read("binding.json") == self.binding, "document_native_journal_binding_changed")

    def read(self, name: str) -> Any:
        path = self.path / name
        info = path.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= MAX_BYTES,
             "document_native_journal_file_invalid")
        return json.loads(path.read_bytes())

    def write(self, name: str, value: Any) -> None:
        raw = canonical(value) + b"\n"
        need(len(raw) <= MAX_BYTES, "document_native_journal_bound")
        fd = os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def sdk_library() -> Path:
    """Discover the officially installed SDK on Linux or macOS; no auth export."""
    executable = shutil.which("gcloud")
    need(executable is not None, "document_native_sdk_required")
    assert executable is not None
    result = subprocess.run([executable, "info", "--format=value(installation.sdk_root)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=5, check=False)
    need(result.returncode == 0 and len(result.stdout) <= 4096,
         "document_native_sdk_required")
    root = Path(result.stdout.decode().strip())
    need(root.is_absolute() and root.is_dir() and (root / "lib/googlecloudsdk").is_dir()
         and (root / "lib/third_party").is_dir(), "document_native_sdk_required")
    return root / "lib"


def validate_serving(clients: Any, service: Any, image: str, name: str, uid: str | None = None) -> dict[str, Any]:
    resource = PARENT + "/services/" + name
    need(type(service) is dict and service.get("name") == resource
         and type(service.get("uid")) is str and UUID.fullmatch(service["uid"])
         and (uid is None or service["uid"] == uid)
         and type(service.get("generation")) in (int, str)
         and re.fullmatch(r"[1-9][0-9]{0,18}", str(service["generation"]))
         and str(service["generation"]) == str(service.get("observedGeneration"))
         and service.get("reconciling", False) is False
         and service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED",
         "document_native_serving_not_ready")
    ready = service.get("latestReadyRevision")
    need(type(ready) is str and ready == service.get("latestCreatedRevision")
         and re.fullmatch(re.escape(resource + "/revisions/" + name + "-") + r"[a-z0-9-]{1,36}", ready),
         "document_native_serving_revision_changed")
    traffic = service.get("trafficStatuses")
    need(type(traffic) is list and len(traffic) == 1 and type(traffic[0]) is dict
         and type(traffic[0].get("percent")) is int and traffic[0]["percent"] == 100
         and traffic[0].get("revision") in (ready, ready.rsplit("/", 1)[-1])
         and not traffic[0].get("tag"), "document_native_serving_traffic_changed")
    revision = clients.revision(ready)
    rows = revision.get("conditions", [])
    need(revision.get("name") == ready and revision.get("service") in (resource, name)
         and type(revision.get("uid")) is str and UUID.fullmatch(revision["uid"])
         and type(rows) is list and len([row for row in rows if row.get("type") == "Ready"
              and row.get("state") == "CONDITION_SUCCEEDED"]) == 1,
         "document_native_serving_revision_unverified")
    for row in (service.get("template", {}), revision):
        containers = row.get("containers")
        need(type(containers) is list and len(containers) == 1
             and containers[0].get("image") == image, "document_native_serving_image_changed")
    return revision


def validate_worker_snapshot(clients: Any, snapshot: dict[str, Any], *, historical_v1: bool = False) -> str:
    """Original serving controls, for baseline and successor, without age claims."""
    service = snapshot["service"]
    containers = service.get("template", {}).get("containers", [])
    need(type(containers) is list and len(containers) == 1
         and type(containers[0].get("image")) is str and IMAGE.fullmatch(containers[0]["image"]),
         "document_native_worker_image_unverified")
    image = containers[0]["image"]
    snapshot["revision"] = validate_serving(clients, service, image, "hirewiz-document-inspector", SERVICE_UID)
    configuration = load_source("hirewiz_document_refresh_worker_contract", HERE / "document_worker.py")
    try:
        candidate = load_source("hirewiz_document_candidate_profile", HERE / "document_candidate.py")
        candidate_v3 = policy_for_serving(service, image) == candidate.POLICY_SHA256
        configuration.check_snapshot(snapshot, image, service.get("template", {}).get("labels", {}).get("hirewiz-source"),
                                     api_phase=True, coordinator_phase=True, historical_v1=historical_v1,
                                     candidate_v3=candidate_v3)
    except configuration.ConfigurationDenied:
        raise NativeProofDenied("document_native_worker_configuration_changed") from None
    return image


def verify_caller_pins(clients: Any, image: str, *, policy_sha256: str | None = None) -> None:
    """Old acceptance evidence is retained until both actual serving callers move."""
    candidate = load_source("hirewiz_document_candidate_profile", HERE / "document_candidate.py")
    if image == candidate.IMAGE or policy_sha256 == candidate.POLICY_SHA256:
        adapter = load_source("hirewiz_document_candidate_release", HERE / "document_candidate_release.py")
        return adapter.verify_caller_pins(clients, image, policy_sha256=policy_sha256)
    policy = POLICY if policy_sha256 is None else policy_sha256
    need(type(policy) is str and policy in {POLICY, LEGACY_POLICY}, "document_native_caller_policy_unverified")
    identity = None
    profile = load_source("hirewiz_document_renderer_profiles", HERE / "document_renderer_profiles.py")
    builds = build_module()
    if policy == POLICY and image != profile.IMAGE and getattr(builds, "RENDERER_BASE", BASE_IMAGE) == profile.IMAGE:
        # After the reviewed daily recipe repin, only a fresh actual Build can
        # select that parent's font profile for a different immutable successor.
        worker = clients.service(PARENT + "/services/hirewiz-document-inspector")
        labels = worker.get("labels", {})
        need(worker.get("template", {}).get("containers", [{}])[0].get("image") == image,
             "document_native_caller_profile_worker_changed")
        verifier = builds.verify_current_build_release if getattr(clients, "read_only_release", False) else builds.verify_current_build
        identity = verifier(labels.get("hirewiz-refresh-build"), labels.get("hirewiz-refresh-nonce"), image)
    renderer, fonts = renderer_identity(image, historical_v1=policy == LEGACY_POLICY, build_identity=identity)
    pins = {"DOCUMENT_WORKER_URL": WORKER_URL, "DOCUMENT_WORKER_IMAGE_DIGEST": image, "DOCUMENT_WORKER_POLICY_SHA256": policy,
            "DOCUMENT_WORKER_DOCX_RENDERER_VERSION": renderer,
            "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256": fonts}
    for name in ("ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-resume-coordinator"):
        service = clients.service(PARENT + "/services/" + name)
        containers = service.get("template", {}).get("containers", [])
        need(type(containers) is list and len(containers) == 1,
             "document_native_caller_pin_unverified")
        revision = validate_serving(clients, service, containers[0].get("image"), name)
        for row in (containers[0], revision["containers"][0]):
            env = row.get("env", [])
            need(type(env) is list, "document_native_caller_pin_unverified")
            for key, value in pins.items():
                matches = [item for item in env if item.get("name") == key]
                need(len(matches) == 1 and matches[0] == {"name": key, "value": value},
                     "document_native_caller_pin_unverified")


class NoRedirectSdkTransport:
    """Apitools supplies a default budget of five; never forward that budget."""
    def __init__(self, http: Any):
        self.http, self.connections = http, http.connections

    def request(self, uri: str, method: str = "GET", body: Any = None, headers: Any = None,
                redirections: int = 0, **kwargs: Any) -> Any:
        need(type(redirections) is int and redirections in (0, 5),
             "document_native_sdk_redirect_budget_invalid")
        return self.http.request(uri, method=method, body=body, headers=headers,
                                 redirections=0, **kwargs)


class Clients:
    """Official SDK with one internally selected, fixed native identity route."""
    def __init__(self, *, read_only_release: bool = False):
        need(type(read_only_release) is bool, "document_native_client_mode_invalid")
        self.read_only_release = read_only_release
        sdk = sdk_library()
        sys.path[:0] = [str(sdk), str(sdk / "third_party")]
        from apitools.base.py import encoding, exceptions
        from googlecloudsdk.generated_clients.apis.cloudresourcemanager.v1 import (
            cloudresourcemanager_v1_client as cc,
        )
        from googlecloudsdk.generated_clients.apis.cloudresourcemanager.v1 import (
            cloudresourcemanager_v1_messages as cm,
        )
        from googlecloudsdk.generated_clients.apis.iam.v1 import iam_v1_client as ic
        from googlecloudsdk.generated_clients.apis.iam.v1 import iam_v1_messages as im
        from googlecloudsdk.generated_clients.apis.logging.v2 import (
            logging_v2_client as lc,
        )
        from googlecloudsdk.generated_clients.apis.logging.v2 import (
            logging_v2_messages as lm,
        )
        from googlecloudsdk.generated_clients.apis.run.v2 import run_v2_client as rc
        from googlecloudsdk.generated_clients.apis.run.v2 import run_v2_messages as rm
        authority = load_source("hirewiz_document_refresh_service_auth", HERE / "document_refresh_auth.py")
        http = (authority.ReleaseReadOnlyHttp() if read_only_release else
                authority.ServiceHttp(os.environ.get("DOCUMENT_REFRESH_CONTROLLER_UID", "")))
        self.http = http
        http = NoRedirectSdkTransport(http)
        self.run = rc.RunV2(url="https://run.googleapis.com/", get_credentials=False, http=http, log_request=False, log_response=False)
        self.iam = ic.IamV1(url="https://iam.googleapis.com/", get_credentials=False, http=http, log_request=False, log_response=False)
        self.crm = cc.CloudresourcemanagerV1(url="https://cloudresourcemanager.googleapis.com/", get_credentials=False, http=http, log_request=False, log_response=False)
        self.logging = lc.LoggingV2(url="https://logging.googleapis.com/", get_credentials=False, http=http, log_request=False, log_response=False)
        for client in (self.run, self.iam, self.crm, self.logging):
            client.num_retries = 0
        self.encoding, self.errors, self.rm, self.im, self.cm, self.lm = encoding, exceptions, rm, im, cm, lm

    def close(self) -> None:
        self.http.close()

    def service(self, name: str) -> dict[str, Any]:
        return self.data(self.run.projects_locations_services.Get(self.rm.RunProjectsLocationsServicesGetRequest(name=name)))

    def revision(self, name: str) -> dict[str, Any]:
        return self.data(self.run.projects_locations_services_revisions.Get(self.rm.RunProjectsLocationsServicesRevisionsGetRequest(name=name)))

    def data(self, value: Any) -> dict[str, Any]:
        result = self.encoding.MessageToDict(value)
        need(type(result) is dict and len(canonical(result)) <= MAX_BYTES, "document_native_response_bound")
        return result

    def job(self, name: str) -> dict[str, Any]:
        return self.data(self.run.projects_locations_jobs.Get(self.rm.RunProjectsLocationsJobsGetRequest(name=name)))

    def operation(self, name: str) -> dict[str, Any]:
        need(type(name) is str and re.fullmatch(re.escape(PARENT + "/operations/") + r"[A-Za-z0-9._-]{1,200}", name),
             "document_native_operation_name_invalid")
        return self.data(self.run.projects_locations_operations.Get(self.rm.RunProjectsLocationsOperationsGetRequest(name=name)))

    def execution(self, name: str) -> dict[str, Any]:
        return self.data(self.run.projects_locations_jobs_executions.Get(self.rm.RunProjectsLocationsJobsExecutionsGetRequest(name=name)))

    def preflight(self) -> dict[str, Any]:
        project = self.data(self.crm.projects.Get(self.cm.CloudresourcemanagerProjectsGetRequest(projectId=PROJECT)))
        service = self.data(self.run.projects_locations_services.Get(self.rm.RunProjectsLocationsServicesGetRequest(name=PARENT + "/services/hirewiz-document-inspector")))
        policy = self.data(self.run.projects_locations_services.GetIamPolicy(self.rm.RunProjectsLocationsServicesGetIamPolicyRequest(
            resource=PARENT + "/services/hirewiz-document-inspector", options_requestedPolicyVersion=3)))
        account = self.data(self.iam.projects_serviceAccounts.Get(self.im.IamProjectsServiceAccountsGetRequest(name="projects/" + PROJECT + "/serviceAccounts/" + ACCOUNT)))
        keys = self.data(self.iam.projects_serviceAccounts_keys.List(self.im.IamProjectsServiceAccountsKeysListRequest(
            name="projects/" + PROJECT + "/serviceAccounts/" + ACCOUNT,
            keyTypes=[self.im.IamProjectsServiceAccountsKeysListRequest.KeyTypesValueValuesEnum.USER_MANAGED])))
        own = self.data(self.iam.projects_serviceAccounts.GetIamPolicy(self.im.IamProjectsServiceAccountsGetIamPolicyRequest(
            resource="projects/" + PROJECT + "/serviceAccounts/" + ACCOUNT, options_requestedPolicyVersion=3)))
        inherited = self.data(self.crm.projects.GetIamPolicy(self.cm.CloudresourcemanagerProjectsGetIamPolicyRequest(
            resource=PROJECT, getIamPolicyRequest=self.cm.GetIamPolicyRequest(options=self.cm.GetPolicyOptions(requestedPolicyVersion=3)))))
        configuration = load_source("hirewiz_document_refresh_worker_contract", HERE / "document_worker.py")
        _, has_maintenance = configuration.service_bindings_without_maintenance(policy, "hirewiz-document-inspector")
        maintenance = self.maintenance_snapshot() if has_maintenance else {}
        if has_maintenance:
            configuration.validate_maintenance_identity(maintenance, expected_uid=getattr(self.http, "uid", None))
        validate_runtime(project, service, policy, account, keys, own, inherited, maintenance=maintenance)
        coordinator = "hirewiz-resume-coordinator@" + PROJECT + ".iam.gserviceaccount.com"
        upload = self.data(self.iam.projects_serviceAccounts.Get(self.im.IamProjectsServiceAccountsGetRequest(
            name="projects/" + PROJECT + "/serviceAccounts/" + coordinator)))
        upload_keys = self.data(self.iam.projects_serviceAccounts_keys.List(self.im.IamProjectsServiceAccountsKeysListRequest(
            name="projects/" + PROJECT + "/serviceAccounts/" + coordinator,
            keyTypes=[self.im.IamProjectsServiceAccountsKeysListRequest.KeyTypesValueValuesEnum.USER_MANAGED])))
        snapshot = {"service": service, "service_iam": policy, "project_iam": inherited,
                    "service_account": account, "service_account_keys": keys,
                    "upload_coordinator_service_account": upload,
                    "upload_coordinator_service_account_keys": upload_keys}
        snapshot.update(maintenance)
        validate_worker_snapshot(self, snapshot, historical_v1=historical_v1_for_serving(service, service["template"]["containers"][0]["image"]))
        return snapshot

    def maintenance_snapshot(self) -> dict[str, Any]:
        """Fresh official IAM reads; never infer a UID or role scope from a binding."""
        account = "hirewiz-document-refresh@" + PROJECT + ".iam.gserviceaccount.com"
        return {
            "maintenance_service_account": self.data(self.iam.projects_serviceAccounts.Get(
                self.im.IamProjectsServiceAccountsGetRequest(name="projects/" + PROJECT + "/serviceAccounts/" + account))),
            "maintenance_service_account_keys": self.data(self.iam.projects_serviceAccounts_keys.List(
                self.im.IamProjectsServiceAccountsKeysListRequest(name="projects/" + PROJECT + "/serviceAccounts/" + account,
                    keyTypes=[self.im.IamProjectsServiceAccountsKeysListRequest.KeyTypesValueValuesEnum.USER_MANAGED]))),
            "maintenance_role": self.data(self.iam.projects_roles.Get(self.im.IamProjectsRolesGetRequest(
                name="projects/" + PROJECT + "/roles/hirewizDocumentServiceMaintenance"))),
        }

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        need(self.read_only_release is False, "document_native_release_mutation_refused")
        sent = {key: value for key, value in body.items() if key != "name"}
        return self.data(self.run.projects_locations_jobs.Create(self.rm.RunProjectsLocationsJobsCreateRequest(
            parent=PARENT, jobId=body["name"].rsplit("/", 1)[-1], googleCloudRunV2Job=self.encoding.DictToMessage(sent, self.rm.GoogleCloudRunV2Job))))

    def start(self, job: dict[str, Any]) -> dict[str, Any]:
        need(self.read_only_release is False, "document_native_release_mutation_refused")
        return self.data(self.run.projects_locations_jobs.Run(self.rm.RunProjectsLocationsJobsRunRequest(
            name=job["name"], googleCloudRunV2RunJobRequest=self.rm.GoogleCloudRunV2RunJobRequest(etag=job["etag"]))))

    def delete(self, job: dict[str, Any]) -> dict[str, Any]:
        need(self.read_only_release is False, "document_native_release_mutation_refused")
        return self.data(self.run.projects_locations_jobs.Delete(self.rm.RunProjectsLocationsJobsDeleteRequest(name=job["name"], etag=job["etag"])))

    def entries(self, query: str, token: str | None) -> dict[str, Any]:
        return self.data(self.logging.entries.List(self.lm.ListLogEntriesRequest(resourceNames=["projects/" + PROJECT],
            filter=query, pageSize=20, orderBy="timestamp asc", pageToken=token)))


def validate_runtime(project: Any, service: Any, policy: Any, account: Any, keys: Any,
                     own: Any, inherited: Any, *, maintenance: dict[str, Any] | None = None) -> None:
    need(project.get("projectId") == PROJECT and str(project.get("projectNumber")) == NUMBER
         and service.get("uid") == SERVICE_UID and service.get("template", {}).get("serviceAccount") == ACCOUNT
         and account.get("projectId") == PROJECT and account.get("email") == ACCOUNT
         and account.get("uniqueId") == ACCOUNT_UID and account.get("disabled", False) is False
         and keys.get("keys", []) == [], "document_native_runtime_identity_changed")
    wanted = [{"role": "roles/run.invoker", "members": CALLERS}]
    configuration = load_source("hirewiz_document_refresh_worker_contract", HERE / "document_worker.py")
    try:
        bindings, has_maintenance = configuration.service_bindings_without_maintenance(policy, "hirewiz-document-inspector")
        if has_maintenance:
            configuration.validate_maintenance_identity(maintenance or {})
    except configuration.ConfigurationDenied:
        raise NativeProofDenied("document_native_maintenance_authority_unverified") from None
    need(type(bindings) is list and len(bindings) == 1 and set(bindings[0]) == {"role", "members"}
         and type(bindings[0]["members"]) is list and len(bindings[0]["members"]) == 2
         and [{**bindings[0], "members": sorted(bindings[0]["members"])}] == wanted
         and type(policy.get("etag")) is str and bool(policy["etag"]),
         "document_native_worker_invokers_changed")
    forbidden = {"allUsers", "allAuthenticatedUsers", "serviceAccount:" + ACCOUNT,
                 "serviceAccount:hirewiz-resume-coordinator@" + PROJECT + ".iam.gserviceaccount.com"}
    for row in (own, inherited):
        need(type(row) is dict and row.get("version", 1) in (1, 3)
             and type(row.get("bindings", [])) is list and len(row.get("bindings", [])) <= 1000,
             "document_native_runtime_policy_invalid")
        for binding in row.get("bindings", []):
            members = binding.get("members")
            need(type(members) is list and all(type(member) is str for member in members)
                 and not set(members) & forbidden, "document_native_runtime_broad_authority_refused")


def operation_result(clients: Clients, acknowledged: dict[str, Any], kind: str) -> dict[str, Any] | None:
    name = acknowledged.get("name")
    need(type(name) is str, "document_native_operation_name_invalid")
    assert isinstance(name, str)
    row = clients.operation(name)
    need(row.get("name") == name, "document_native_operation_changed")
    if row.get("done") is not True:
        return None
    result = row.get("response", {})
    need(not row.get("error") and type(result) is dict
         and result.get("@type") == "type.googleapis.com/google.cloud.run.v2." + kind,
         "document_native_operation_failed_reconcile_only")
    return result


def wait_operation(clients: Clients, acknowledged: dict[str, Any], kind: str, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        result = operation_result(clients, acknowledged, kind)
        if result is not None:
            return result
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise NativeProofDenied("document_native_deadline_reconcile_only")


def owned_job(clients: Clients, journal: Journal, image: str, nonce: str, *, historical_v1: bool = False) -> dict[str, Any]:
    need(journal.read("create.started.json") == {"body_sha256": hashlib.sha256(canonical(desired(image, nonce, historical_v1=historical_v1))).hexdigest(),
         "automatic_replay": False}, "document_native_create_binding_changed")
    created = operation_result(clients, journal.read("create.acknowledged.json"), "Job")
    need(created is not None, "document_native_create_not_terminal")
    assert created is not None
    need(created.get("name") == desired(image, nonce, historical_v1=historical_v1)["name"] and type(created.get("uid")) is str
         and UUID.fullmatch(created["uid"]), "document_native_create_uid_unverified")
    job = clients.job(created["name"])
    validate_job(job, image, nonce, uid=created.get("uid"), historical_v1=historical_v1)
    return job


def owned_execution(clients: Clients, journal: Journal, job: dict[str, Any], image: str, nonce: str, *, historical_v1: bool = False) -> dict[str, Any]:
    marker = journal.read("run.started.json")
    need(type(marker) is dict and set(marker) == {"job_name", "job_uid", "etag", "automatic_replay"}
         and marker["job_name"] == job["name"] and marker["job_uid"] == job["uid"]
         and type(marker["etag"]) is str and bool(marker["etag"]) and marker["automatic_replay"] is False,
         "document_native_run_binding_changed")
    started = operation_result(clients, journal.read("run.acknowledged.json"), "Execution")
    need(started is not None, "document_native_run_not_terminal")
    assert started is not None
    need(type(started.get("name")) is str and type(started.get("uid")) is str
         and UUID.fullmatch(started["uid"]), "document_native_run_uid_unverified")
    execution = clients.execution(started["name"])
    validate_execution(execution, job, image, nonce, uid=started.get("uid"), historical_v1=historical_v1)
    return execution


def timestamp(value: Any) -> datetime:
    need(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]{8,18}Z", value),
         "document_native_event_time_invalid")
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def execution_reference(job: dict[str, Any], value: Any) -> str:
    """Canonicalize only the exact current Job's native execution reference."""
    name = job["name"]
    short = name.rsplit("/", 1)[-1]
    prefix = name + "/executions/"
    need(type(value) is str, "document_native_current_execution_missing")
    if re.fullmatch(re.escape(short + "-") + r"[a-z0-9]{1,20}", value):
        return prefix + value
    need(re.fullmatch(re.escape(prefix + short + "-") + r"[a-z0-9]{1,20}", value),
         "document_native_current_execution_reference_invalid")
    return value


def native_payload(clients: Clients, job: dict[str, Any], execution: dict[str, Any], *, historical_v1: bool = False) -> dict[str, Any]:
    job_id, execution_id = job["name"].rsplit("/", 1)[-1], execution["name"].rsplit("/", 1)[-1]
    begin, end = timestamp(job.get("createTime")), timestamp(execution.get("completionTime"))
    need(begin < end, "document_native_event_window_invalid")
    event = "hirewiz_document_native_six_case_v1" if historical_v1 else "hirewiz_document_native_required_v2"
    query = (f'resource.type="cloud_run_job" AND resource.labels.project_id="{PROJECT}" '
             f'AND resource.labels.location="{REGION}" AND resource.labels.job_name="{job_id}" '
             f'AND labels."run.googleapis.com/execution_name"="{execution_id}" '
             f'AND timestamp>="{job["createTime"]}" AND timestamp<="{execution["completionTime"]}" '
             f'AND jsonPayload.event="{event}"')
    token = None
    seen: set[str] = set()
    payload = None
    for _ in range(10):
        response = clients.entries(query, token)
        rows = response.get("entries", [])
        need(type(rows) is list and len(rows) <= 20, "document_native_log_page_bound")
        for row in rows:
            need(type(row) is dict and row.get("resource") == {"type": "cloud_run_job", "labels": {
                "project_id": PROJECT, "location": REGION, "job_name": job_id}}
                 and row.get("labels", {}).get("run.googleapis.com/execution_name") == execution_id
                 and row.get("logName") == "projects/" + PROJECT + "/logs/run.googleapis.com%2Fstdout"
                 and begin <= timestamp(row.get("timestamp")) <= end
                 and type(row.get("jsonPayload")) is dict and len(canonical(row["jsonPayload"])) <= 16384
                 and row["jsonPayload"].get("execution_name") == execution_id
                 and row["jsonPayload"].get("event") == event
                 and payload is None, "document_native_event_scope_or_uniqueness_invalid")
            payload = row["jsonPayload"]
        next_token = response.get("nextPageToken")
        if next_token in (None, ""):
            need(payload is not None, "document_native_required_event_missing")
            assert payload is not None
            return payload
        need(type(next_token) is str and 0 < len(next_token) <= 8192
             and all(33 <= ord(char) <= 126 for char in next_token) and next_token not in seen,
             "document_native_log_pagination_invalid")
        assert isinstance(next_token, str)
        seen.add(next_token)
        token = next_token
    raise NativeProofDenied("document_native_complete_logs_unavailable")


@dataclass(frozen=True)
class NativeAcceptance:
    image: str
    build_id: str
    build_resource: str
    source_archive_sha256: str
    refresh_dockerfile_sha256: str
    source_sha256: dict[str, str]
    job_name: str
    job_uid: str
    execution_name: str
    execution_uid: str
    probe_sha256: str
    receipt_sha256: str
    policy_sha256: str
    font_manifest_sha256: str
    renderer_version: str
    definition_expires_at: str
    contract_version: int
    required_case_count: int


def acceptance(clients: Clients, image: str, build: dict[str, Any], job: dict[str, Any],
               execution: dict[str, Any], *, historical_v1: bool = False,
               require_fresh: bool = True) -> NativeAcceptance:
    nonce = build["nonce"]
    verify_renderer_build(build)
    renderer, fonts = renderer_identity(image, historical_v1=historical_v1, build_identity=build)
    validate_job(job, image, nonce, historical_v1=historical_v1)
    validate_execution(execution, job, image, nonce, historical_v1=historical_v1)
    payload = native_payload(clients, job, execution, historical_v1=historical_v1)
    facts = validate_payload(payload, image, execution["name"], datetime.now(UTC),
                             require_fresh=require_fresh, historical_v1=historical_v1, build_identity=build)
    return NativeAcceptance(image, build["build_id"], build["build_resource"], build["source_archive_sha256"],
        build["refresh_dockerfile_sha256"], build["source_sha256"], job["name"], job["uid"],
        execution["name"], execution["uid"], hashlib.sha256(rendered_payload(image, historical_v1=historical_v1).encode()).hexdigest(),
        facts["payload_sha256"], LEGACY_POLICY if historical_v1 else POLICY,
        fonts, renderer,
        facts["definition_expires_at"], 1 if historical_v1 else 2, 6 if historical_v1 else 10)


def run_successor(image: str, build_identity: dict[str, Any], journal: Path) -> NativeAcceptance:
    """One new synthetic native Job; existing journal/unknown mutation cannot replay."""
    clients = None
    try:
        build = json.loads(canonical(build_identity))
        need(build.get("immutable_image") == image, "document_native_build_image_changed")
        verify_renderer_build(build)
        build_module().verify_native_build(build)
        owner = Journal(journal, image, build, create=True)
        clients = Clients()
        clients.preflight()
        body = desired(image, build["nonce"])
        try:
            clients.job(body["name"])
        except clients.errors.HttpError as error:
            need(error.status_code == 404, "document_native_fresh_job_absence_unverified")
        else:
            raise NativeProofDenied("document_native_job_exists_reconcile_only")
        deadline = time.monotonic() + 900
        owner.write("create.started.json", {"body_sha256": hashlib.sha256(canonical(body)).hexdigest(), "automatic_replay": False})
        created = clients.create(body)
        owner.write("create.acknowledged.json", {"name": created.get("name")})
        wait_operation(clients, created, "Job", deadline)
        job = owned_job(clients, owner, image, build["nonce"])
        need(job.get("executionCount") is None or integer(job.get("executionCount"), 0), "document_native_job_previously_run")
        clients.preflight()
        owner.write("run.started.json", {"job_name": job["name"], "job_uid": job["uid"], "etag": job["etag"], "automatic_replay": False})
        started = clients.start(job)
        owner.write("run.acknowledged.json", {"name": started.get("name")})
        wait_operation(clients, started, "Execution", deadline)
        result = verify_successor(image, build, journal)
        owner.write("acceptance.json", asdict(result))
        return result
    except NativeProofDenied:
        raise
    except Exception:  # noqa: BLE001 - vendor errors may contain credential-bearing request details.
        raise NativeProofDenied("document_native_outcome_unknown_reconcile_only") from None
    finally:
        if clients is not None:
            clients.close()


def verify_successor(image: str, build_identity: dict[str, Any], journal: Path) -> NativeAcceptance:
    """Fresh genuine Build/Job/Execution/log reads; no mutation or saved-result adoption."""
    clients = None
    try:
        build = json.loads(canonical(build_identity))
        need(build.get("immutable_image") == image, "document_native_build_image_changed")
        build_module().verify_native_build(build)
        owner = Journal(journal, image, build)
        owner.read("create.started.json")
        owner.read("run.started.json")
        clients = Clients()
        clients.preflight()
        job = owned_job(clients, owner, image, build["nonce"])
        execution = owned_execution(clients, owner, job, image, build["nonce"])
        return acceptance(clients, image, build, job, execution)
    except NativeProofDenied:
        raise
    except Exception:  # noqa: BLE001 - vendor errors may contain credential-bearing request details.
        raise NativeProofDenied("document_native_readonly_verification_unavailable") from None
    finally:
        if clients is not None:
            clients.close()


def verify_current_successor(image: str, service_metadata: dict[str, Any]) -> NativeAcceptance:
    """Maintenance uses only the dedicated unattended service-account identity."""
    candidate = load_source("hirewiz_document_candidate_profile", HERE / "document_candidate.py")
    if policy_for_serving(service_metadata, image) == candidate.POLICY_SHA256:
        adapter = load_source("hirewiz_document_candidate_release", HERE / "document_candidate_release.py")
        return adapter.verify_current_successor(image, service_metadata)
    result = _verify_current_successor(image, service_metadata, read_only_release=False)
    assert isinstance(result, NativeAcceptance)
    return result


def verify_current_successor_for_release(image: str, service_metadata: dict[str, Any]) -> NativeAcceptance:
    """Ordinary release uses only the original, read-only retained SDK identity."""
    candidate = load_source("hirewiz_document_candidate_profile", HERE / "document_candidate.py")
    if policy_for_serving(service_metadata, image) == candidate.POLICY_SHA256:
        adapter = load_source("hirewiz_document_candidate_release", HERE / "document_candidate_release.py")
        return adapter.verify_current_successor_for_release(image, service_metadata)
    result = _verify_current_successor(image, service_metadata, read_only_release=True)
    assert isinstance(result, NativeAcceptance)
    return result


def _verify_current_successor(image: str, service_metadata: dict[str, Any], *, read_only_release: bool,
                              retirement_only: bool = False) -> NativeAcceptance | None:
    """Both fixed native identity routes execute the identical proof checks."""
    clients = None
    try:
        metadata = json.loads(canonical(service_metadata))
        historical_v1 = historical_v1_for_serving(metadata, image)
        if retirement_only:
            need(not historical_v1 and policy_for_serving(metadata, image) == POLICY,
                 "document_native_retirement_requires_v2")
        labels = metadata.get("labels", metadata.get("metadata", {}).get("labels", {}))
        build_id, nonce, job_id = (labels.get(key) for key in (
            "hirewiz-refresh-build", "hirewiz-refresh-nonce", "hirewiz-refresh-job"))
        need(type(build_id) is str and UUID.fullmatch(build_id) and type(nonce) is str
             and re.fullmatch(r"[a-f0-9]{32}", nonce) and job_id == "hirewiz-doc-six-" + nonce[:12]
             and type(image) is str and IMAGE.fullmatch(image) and image != BASE_IMAGE,
             "document_native_current_labels_invalid")
        builds = build_module()
        if read_only_release:
            build = (builds.verify_current_build_release(build_id, nonce, image, historical_v1=True)
                     if historical_v1 else builds.verify_current_build_release(build_id, nonce, image))
            clients = Clients(read_only_release=True)
        else:
            build = (builds.verify_current_build(build_id, nonce, image, historical_v1=True)
                     if historical_v1 else builds.verify_current_build(build_id, nonce, image))
            clients = Clients()
        snapshot = clients.preflight()
        actual = clients.service(PARENT + "/services/hirewiz-document-inspector")
        actual_labels = actual.get("labels", {})
        need(actual.get("uid") == SERVICE_UID and all(actual_labels.get(key) == labels[key] for key in (
            "hirewiz-refresh-build", "hirewiz-refresh-nonce", "hirewiz-refresh-job"))
             and actual.get("template", {}).get("containers", [{}])[0].get("image") == image,
             "document_native_current_service_changed")
        snapshot.update(service=actual)
        need(validate_worker_snapshot(clients, snapshot, historical_v1=historical_v1) == image, "document_native_current_service_changed")
        job = clients.job(PARENT + "/jobs/" + job_id)
        validate_job(job, image, nonce, historical_v1=historical_v1)
        latest = job.get("latestCreatedExecution", {}).get("name")
        execution = clients.execution(execution_reference(job, latest))
        result = acceptance(clients, image, build, job, execution, historical_v1=historical_v1,
                            require_fresh=not retirement_only)
        if retirement_only:
            # Only proof that this exact expired image can be retired escapes.
            # A stale NativeAcceptance is never returned to the transition.
            expiry = datetime.fromisoformat(result.definition_expires_at)
            need(expiry.tzinfo is not None and expiry <= datetime.now(UTC),
                 "document_native_retirement_requires_expired_v2")
            return None
        return result
    except NativeProofDenied:
        raise
    except Exception:  # noqa: BLE001 - vendor errors may contain credential-bearing request details.
        raise NativeProofDenied("document_native_current_verification_unavailable") from None
    finally:
        if clients is not None:
            clients.close()


def verify_expired_current_v2_for_retirement(image: str, service_metadata: dict[str, Any]) -> None:
    """Read-only exact current V2 proof; expired definitions authorize retirement only."""
    need(_verify_current_successor(image, service_metadata, read_only_release=True,
                                   retirement_only=True) is None,
         "document_native_retirement_proof_unavailable")


def cleanup_successor(image: str, build_identity: dict[str, Any], journal: Path) -> dict[str, Any]:
    """Delete only the original owned prior Job after a different native successor serves."""
    clients = None
    try:
        build = json.loads(canonical(build_identity))
        clients = Clients()
        service = clients.service(PARENT + "/services/hirewiz-document-inspector")
        current = service.get("template", {}).get("containers", [{}])[0].get("image")
        need(current != image, "document_native_current_job_must_be_retained")
        current_acceptance = verify_current_successor(current, service)
        need(current_acceptance.contract_version == 2 and current_acceptance.required_case_count == 10
             and current_acceptance.policy_sha256 == POLICY, "document_native_v2_successor_required_for_retirement")
        verify_caller_pins(clients, current)
        nonce = build.get("nonce")
        need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce), "document_native_nonce_invalid")
        prior = clients.job(PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12])
        historical_v1 = historical_v1_for_serving({"template": prior.get("template", {}).get("template", {})}, image)
        owner = Journal(journal, image, build, historical_v1=historical_v1)
        if historical_v1:
            build_module().verify_native_build(build, historical_v1=True)
        else:
            build_module().verify_native_build(build)
        job = owned_job(clients, owner, image, nonce, historical_v1=historical_v1)
        execution = owned_execution(clients, owner, job, image, nonce, historical_v1=historical_v1)
        # Expired prior definitions cannot authorize serving, but may be retired
        # after newer native acceptance and all serving caller pins are proven.
        validate_payload(native_payload(clients, job, execution, historical_v1=historical_v1), image, execution["name"],
                         datetime.now(UTC), require_fresh=False, historical_v1=historical_v1, build_identity=build)
        owner.write("delete.started.json", {"name": job["name"], "uid": job["uid"], "etag": job["etag"], "automatic_replay": False})
        deleted = clients.delete(job)
        owner.write("delete.acknowledged.json", {"name": deleted.get("name")})
        # Delete may return google.protobuf.Empty. Current status alone never permits another Delete.
        delete_name = deleted.get("name")
        need(type(delete_name) is str, "document_native_cleanup_unknown")
        assert isinstance(delete_name, str)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = clients.operation(delete_name)
            need(result.get("name") == delete_name, "document_native_cleanup_unknown")
            if result.get("done") is True:
                need(not result.get("error"), "document_native_cleanup_unknown")
                break
            time.sleep(2)
        else:
            raise NativeProofDenied("document_native_cleanup_unknown")
        for call, name in ((clients.job, job["name"]), (clients.execution, execution["name"])):
            try:
                call(name)
            except clients.errors.HttpError as error:
                need(error.status_code == 404, "document_native_cleanup_absence_unverified")
            else:
                raise NativeProofDenied("document_native_cleanup_resource_remains")
        result = {"job_name": job["name"], "job_uid": job["uid"], "execution_name": execution["name"],
                  "execution_uid": execution["uid"], "owned_resources_absent": True}
        owner.write("cleanup.json", result)
        return result
    except NativeProofDenied:
        raise
    except Exception:  # noqa: BLE001 - vendor errors may contain credential-bearing request details.
        raise NativeProofDenied("document_native_cleanup_unknown_reconcile_only") from None
    finally:
        if clients is not None:
            clients.close()
