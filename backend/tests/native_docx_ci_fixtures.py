"""Genuine existing CI DOCX engine with local emulated identity/HTTP delivery.

No source, scanner, font, geometry or custody receipt is manufactured here.
This is local Docker/application evidence; it does not prove GCP IAM or HTTP.
Only tests that explicitly call configure_native_docx_worker use this adapter.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import httpx
from backend.scripts import document_test_image as engine


def _complete_corpus(report: dict, contracts: list) -> tuple[list, list]:
    """Validate all observed groups with the original per-case native validator."""
    corpus = report.get("corpus")
    layouts = report.get("docx_layout_corpus")
    clamd = report.get("request_clamd_corpus")
    ordinary_names = {"clean_pdf", "clean_docx", "page_counter", "active_pdf", "includetext",
                      "eicar_exact_file", "eicar_zip_member", "missing_definitions",
                      "clean_tex", "clean_texzip", "eicar_tex_source", "eicar_texzip_member"}
    layout_names = {"styled_multipage_fit", "rendered_reflow_refusal"}
    if (type(corpus) is not list or len(corpus) != 12
            or type(layouts) is not list or len(layouts) != 2
            or any(type(row) is not dict or type(row.get("case")) is not str or row.get("passed") is not True for row in corpus + layouts)
            or {row.get("case") for row in corpus} != ordinary_names
            or {row.get("case") for row in layouts} != layout_names
            or type(clamd) is not list or len(clamd) != 11
            or any(type(row) is not dict or type(row.get("case")) is not str for row in clamd)
            or type(contracts) is not list or len(contracts) != 11
            or any(type(row) is not tuple or len(row) != 4 or type(row[0]) is not str
                   or type(row[1]) is not bytes or type(row[2]) is not str
                   or type(row[3]) is not bool for row in contracts)):
        raise AssertionError("The complete genuine native corpus must pass before runtime reuse.")
    names = [name for name, _, _, _ in contracts]
    if (len(set(names)) != 11 or {row.get("case") for row in clamd} != set(names)
            or ordinary_names | layout_names | set(names) != set(engine._FAILURE_CASES)):
        raise AssertionError("The complete genuine native corpus must pass before runtime reuse.")
    by_name = {row["case"]: row for row in clamd}
    for name, payload, expected, resident in contracts:
        engine.validate_clamd_native(by_name[name], name, expected, resident, payload, report["platform"])
    return corpus, layouts


@lru_cache(maxsize=1)
def _runtime(report_path: str) -> dict:
    if not report_path:
        raise AssertionError("Run document_test_image.py and expose its successful native report first.")
    path = Path(report_path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 64 * 1024:
        raise AssertionError("The native DOCX test report is unavailable.")
    report = json.loads(path.read_bytes())
    if (type(report) is not dict or report.get("scope") != "local_native_docker_only"
            or report.get("cloud_run_isolation_proven") is not False
            or report.get("runtime_source_bytes_verified") is not True
            or report.get("restricted_container_metadata_verified") is not True
            or report.get("platform") != "linux/amd64"
            or report.get("source_sha256") != engine.source_hashes()
            or not engine.IMAGE_ID.fullmatch(report.get("image_id", ""))):
        raise AssertionError("The native DOCX test report does not match this source/image.")
    _, layouts = _complete_corpus(report, engine.clamd_corpus())
    fit = next(row for row in layouts if row["case"] == "styled_multipage_fit")
    identity = fit["layout"]["renderer"]
    image = report["image_id"]
    policy = hashlib.sha256((engine.ROOT / "infra/document-processing/policy.json").read_bytes()).hexdigest()
    if (identity["image_digest"] != "local-document-image@" + image
            or identity["policy_sha256"] != policy):
        raise AssertionError("The observed renderer identity is not joined to the native image/policy.")
    docker = engine.docker_command()
    engine.image_metadata(docker, image, report["platform"], report["source_sha256"])
    code, raw = engine.container(docker, image, report["platform"], ["-c", engine.HASH_PROGRAM], [])
    if code or json.loads(raw) != {key: report["source_sha256"][key] for key in engine.RUNTIME}:
        raise AssertionError("The reused native image runtime bytes differ from this source.")
    return {"docker": docker, "image": image, "platform": report["platform"],
            "source_sha256": report["source_sha256"], "policy": policy, "identity": identity}


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, body: bytes):
        self.body = body

    async def __aiter__(self):
        yield self.body


def configure_native_docx_worker(monkeypatch) -> list[dict]:
    """Keep actual API validation; emulate only delivery to the genuine image."""
    runtime = _runtime(os.environ.get("HIREWIZ_DOCUMENT_TEST_REPORT", ""))
    if engine.source_hashes() != runtime["source_sha256"]:
        raise AssertionError("Native DOCX source changed after test-image verification.")
    # App modules are imported only after the genuine prerequisite, by explicitly
    # scoped runtime tests in CI. No app/package is imported for missing handles.
    from backend.app.services import document_ingestion, docx_artifact

    audience = "https://native-docx-ci.run.app"
    worker_image = "local-document-image@" + runtime["image"]
    monkeypatch.setenv("DOCUMENT_WORKER_URL", audience + "/inspect")
    monkeypatch.setenv("DOCUMENT_WORKER_IMAGE_DIGEST", worker_image)
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", runtime["policy"])
    monkeypatch.setenv("DOCUMENT_WORKER_DOCX_RENDERER_VERSION", runtime["identity"]["renderer_version"])
    monkeypatch.setenv("DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256", runtime["identity"]["font_manifest_sha256"])
    def identity_token(value):
        if value != audience:
            raise AssertionError("Unexpected identity audience")
        return "local-native-ci-transport"

    monkeypatch.setattr(document_ingestion, "_identity_token", identity_token)
    calls: list[dict] = []

    def receive(request: httpx.Request) -> httpx.Response:
        if (request.method != "POST" or str(request.url) != audience + "/validate-docx"
                or request.headers.get("Authorization") != "Bearer local-native-ci-transport"
                or request.headers.get("X-HireWiz-Document-Policy") != runtime["policy"]):
            raise AssertionError("Unexpected local DOCX transport request.")
        payload = json.loads(request.content)
        if payload.get("worker_image") != worker_image or payload.get("policy_sha256") != runtime["policy"]:
            raise AssertionError("The native request differs from its verified local image/policy.")
        with tempfile.TemporaryDirectory(prefix="hirewiz-native-docx-runtime-") as temporary:
            directory = Path(temporary)
            directory.chmod(0o755)
            path = directory / "request.json"
            path.write_bytes(request.content)
            path.chmod(0o444)
            code, raw = engine.container(runtime["docker"], runtime["image"], runtime["platform"],
                                         ["-c", engine.DOCX_LAYOUT_PROGRAM], [(path, "/tmp/request.json")])
        if code not in {0, 1} or not 0 < len(raw) <= (32768 if code == 0 else 4096):
            raise AssertionError("The genuine native DOCX processor did not produce a bounded terminal response.")
        observed = json.loads(raw)
        # The production HTTP handler adds these two verified deployment pins
        # after native processing. The processor deliberately omits them.
        if type(observed) is not dict or {"worker_image", "policy_sha256"} & set(observed):
            raise AssertionError("The native processor returned an unexpected delivery envelope.")
        observed.update(worker_image=worker_image, policy_sha256=runtime["policy"])
        delivered = json.dumps(observed, allow_nan=False).encode()
        if len(delivered) > (32768 if code == 0 else 4096):
            raise AssertionError("The verified native delivery envelope exceeded its response cap.")
        calls.append({"exit_code": code, "reason": observed.get("reason"), "unit_id": observed.get("unit_id")})
        return httpx.Response(200 if code == 0 else 422,
                              headers={"Content-Type": "application/json"}, stream=_Chunks(delivered))

    def client(**kwargs):
        if kwargs.get("trust_env") is not False or kwargs.get("follow_redirects") is not False:
            raise AssertionError("Unexpected native test HTTP client policy.")
        return httpx.AsyncClient(transport=httpx.MockTransport(receive), **kwargs)

    # Do not patch the shared httpx module or actual _worker_http/_check_success.
    monkeypatch.setattr(docx_artifact, "httpx", SimpleNamespace(AsyncClient=client, Timeout=httpx.Timeout))
    return calls
