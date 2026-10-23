"""Build immutable scanner candidates or audit serving definition expiry.

The refresh mode builds a fixed-renderer definitions-only successor, requires
new native required-case acceptance, and performs a bounded fail-closed worker/caller
pin transition. Monitor and candidate modes grant no promotion. IAM provisioning
is separate. Vendor output stays in RAM; unknown writes never authorize replay.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROJECT, REGION, SERVICE = "ai-resume-parser-482412", "us-central1", "hirewiz-document-inspector"
REPOSITORY = f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/{SERVICE}"
RENDERER_BASE = REPOSITORY + "@sha256:624597726ce2fefa2b3574fbf0a8ec4ccdc02a826380d76c5edc8ebbd59aa107"
BUILDER = f"hirewiz-builder-20261014@{PROJECT}.iam.gserviceaccount.com"
DOCKER_BUILDER = "gcr.io/cloud-builders/docker@sha256:40c2fb4fcd0ad51376eef166c2e7b2b40a3508d5776e2bf33db3783ab39d0f2e"
AGE_LIMIT = timedelta(days=3)
ALERT_BEFORE = timedelta(hours=24)
CANDIDATE_MAX_AGE = timedelta(hours=24)
MAX_OUTPUT = 1024 * 1024


class RefreshDenied(RuntimeError):
    """Fixed nonsecret refusal; never echo vendor output or exception text."""


def need(value: Any, code: str) -> None:
    if not value:
        raise RefreshDenied(code)


def accepted() -> Any:
    spec = importlib.util.spec_from_file_location("document_refresh_contract", ROOT / "infra/gcp/document_worker.py")
    need(spec is not None and spec.loader is not None, "document_refresh_contract_unavailable")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(argv: list[str], *, seconds: int) -> bytes:
    try:
        result = subprocess.run(argv, capture_output=True, timeout=seconds, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RefreshDenied("document_refresh_operation_unavailable") from None
    need(result.returncode == 0 and len(result.stdout) <= MAX_OUTPUT,
         "document_refresh_operation_unavailable")
    return result.stdout


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        need(key not in result, "document_refresh_duplicate_json_key")
        result[key] = value
    return result


def decode(raw: bytes) -> Any:
    need(type(raw) is bytes and 0 < len(raw) <= MAX_OUTPUT, "document_refresh_metadata_bound")
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(RefreshDenied("document_refresh_nonfinite_json")))
    except (ValueError, UnicodeError, RecursionError):
        raise RefreshDenied("document_refresh_metadata_invalid") from None


def definition(raw: bytes, *, now: datetime) -> dict[str, Any]:
    """Same official header format and three-day limit as the request scanner."""
    need(type(raw) is bytes and len(raw) <= 256 and now.tzinfo is not None,
         "document_refresh_definition_header_invalid")
    try:
        match = re.fullmatch(r"ClamAV (1\.4\.3)/([0-9]{1,10})/([A-Za-z0-9 :]{24})", raw.decode("ascii").strip())
        need(match is not None, "document_refresh_definition_header_invalid")
        assert match is not None
        issued = datetime.strptime(match[3], "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
    except (ValueError, UnicodeError):
        raise RefreshDenied("document_refresh_definition_header_invalid") from None
    remaining = issued + AGE_LIMIT - now
    need(remaining <= AGE_LIMIT, "document_refresh_future_definitions_refused")
    return {"engine": "ClamAV", "version": match[1], "definitions": match[2],
            "issued_at_utc": issued.isoformat(), "expires_at_utc": (issued + AGE_LIMIT).isoformat(),
            "seconds_remaining": int(remaining.total_seconds()),
            "status": "expired" if remaining < timedelta(0) else "refresh_required" if remaining <= ALERT_BEFORE else "fresh"}


def header(docker: list[str], image: str) -> bytes:
    need(re.fullmatch(r"sha256:[a-f0-9]{64}", image) is not None
         or re.fullmatch(re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}", image) is not None,
         "document_refresh_immutable_image_required")
    return command(docker + ["run", "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=32", "--memory=256m", "--cpus=0.5",
        "--entrypoint=/usr/bin/clamscan", image, "--database=/var/lib/clamav", "--version"], seconds=30)


def _serving_pin(service: Any, contract: Any) -> tuple[str, Any]:
    """Bind the native v2 serving revision; every successor needs fresh proof."""
    need(type(service) is dict and service.get("name") == f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"
         and service.get("uid") == "7014a5ba-faee-4fbf-8272-a437b1de49ad"
         and re.fullmatch(r"[1-9][0-9]{0,18}", str(service.get("generation", "")))
         and str(service.get("generation")) == str(service.get("observedGeneration"))
         and service.get("reconciling", False) is False
         and service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED",
         "document_refresh_serving_not_ready")
    ready = service.get("latestReadyRevision")
    need(type(ready) is str and ready == service.get("latestCreatedRevision")
         and re.fullmatch(re.escape(service["name"] + "/revisions/" + SERVICE) + r"-[a-z0-9-]{1,36}", ready),
         "document_refresh_serving_not_ready")
    containers = service.get("template", {}).get("containers", [])
    need(type(containers) is list and len(containers) == 1, "document_refresh_serving_pin_drift")
    image = containers[0].get("image", "")
    need(re.fullmatch(re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}", image),
         "document_refresh_serving_pin_drift")
    traffic = service.get("trafficStatuses")
    need(type(traffic) is list and len(traffic) == 1 and type(traffic[0].get("percent")) is int
         and traffic[0]["percent"] == 100 and traffic[0].get("revision") in {ready, ready.rsplit("/", 1)[-1]}
         and not traffic[0].get("tag"), "document_refresh_serving_traffic_drift")
    verified = None
    if image != contract.ACCEPTED_IMAGE:
        sys.path.insert(0, str(ROOT / "infra/gcp"))
        from document_refresh_probe import verify_current_successor

        verified = verify_current_successor(image, service)
        need(verified.image == image, "document_refresh_serving_successor_unverified")
    return image, verified


def serving_image(service: Any, contract: Any) -> str:
    return _serving_pin(service, contract)[0]


def monitor(*, now: datetime) -> dict[str, Any]:
    contract = accepted()
    http = native_http()
    try:
        service = native_json(http, "GET", f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/services/{SERVICE}")
        image, native = _serving_pin(service, contract)
    finally:
        http.close()
    if native is None:
        # Only the initial accepted base lacks this successor's durable native
        # definition receipt. Do not download a multi-GB image every hour after
        # refresh: immutable native proof already binds its exact definitions.
        command(["docker", "pull", image], seconds=180)
        result = definition(header(["docker"], image), now=now)
    else:
        expires = datetime.fromisoformat(native.definition_expires_at)
        need(expires.tzinfo is not None and expires - now <= AGE_LIMIT,
             "document_refresh_native_definition_time_invalid")
        issued, remaining = expires - AGE_LIMIT, expires - now
        result = {"status": "expired" if remaining < timedelta(0) else
                  "refresh_required" if remaining <= ALERT_BEFORE else "fresh",
                  "issued_at_utc": issued.isoformat(), "expires_at_utc": expires.isoformat(),
                  "seconds_remaining": int(remaining.total_seconds()), "source": "fresh_native_immutable_image_receipt"}
    return {"mode": "monitor", "image": image, "native_serving_pin_observed": True,
            "definitions": result, "promotion_performed": False}


def proof_contract(proof: Any, contract: Any, source_hashes: dict[str, str], *, now: datetime) -> dict[str, Any]:
    need(type(proof) is dict and proof.get("platform") == "linux/amd64"
         and proof.get("scope") == "local_native_docker_only"
         and proof.get("cloud_run_isolation_proven") is False
         and proof.get("runtime_source_bytes_verified") is True
         and proof.get("restricted_container_metadata_verified") is True
         and proof.get("source_sha256") == source_hashes
         and re.fullmatch(r"sha256:[a-f0-9]{64}", proof.get("image_id", "")),
         "document_refresh_local_proof_unverified")
    corpus = proof.get("corpus", [])
    need(type(corpus) is list and len(corpus) == 12
         and {row.get("case") for row in corpus} == {"clean_pdf", "clean_docx", "page_counter", "active_pdf",
             "includetext", "eicar_exact_file", "eicar_zip_member", "missing_definitions",
             "clean_tex", "clean_texzip", "eicar_tex_source", "eicar_texzip_member"}
         and all(row.get("passed") is True for row in corpus), "document_refresh_corpus_incomplete")
    need(all(row.get("outcome") == "clean" and row.get("native_source_empty") is True
             for row in corpus if row["case"] in {"clean_tex", "clean_texzip"})
         and all(row.get("outcome") == "detected" for row in corpus
                 if row["case"] in {"eicar_tex_source", "eicar_texzip_member"}),
         "document_refresh_native_source_corpus_incomplete")
    layouts = proof.get("docx_layout_corpus", [])
    need(type(layouts) is list and len(layouts) == 2
         and {row.get("case") for row in layouts} == {"styled_multipage_fit", "rendered_reflow_refusal"}
         and all(row.get("passed") is True for row in layouts), "document_refresh_layout_incomplete")
    fit = next(row for row in layouts if row["case"] == "styled_multipage_fit")
    renderer = fit.get("layout", {}).get("renderer", {})
    need(type(renderer) is dict and renderer.get("renderer_version") == contract.ACCEPTED_RENDERER
         and renderer.get("font_manifest_sha256") == contract.ACCEPTED_FONT_MANIFEST
         and renderer.get("policy_sha256") == contract.POLICY_SHA256
         and renderer.get("image_digest") == "local-document-image@" + proof["image_id"]
         and re.fullmatch(r"[a-f0-9]{64}", fit.get("layout", {}).get("rendered_fonts_sha256", "")),
         "document_refresh_renderer_changed_review_required")
    clamd = proof.get("request_clamd_corpus", [])
    need(type(clamd) is list and len(clamd) == 11
         and len({row.get("case") for row in clamd}) == 11
         and all(row.get("daemon_stopped_before_writer") is True and row.get("native_log_removed") is True
                 and row.get("daemon_returncode") == 0 for row in clamd), "document_refresh_clamd_incomplete")
    scans = [row["scan"] for row in corpus if row.get("scan")]
    need(bool(scans) and all(row == scans[0] for row in scans), "document_refresh_definition_disagreement")
    try:
        number, issued = scans[0]["definitions"].split("/", 1)
        timestamp = datetime.fromisoformat(issued)
        need(timestamp.tzinfo is not None and re.fullmatch(r"[0-9]{1,10}", number),
             "document_refresh_definition_header_invalid")
        age = now - timestamp
    except (KeyError, TypeError, ValueError):
        raise RefreshDenied("document_refresh_definition_header_invalid") from None
    need(timedelta(0) <= age < CANDIDATE_MAX_AGE, "document_refresh_candidate_not_fresh")
    return {"issued_at_utc": timestamp.isoformat(), "expires_at_utc": (timestamp + AGE_LIMIT).isoformat(),
            "definition_number": number, "local_cases": 25,
            "renderer_version": contract.ACCEPTED_RENDERER,
            "rendered_fonts_sha256": fit["layout"]["rendered_fonts_sha256"]}


def write_new(path: Path, value: Any) -> str:
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


# The old one-file refresh profile remains unchanged. This distinct profile
# publishes current parser/validator source; its native V2 proof is still separate.
FULL_SOURCE_KIND = "native_cloud_build_full_source_v1"
# Keep the existing narrowly authorized nonce-owned source namespace.
# Exact Build/result tags and steps distinguish the two finite profiles.
FULL_SOURCE_PREFIX = "hirewiz-document-refresh-source/"
FULL_SOURCE_POLICY = "infra/document-processing/policy.json"
FULL_SOURCE_DOCKERFILE = "infra/document-processing/Dockerfile"
FULL_SOURCE_MAX_BYTES = 4 * 1024 * 1024


def full_source_snapshot() -> tuple[dict[str, bytes], dict[str, str], str]:
    """Exactly the reviewed thirteen source files plus their bound policy."""
    sys.path.insert(0, str(ROOT / "backend"))
    from scripts import document_test_image as engines

    need(len(engines.SOURCES) == 13 and len(set(engines.SOURCES)) == 13,
         "document_source_manifest_invalid")
    contents = {}
    for name in (*engines.SOURCES, FULL_SOURCE_POLICY):
        path = ROOT
        for component in name.split("/"):
            need(component not in {"", ".", ".."}, "document_source_path_invalid")
            path = path / component
            need(not path.is_symlink(), "document_source_path_invalid")
        need(path.is_file() and 0 < path.stat().st_size <= 1024 * 1024,
             "document_source_file_bound")
        contents[name] = path.read_bytes()
    hashes = {name: hashlib.sha256(contents[name]).hexdigest() for name in engines.SOURCES}
    policy = contents[FULL_SOURCE_POLICY]
    policy_value = decode(policy)
    need(type(policy_value) is dict and policy_value.get("source_sha256") == hashes
         and hashlib.sha256(policy).hexdigest() == accepted().POLICY_SHA256,
         "document_source_policy_changed")
    need(sum(map(len, contents.values())) <= FULL_SOURCE_MAX_BYTES,
         "document_source_archive_bound")
    return contents, hashes, hashlib.sha256(policy).hexdigest()


def _full_source_tar() -> bytes:
    contents, _, _ = full_source_snapshot()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, content in sorted(contents.items()):
            row = tarfile.TarInfo(name)
            row.size, row.mode, row.mtime, row.uid, row.gid = len(content), 0o644, 0, 0, 0
            archive.addfile(row, io.BytesIO(content))
    raw = output.getvalue()
    need(len(raw) <= FULL_SOURCE_MAX_BYTES, "document_source_archive_bound")
    return raw


def full_source_archive() -> bytes:
    import gzip

    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as zipped:
        zipped.write(_full_source_tar())
    raw = output.getvalue()
    need(len(raw) <= FULL_SOURCE_MAX_BYTES, "document_source_archive_bound")
    return raw


def archive_full_source(raw: bytes) -> str:
    """Compare bounded bytes with canonical source; never parse untrusted tar."""
    import zlib

    need(type(raw) is bytes and 0 < len(raw) <= FULL_SOURCE_MAX_BYTES,
         "document_source_archive_bound")
    expected = _full_source_tar()
    try:
        decoder = zlib.decompressobj(wbits=31)
        actual = decoder.decompress(raw, len(expected) + 1)
        need(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
             and actual == expected, "document_source_archive_changed")
    except zlib.error:
        raise RefreshDenied("document_source_archive_changed") from None
    return hashlib.sha256(raw).hexdigest()


def full_source_build_request(nonce: str, source: dict[str, str]) -> dict[str, Any]:
    need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce),
         "document_refresh_build_nonce_invalid")
    _, hashes, _ = full_source_snapshot()
    tag = REPOSITORY + ":source-" + nonce
    body = build_request(nonce, source)
    arguments = ["build", "--pull=false", "--platform=linux/amd64", "--build-arg",
                 "DOCUMENT_DEFINITIONS_NONCE=" + nonce, "--file", FULL_SOURCE_DOCKERFILE]
    for name, digest in sorted(hashes.items()):
        arguments.extend(("--label", "hirewiz.document-test.source." + name + "=" + digest))
    arguments.extend(("--tag", tag, "."))
    body.update(steps=[{"name": DOCKER_BUILDER, "args": arguments},
                       {"name": DOCKER_BUILDER, "args": ["push", tag]}], images=[tag])
    return body


def build_contract(value: Any, nonce: str, recipe_sha: str, *, historical_v1: bool = False, full_source: bool = False) -> dict[str, Any]:
    """Check actual native Build fields; a caller-supplied summary is insufficient."""
    need(type(value) is dict and re.fullmatch(r"[a-f0-9]{32}", nonce), "document_refresh_native_build_invalid")
    build_id = value.get("id", "")
    need(re.fullmatch(r"[a-f0-9-]{36}", build_id) and value.get("projectId") == PROJECT
         and value.get("name") in {f"projects/{PROJECT}/locations/{REGION}/builds/{build_id}",
             f"projects/157225724590/locations/{REGION}/builds/{build_id}"}
         and value.get("status") == "SUCCESS" and value.get("serviceAccount") in {
             f"projects/{PROJECT}/serviceAccounts/{BUILDER}", f"projects/157225724590/serviceAccounts/{BUILDER}"},
         "document_refresh_native_build_identity_unverified")
    need(type(full_source) is bool and not (historical_v1 and full_source), "document_refresh_contract_version_invalid")
    tag = REPOSITORY + (":source-" if full_source else ":refresh-") + nonce
    expected = [["build", "--pull=false", "--platform=linux/amd64", "--build-arg",
        "DOCUMENT_DEFINITIONS_NONCE=" + nonce, "--tag", tag, "."], ["push", tag]]
    if full_source:
        expected = [row["args"] for row in full_source_build_request(nonce, {})["steps"]]
    steps = value.get("steps")
    need(type(steps) is list and len(steps) == 2 and all(
        row.get("name") == DOCKER_BUILDER and row.get("args") == args
        and row.get("dir", "") in ("", ".") and not row.get("env") and not row.get("secretEnv")
        and not row.get("volumes") and not row.get("script") and not row.get("entrypoint")
        and not row.get("automapSubstitutions") and not row.get("allowFailure") and not row.get("allowExitCodes")
        for row, args in zip(steps, expected, strict=True)), "document_refresh_native_build_steps_changed")
    options = value.get("options", {})
    need(type(options) is dict and options.get("logging") == "CLOUD_LOGGING_ONLY"
         and value.get("timeout") == "900s"
         and not any(options.get(key) for key in ("env", "secretEnv", "volumes", "automapSubstitutions", "pool"))
         and not any(value.get(key) for key in ("availableSecrets", "secrets", "substitutions", "automapSubstitutions")),
         "document_refresh_native_build_execution_inputs_changed")
    images = value.get("results", {}).get("images", [])
    need(type(images) is list and len(images) == 1 and images[0].get("name") == tag
         and re.fullmatch(r"sha256:[a-f0-9]{64}", images[0].get("digest", "")),
         "document_refresh_native_build_image_unverified")
    source = value.get("source", {}).get("storageSource", {})
    resolved = value.get("sourceProvenance", {}).get("resolvedStorageSource", {})
    need(source == resolved and source.get("bucket") == PROJECT + "_cloudbuild"
         and type(source.get("object")) is str
         and source["object"] == (FULL_SOURCE_PREFIX if full_source else "hirewiz-document-refresh-source/") + nonce + "/source.tar.gz"
         and re.fullmatch(r"[1-9][0-9]{0,19}", str(source.get("generation", ""))),
         "document_refresh_native_source_unverified")
    renderer_base = RENDERER_BASE
    if historical_v1:
        sys.path.insert(0, str(ROOT / "infra/gcp"))
        from document_refresh_probe import LEGACY_BASE_IMAGE

        renderer_base = LEGACY_BASE_IMAGE
    result = {"version": 1, "kind": FULL_SOURCE_KIND if full_source else "native_cloud_build", "nonce": nonce, "build_id": build_id,
        "build_resource": value.get("name"), "builder_service_account": BUILDER,
        "source_bucket": source["bucket"], "source_object": source["object"],
        "source_generation": str(source["generation"]), "refresh_dockerfile_sha256": recipe_sha,
        "fixed_renderer_base": renderer_base, "immutable_image": REPOSITORY + "@" + images[0]["digest"]}
    if full_source:
        contents, _, policy = full_source_snapshot()
        need(recipe_sha == hashlib.sha256(contents[FULL_SOURCE_DOCKERFILE]).hexdigest(),
             "document_source_recipe_changed")
        # Existing acceptance receipts retain their historical recipe-key name.
        # Full-source builds do not claim to reuse the old renderer image.
        result.update(fixed_renderer_base=None, policy_sha256=policy)
    return result


def archive_recipe(raw: bytes, expected_sha: str) -> str:
    need(type(raw) is bytes and 0 < len(raw) <= 262144, "document_refresh_source_archive_bound")
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            rows = archive.getmembers()
            need(len(rows) == 1 and rows[0].isfile() and rows[0].name in {"Dockerfile", "./Dockerfile"}
                 and 0 < rows[0].size <= 16384, "document_refresh_source_archive_changed")
            stream = archive.extractfile(rows[0])
            need(stream is not None, "document_refresh_source_archive_changed")
            assert stream is not None
            need(hashlib.sha256(stream.read(16385)).hexdigest() == expected_sha,
                 "document_refresh_source_archive_changed")
    except (tarfile.TarError, OSError):
        raise RefreshDenied("document_refresh_source_archive_changed") from None
    return hashlib.sha256(raw).hexdigest()


def native_http():
    sys.path.insert(0, str(ROOT / "infra/gcp"))
    from document_refresh_auth import ServiceHttp

    return ServiceHttp(os.environ.get("DOCUMENT_REFRESH_CONTROLLER_UID", ""))


def native_json(http: Any, method: str, uri: str, body: Any = None) -> Any:
    raw = None if body is None else json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
    response, content = http.request(uri, method=method, body=raw,
        headers={"Content-Type": "application/json"}, redirections=0)
    need(response.status in (200, 201), "document_refresh_native_operation_unavailable_reconcile_only")
    return decode(content)


def source_archive(recipe: bytes) -> bytes:
    import gzip

    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            row = tarfile.TarInfo("Dockerfile")
            row.size, row.mode, row.mtime, row.uid, row.gid = len(recipe), 0o644, 0, 0, 0
            archive.addfile(row, io.BytesIO(recipe))
    return output.getvalue()


def read_native_source(http: Any, identity: dict[str, Any]) -> bytes:
    from urllib.parse import quote, urlencode

    uri = "https://storage.googleapis.com/download/storage/v1/b/" + identity["source_bucket"] + "/o/" \
        + quote(identity["source_object"], safe="") + "?" + urlencode({"alt": "media", "generation": identity["source_generation"]})
    response, raw = http.request(uri, method="GET", redirections=0)
    need(response.status == 200, "document_refresh_native_source_unavailable")
    return raw


def build_request(nonce: str, source: dict[str, str]) -> dict[str, Any]:
    tag = REPOSITORY + ":refresh-" + nonce
    return {"source": {"storageSource": source}, "serviceAccount": f"projects/{PROJECT}/serviceAccounts/{BUILDER}",
        "steps": [{"name": DOCKER_BUILDER, "args": ["build", "--pull=false", "--platform=linux/amd64", "--build-arg",
            "DOCUMENT_DEFINITIONS_NONCE=" + nonce, "--tag", tag, "."]},
            {"name": DOCKER_BUILDER, "args": ["push", tag]}], "images": [tag],
        "options": {"logging": "CLOUD_LOGGING_ONLY"}, "timeout": "900s"}


def native_build(directory: Path, recipe: bytes, *, nonce: str | None = None, full_source: bool = False) -> dict[str, Any]:
    from urllib.parse import urlencode

    nonce = uuid.uuid4().hex if nonce is None else nonce
    need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce), "document_refresh_build_nonce_invalid")
    need(type(full_source) is bool, "document_refresh_contract_version_invalid")
    recipe_sha = hashlib.sha256(recipe).hexdigest()
    if full_source:
        contents, _, _ = full_source_snapshot()
        need(recipe == contents[FULL_SOURCE_DOCKERFILE], "document_source_recipe_changed")
    archive = full_source_archive() if full_source else source_archive(recipe)
    archive_sha = archive_full_source(archive) if full_source else archive_recipe(archive, recipe_sha)
    source_name = (FULL_SOURCE_PREFIX if full_source else "hirewiz-document-refresh-source/") + nonce + "/source.tar.gz"
    write_new(directory / "build-started.json", {"nonce": nonce, "recipe_sha256": recipe_sha,
        "source_archive_sha256": archive_sha, "automatic_replay": False,
        "unknown_outcome": "reconcile_exact_native_object_and_build_only"})
    http = native_http()
    try:
        uri = "https://storage.googleapis.com/upload/storage/v1/b/" + PROJECT + "_cloudbuild/o?" + urlencode({
            "uploadType": "media", "name": source_name, "ifGenerationMatch": "0"})
        response, raw = http.request(uri, method="POST", body=archive,
            headers={"Content-Type": "application/gzip"}, redirections=0)
        need(response.status in (200, 201), "document_refresh_source_upload_unknown_reconcile_only")
        obj = decode(raw)
        need(obj.get("name") == source_name and obj.get("bucket") == PROJECT + "_cloudbuild"
             and re.fullmatch(r"[1-9][0-9]{0,19}", str(obj.get("generation", ""))),
             "document_refresh_source_upload_identity_unverified")
        source = {"bucket": obj["bucket"], "object": obj["name"], "generation": str(obj["generation"])}
        write_new(directory / "source-upload-acknowledged.json", source)
        body = full_source_build_request(nonce, source) if full_source else build_request(nonce, source)
        write_new(directory / "build-create-started.json", {"nonce": nonce,
            "request_sha256": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "automatic_replay": False})
        operation = native_json(http, "POST", f"https://cloudbuild.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/builds", body)
        build_id = operation.get("metadata", {}).get("build", {}).get("id", "")
        need(re.fullmatch(r"[a-f0-9-]{36}", build_id), "document_refresh_build_outcome_unknown_reconcile_only")
        write_new(directory / "build-acknowledged.json", {"nonce": nonce, "build_id": build_id,
            "operation_name": operation.get("name"), "automatic_replay": False})
        deadline = time.monotonic() + 960
        while True:
            value = native_json(http, "GET", f"https://cloudbuild.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/builds/{build_id}")
            need(value.get("id") == build_id, "document_refresh_build_identity_changed")
            if value.get("status") == "SUCCESS":
                break
            need(value.get("status") in {"PENDING", "QUEUED", "WORKING"}, "document_refresh_build_failed_reconcile_only")
            need(time.monotonic() < deadline, "document_refresh_build_deadline_reconcile_only")
            time.sleep(min(10, max(0, deadline - time.monotonic())))
        identity = (build_contract(value, nonce, recipe_sha, full_source=True) if full_source
                    else build_contract(value, nonce, recipe_sha))
        stored_source = read_native_source(http, identity)
        native_archive_sha = archive_full_source(stored_source) if full_source else archive_recipe(stored_source, recipe_sha)
        need(native_archive_sha == archive_sha, "document_refresh_native_source_changed")
        identity["source_archive_sha256"] = native_archive_sha
        write_new(directory / "native-build-identity.json", identity)
        return identity
    finally:
        http.close()


def verify_native_build(identity: dict[str, Any], *, historical_v1: bool = False) -> dict[str, Any]:
    """Fresh native Build+generation-bound source read; zero provider mutations."""
    keys = {"version", "kind", "nonce", "build_id", "build_resource", "builder_service_account",
        "source_bucket", "source_object", "source_generation", "source_archive_sha256",
        "refresh_dockerfile_sha256", "fixed_renderer_base", "immutable_image",
        "source_sha256", "local_native_proof_sha256"}
    full_source = type(identity) is dict and identity.get("kind") == FULL_SOURCE_KIND
    if full_source:
        need(not historical_v1, "document_source_historical_profile_refused")
        keys.add("policy_sha256")
    need(type(identity) is dict and set(identity) == keys and type(identity.get("version")) is int
         and identity["version"] == 1 and identity.get("kind") in {"native_cloud_build", FULL_SOURCE_KIND}
         and type(identity.get("build_id")) is str and re.fullmatch(r"[a-f0-9-]{36}", identity["build_id"])
         and type(identity.get("nonce")) is str and re.fullmatch(r"[a-f0-9]{32}", identity["nonce"]),
         "document_refresh_native_build_identity_unverified")
    need(re.fullmatch(r"[a-f0-9]{64}", identity.get("local_native_proof_sha256", "")),
         "document_refresh_build_recipe_changed")
    actual = (verify_current_build(identity["build_id"], identity["nonce"], identity["immutable_image"], historical_v1=True)
              if historical_v1 else verify_current_build(identity["build_id"], identity["nonce"], identity["immutable_image"]))
    need(all(identity[key] == value for key, value in actual.items()),
         "document_refresh_native_build_readback_changed")
    return actual


def verify_current_build(build_id: str, nonce: str, image: str, *, historical_v1: bool = False) -> dict[str, Any]:
    """Dedicated service-identity verifier for scheduled maintenance."""
    return _verify_current_build(build_id, nonce, image, release_read_only=False, historical_v1=historical_v1)


def verify_current_build_release(build_id: str, nonce: str, image: str, *, historical_v1: bool = False) -> dict[str, Any]:
    """Fixed normal-manager read-only entry; no ADC or controller-UID dependency."""
    return _verify_current_build(build_id, nonce, image, release_read_only=True, historical_v1=historical_v1)


def _verify_current_build(build_id: str, nonce: str, image: str, *, release_read_only: bool, historical_v1: bool = False) -> dict[str, Any]:
    """Reconstruct current native provenance without asserting a lost local proof."""
    need(type(build_id) is str and re.fullmatch(r"[a-f0-9-]{36}", build_id)
         and type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce)
         and type(image) is str and re.fullmatch(re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}", image),
         "document_refresh_native_build_identity_unverified")
    need(type(historical_v1) is bool, "document_refresh_contract_version_invalid")
    if historical_v1:
        sys.path.insert(0, str(ROOT / "infra/gcp"))
        from document_refresh_probe import legacy_source

        recipe = legacy_source("refresh.Dockerfile")
    else:
        recipe = (ROOT / "infra/document-processing/refresh.Dockerfile").read_bytes()
    recipe_sha = hashlib.sha256(recipe).hexdigest()
    if release_read_only:
        sys.path.insert(0, str(ROOT / "infra/gcp"))
        from document_refresh_auth import ReleaseReadOnlyHttp

        http = ReleaseReadOnlyHttp()
    else:
        http = native_http()
    try:
        value = native_json(http, "GET", f"https://cloudbuild.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/builds/{build_id}")
        images = value.get("results", {}).get("images", [])
        full_source = (type(images) is list and len(images) == 1 and type(images[0]) is dict
                       and images[0].get("name") == REPOSITORY + ":source-" + nonce)
        need(not (full_source and historical_v1), "document_source_historical_profile_refused")
        if full_source:
            recipe = (ROOT / FULL_SOURCE_DOCKERFILE).read_bytes()
            recipe_sha = hashlib.sha256(recipe).hexdigest()
            actual = build_contract(value, nonce, recipe_sha, full_source=True)
        else:
            actual = build_contract(value, nonce, recipe_sha, historical_v1=historical_v1)
        need(actual["immutable_image"] == image, "document_refresh_native_build_readback_changed")
        stored_source = read_native_source(http, actual)
        archive_sha = archive_full_source(stored_source) if full_source else archive_recipe(stored_source, recipe_sha)
    finally:
        http.close()
    sys.path.insert(0, str(ROOT / "backend"))
    from scripts import document_test_image as engines

    if historical_v1:
        source_hashes = json.loads(legacy_source("policy.json"))["source_sha256"]
    else:
        source_hashes = engines.source_hashes()
    actual.update(source_archive_sha256=archive_sha, source_sha256=source_hashes)
    return actual


def candidate(directory: Path, *, publish: bool, now: datetime, nonce: str | None = None) -> dict[str, Any]:
    # Import only the existing synthetic build/check helper; never the API app.
    sys.path.insert(0, str(ROOT / "backend"))
    from scripts import document_test_image as engines

    contract = accepted()
    need(publish, "document_refresh_explicit_native_build_required")
    before = engines.source_hashes()
    docker = engines.docker_command()
    # FreshClam changes only the database directory; no apt/compiler/font changes.
    # Never use the previous daily successor as the parent.
    recipe = ROOT / "infra/document-processing/refresh.Dockerfile"
    raw = recipe.read_bytes()
    need(raw.count(RENDERER_BASE.encode()) == 2, "document_refresh_fixed_base_changed")
    recipe_sha = hashlib.sha256(raw).hexdigest()
    identity = native_build(directory, raw) if nonce is None else native_build(directory, raw, nonce=nonce)
    digest = identity["immutable_image"]
    command(docker + ["pull", digest], seconds=180)
    installed = decode(command(docker + ["image", "inspect", digest], seconds=15))
    need(type(installed) is list and len(installed) == 1
         and digest in installed[0].get("RepoDigests", []), "document_refresh_pulled_digest_unverified")
    image = installed[0]["Id"]
    need(recipe.read_bytes() == raw, "document_refresh_source_changed")
    proof = engines.check("linux/amd64", image=image)
    facts = proof_contract(proof, contract, before, now=datetime.now(UTC))
    need(engines.source_hashes() == before, "document_refresh_source_changed")
    proof_sha = write_new(directory / "local-native-proof.json", proof)
    image = proof["image_id"]
    observed = definition(header(docker, image), now=datetime.now(UTC))
    need(observed["issued_at_utc"] == facts["issued_at_utc"] and observed["status"] == "fresh",
         "document_refresh_built_header_mismatch")
    identity.update(source_sha256=before, local_native_proof_sha256=proof_sha)
    return {"mode": "candidate", "status": "candidate_requires_native_acceptance", "image_id": image,
            "fixed_renderer_base": RENDERER_BASE, "refresh_dockerfile_sha256": recipe_sha,
            "candidate_registry_digest": digest, "previous_accepted_image": contract.ACCEPTED_IMAGE,
            "build_identity": identity,
            "local_proof_sha256": proof_sha, "freshness": facts, "source_sha256": before,
            "native_isolation_proven": False, "native_corpus_proven": False,
            "font_manifest_native_verified": False, "promotion_performed": False,
            "next_actions": ["native_stage_without_customer_traffic", "native_source_font_and_isolation_acceptance",
                "native_positive_negative_corpus_and_lifecycle_acceptance", "review_worker_and_client_pin_transition",
                "controlled_worker_and_api_rollout", "verify_exact_serving_image_header_and_complete_upload_path"]}


def full_source_candidate(directory: Path, *, publish: bool, now: datetime) -> dict[str, Any]:
    """Publish/check exact source only; no Job, deployment, IAM or promotion."""
    sys.path.insert(0, str(ROOT / "backend"))
    from scripts import document_test_image as engines

    need(publish, "document_refresh_explicit_native_build_required")
    contents, before, policy = full_source_snapshot()
    identity = native_build(directory, contents[FULL_SOURCE_DOCKERFILE], full_source=True)
    digest = identity["immutable_image"]
    docker = engines.docker_command()
    command(docker + ["pull", digest], seconds=180)
    installed = decode(command(docker + ["image", "inspect", digest], seconds=15))
    need(type(installed) is list and len(installed) == 1 and digest in installed[0].get("RepoDigests", []),
         "document_refresh_pulled_digest_unverified")
    proof = engines.check("linux/amd64", image=installed[0]["Id"])
    facts = proof_contract(proof, accepted(), before, now=datetime.now(UTC))
    need(full_source_snapshot()[1:] == (before, policy), "document_refresh_source_changed")
    proof_sha = write_new(directory / "local-native-proof.json", proof)
    observed = definition(header(docker, proof["image_id"]), now=datetime.now(UTC))
    need(observed["issued_at_utc"] == facts["issued_at_utc"] and observed["status"] == "fresh",
         "document_refresh_built_header_mismatch")
    identity.update(source_sha256=before, local_native_proof_sha256=proof_sha)
    return {"mode": "full-source-candidate", "status": "candidate_requires_native_acceptance",
            "candidate_registry_digest": digest, "image_id": proof["image_id"], "build_identity": identity,
            "source_sha256": before, "policy_sha256": policy, "freshness": facts,
            "local_proof_sha256": proof_sha, "native_isolation_proven": False,
            "native_corpus_proven": False, "promotion_performed": False}


def infra(name: str) -> Any:
    need(name in {"document_refresh_probe", "document_refresh_transition", "document_refresh_recovery"},
         "document_refresh_module_refused")
    spec = importlib.util.spec_from_file_location("hirewiz_" + name, ROOT / "infra/gcp" / (name + ".py"))
    need(spec is not None and spec.loader is not None, "document_refresh_module_unavailable")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def refresh(directory: Path, *, publish: bool, now: datetime, prior_journal: Path | None = None,
            history: Path | None = None, recover_failed_native_job: str | None = None) -> dict[str, Any]:
    """Full controlled pipeline. No serialized acceptance result grants authority."""
    need(recover_failed_native_job is None or publish is True, "document_refresh_recovery_requires_explicit_refresh")
    nonce = infra("document_refresh_recovery").prepare(
        directory, history, recover_failed_native_job, refresh_module=sys.modules[__name__])
    probe = infra("document_refresh_probe")
    preflight = probe.Clients()
    try:
        preflight.preflight()
    finally:
        preflight.close()
    value = (candidate(directory, publish=publish, now=now) if nonce is None else
             candidate(directory, publish=publish, now=now, nonce=nonce))
    image, build = value["candidate_registry_digest"], value["build_identity"]
    native = directory / "native-proof"
    acceptance = probe.run_successor(image, build, native)
    # The transition freshly verifies Build/Job/Execution/logs again before writes.
    controller = infra("document_refresh_transition")
    http = native_http()
    try:
        result = controller.transition(image, build, native, directory / "transition", http=http, probe=probe)
    finally:
        http.close()
    result["definition_expires_at"] = acceptance.definition_expires_at
    if prior_journal is None and history is not None and result["prior_image"] != RENDERER_BASE:
        matches = []
        for binding_path in history.glob("*/native-proof/binding.json"):
            # Retained failed journals are evidence, never a serving predecessor
            # or a deletion target, even if another Job proved identical bytes.
            acceptance_path = binding_path.parent / "acceptance.json"
            if (acceptance_path.is_file() and not acceptance_path.is_symlink()
                    and not binding_path.is_symlink() and binding_path.stat().st_size <= MAX_OUTPUT):
                binding = decode(binding_path.read_bytes())
                if binding.get("image") == result["prior_image"]:
                    matches.append(binding_path.parent)
        need(len(matches) == 1, "document_refresh_prior_owned_history_unavailable")
        prior_journal = matches[0]
    if prior_journal is not None:
        # Only the actual prior owned journal can retire that exact Job. A prior
        # acceptance JSON or current labels never synthesize old mutation ACKs.
        info = prior_journal.lstat()
        need(prior_journal.is_absolute() and not prior_journal.is_symlink()
             and info.st_uid == os.getuid(), "document_refresh_prior_journal_refused")
        retained = retain_prior_journal(prior_journal, directory / "prior-native-retirement")
        binding = decode((retained / "binding.json").read_bytes())
        result["prior_cleanup"] = probe.cleanup_successor(binding["image"], binding["build_identity"], retained)
    return result


def retain_prior_journal(source: Path, target: Path) -> Path:
    """Preserve genuine historical bytes and new Delete markers in this run's artifact."""
    info = source.lstat()
    need(source.resolve() == source and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
         and stat.S_IMODE(info.st_mode) == 0o700 and not target.exists(),
         "document_refresh_prior_journal_refused")
    entries = list(source.iterdir())
    need(1 <= len(entries) <= 32, "document_refresh_prior_journal_bound")
    target.mkdir(mode=0o700)
    for entry in entries:
        need(re.fullmatch(r"[a-z][a-z0-9.-]{0,99}\.json", entry.name), "document_refresh_prior_journal_refused")
        fd = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            status = os.fstat(stream.fileno())
            need(stat.S_ISREG(status.st_mode) and status.st_nlink == 1 and status.st_uid == os.getuid()
                 and stat.S_IMODE(status.st_mode) == 0o600 and 0 < status.st_size <= MAX_OUTPUT,
                 "document_refresh_prior_journal_refused")
            raw = stream.read(MAX_OUTPUT + 1)
        need(0 < len(raw) <= MAX_OUTPUT, "document_refresh_prior_journal_bound")
        fd = os.open(target / entry.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return target


def emit_status(result: dict[str, Any]) -> None:
    """Fixed nonsecret monitoring payload; absence of this heartbeat also alerts."""
    definitions = result.get("definitions", {})
    state = definitions.get("status", "unavailable") if result.get("mode") == "monitor" else "refreshed"
    need(state in {"fresh", "refresh_required", "expired", "unavailable", "refreshed"},
         "document_refresh_monitor_status_refused")
    payload = {"event": "hirewiz_document_definition_maintenance_v1", "status": state,
               "controller": "hirewiz-document-refresh", "version": 1}
    if type(definitions.get("seconds_remaining")) is int:
        payload["seconds_remaining"] = definitions["seconds_remaining"]
    http = native_http()
    try:
        native_json(http, "POST", "https://logging.googleapis.com/v2/entries:write", {
            "logName": "projects/" + PROJECT + "/logs/hirewiz-document-refresh",
            "resource": {"type": "global", "labels": {"project_id": PROJECT}},
            "entries": [{"severity": "INFO" if state in {"fresh", "refreshed"} else "ERROR", "jsonPayload": payload}]})
    finally:
        http.close()


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("monitor", "candidate", "full-source-candidate", "refresh", "reconcile"), required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--publish-candidate", action="store_true")
    parser.add_argument("--prior-native-journal", type=Path)
    parser.add_argument("--transition-journal", type=Path)
    parser.add_argument("--prior-history-directory", type=Path)
    parser.add_argument("--recover-failed-native-job")
    args = parser.parse_args()
    need(args.recover_failed_native_job is None or args.mode == "refresh" and args.publish_candidate,
         "document_refresh_recovery_requires_explicit_refresh")
    need(args.output_directory.is_absolute() and not args.output_directory.exists(),
         "document_refresh_fresh_output_directory_required")
    args.output_directory.mkdir(mode=0o700)
    if args.mode == "monitor":
        result = monitor(now=datetime.now(UTC))
        emit_status(result)
    elif args.mode == "refresh":
        result = refresh(args.output_directory, publish=args.publish_candidate,
                         now=datetime.now(UTC), prior_journal=args.prior_native_journal, history=args.prior_history_directory,
                         recover_failed_native_job=args.recover_failed_native_job)
        emit_status(result)
    elif args.mode == "full-source-candidate":
        result = full_source_candidate(args.output_directory, publish=args.publish_candidate, now=datetime.now(UTC))
    elif args.mode == "reconcile":
        need(args.transition_journal is not None, "document_refresh_transition_journal_required")
        http = native_http()
        try:
            result = infra("document_refresh_transition").reconcile(args.transition_journal, http=http)
        finally:
            http.close()
    else:
        result = candidate(args.output_directory, publish=args.publish_candidate, now=datetime.now(UTC))
    write_new(args.output_directory / "result.json", result)
    print(json.dumps(result, sort_keys=True))
    if args.mode == "monitor" and result["definitions"]["status"] != "fresh":
        raise RefreshDenied("document_refresh_expiry_alert")


def run_cli() -> int:
    try:
        main()
        return 0
    except Exception as error:
        # Reporting is an independent bounded write, never a retry of the failed
        # Build/Job/PATCH. Missing-heartbeat alert covers unavailable auth/logging.
        try:
            emit_status({"mode": "monitor", "definitions": {"status": "unavailable"}})
        except Exception:
            pass
        code = str(error) if type(error) is RefreshDenied else "document_refresh_unavailable"
        print(json.dumps({"status": "maintenance_attention_required", "fixed_code": code,
                          "state_requires_read_only_reconciliation": True, "automatic_replay": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
