"""Private inspection transport and actual upload admission; no scanner claims."""

from __future__ import annotations

import asyncio
import base64
import copy
import gzip
import hashlib
import json

import httpx
import pytest
from backend.app import models
from backend.app.routers import resume
from backend.app.services import document_ingestion as ingestion
from backend.tests.test_resume_source import _client, _source_bytes

URL = "https://document-worker.test-region.run.app/inspect"
IMAGE = "region-docker.pkg.dev/project/worker/document@sha256:" + "a" * 64
POLICY = "b" * 64
SOURCE = b"synthetic file bytes"


def _response(source=SOURCE, kind="pdf"):
    return {
        "version": 1, "sha256": hashlib.sha256(source).hexdigest(),
        "size_bytes": len(source), "source_format": kind,
        "worker_image": IMAGE, "policy_sha256": POLICY,
        "scan": {"engine": "ClamAV", "version": "1.4.3", "definitions": "12345/frozen-test"},
        "parsed": {
            "raw_text": "Synthetic Candidate\nPython", "sections": {"skills": "Python"},
            "skills": ["Python"], "experience_years": 1.0, "contact_info": {"name": "Synthetic Candidate"},
        },
    }


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setenv("DOCUMENT_WORKER_URL", URL)
    monkeypatch.setenv("DOCUMENT_WORKER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", POLICY)
    tokens, calls, options = [], [], []
    monkeypatch.setattr(ingestion, "_identity_token", lambda audience: tokens.append(audience) or "synthetic-id-token")
    real_client = httpx.AsyncClient

    def install(handler):
        def streaming_handler(request):
            response = handler(request)
            # MockTransport's json/content shortcut eagerly reads its body.
            # Recreate an unread raw stream, matching real HTTP streaming.
            if response.is_stream_consumed:
                return httpx.Response(response.status_code, headers=response.headers,
                                      stream=httpx.ByteStream(response.content))
            return response
        def client(**kwargs):
            options.append(kwargs.copy())
            return real_client(transport=httpx.MockTransport(streaming_handler), **kwargs)
        monkeypatch.setattr(ingestion.httpx, "AsyncClient", client)

    def success(request):
        calls.append(request)
        return httpx.Response(200, json=_response())

    install(success)
    return install, tokens, calls, options


def test_private_worker_binds_exact_bytes_config_and_no_ambient_transport(worker):
    _, tokens, calls, options = worker
    result = ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert result[2] == ["Python"] and result[3] == 1.0
    assert tokens == [URL.removesuffix("/inspect")]
    assert len(calls) == 1 and calls[0].method == "POST" and str(calls[0].url) == URL
    assert calls[0].headers["authorization"] == "Bearer synthetic-id-token"
    assert calls[0].headers["accept-encoding"] == "identity"
    assert calls[0].headers["x-hirewiz-document-policy"] == POLICY
    payload = json.loads(calls[0].content)
    assert set(payload) == {"version", "source_format", "content_base64", "sha256"}
    assert base64.b64decode(payload["content_base64"]) == SOURCE
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert options[0]["verify"].check_hostname and options[0]["verify"].keylog_filename is None


def test_encoded_bomb_is_refused_without_reading_or_decompressing_body(worker):
    reads = []
    class Encoded(httpx.AsyncByteStream):
        async def __aiter__(self):
            reads.append(True)
            yield gzip.compress(b"x" * (20 * 1024 * 1024))
    worker[0](lambda request: httpx.Response(200, stream=Encoded(), headers={
        "content-type": "application/json", "content-encoding": "gzip",
    }))
    with pytest.raises(ingestion.DocumentInspectionError):
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert not reads


def test_total_deadline_closes_slow_response_before_next_byte(worker, monkeypatch):
    closed = []
    monkeypatch.setattr(ingestion, "HTTP_DEADLINE_SECONDS", 0.01)
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1)
            pytest.fail("A slow body must be cancelled before another byte arrives")
            yield b"never"
        async def aclose(self):
            closed.append(True)
    worker[0](lambda request: httpx.Response(200, stream=Slow(), headers={"content-type": "application/json"}))
    with pytest.raises(ingestion.DocumentInspectionError):
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert closed == [True]


def test_explicit_tls_context_never_creates_ambient_keylog(monkeypatch, tmp_path):
    keylog = tmp_path / "ambient-keylog"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    context = ingestion._tls_context()
    assert context.keylog_filename is None and context.check_hostname and not keylog.exists()


def test_identity_authentication_uses_explicit_trust_and_bounded_request(monkeypatch, tmp_path):
    import requests
    from google.oauth2 import id_token

    keylog = tmp_path / "identity-keylog"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setenv("HTTPS_PROXY", "https://untrusted.example")
    observed = []
    def fetch(request, audience):
        assert audience == "https://document.run.app"
        request("https://oauth2.googleapis.com/test", timeout=120)
        return "synthetic-token"
    def send(session, method, url, **kwargs):
        assert session.trust_env is False and session.verify == ingestion.certifi.where()
        context = session.get_adapter(url).poolmanager.connection_pool_kw["ssl_context"]
        assert context.check_hostname and context.keylog_filename is None
        observed.append((method, url, kwargs["timeout"]))
        return httpx.Response(200, content=b"synthetic-auth")
    monkeypatch.setattr(id_token, "fetch_id_token", fetch)
    monkeypatch.setattr(requests.Session, "request", send)
    assert ingestion._identity_token("https://document.run.app") == "synthetic-token"
    assert observed == [("GET", "https://oauth2.googleapis.com/test", 5)] and not keylog.exists()


@pytest.mark.parametrize("url", [
    "http://document.run.app/inspect", "https://example.com/inspect",
    "https://user:secret@document.run.app/inspect", "https://document.run.app:443/inspect",
    "https://document.run.app/inspect?file=secret", "https://document.run.app/inspect#secret",
    "https://document.run.app/other", "https://document.run.app:broken/inspect", "",
])
def test_invalid_config_refuses_before_identity_or_file_disclosure(worker, monkeypatch, url):
    monkeypatch.setenv("DOCUMENT_WORKER_URL", url)
    with pytest.raises(ingestion.DocumentInspectionError) as rejected:
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert rejected.value.status_code == 503 and not worker[1] and not worker[2]


@pytest.mark.parametrize("source,kind", [(b"", "pdf"), (b"a", "exe"), (b"a" * (ingestion.MAX_SOURCE_BYTES + 1), "pdf")])
def test_bad_size_and_format_refuse_before_authentication(worker, source, kind):
    with pytest.raises(ingestion.DocumentInspectionError) as rejected:
        ingestion.inspect_resume_document(source, source_format=kind)
    assert rejected.value.status_code == 422 and not worker[1]


@pytest.mark.parametrize("field,value", [
    ("sha256", "c" * 64), ("size_bytes", 1), ("source_format", "docx"),
    ("worker_image", IMAGE.replace("a" * 64, "c" * 64)), ("policy_sha256", "c" * 64),
    ("version", True), ("scan", {"engine": "unknown", "version": "1", "definitions": "1"}),
    ("scan", {"engine": "ClamAV", "version": "1", "definitions": ""}),
    ("scan", {"engine": "ClamAV", "version": "secret\ntrace", "definitions": "1"}),
])
def test_unbound_or_unscanned_results_are_not_accepted(worker, field, value):
    data = _response()
    data[field] = value
    worker[0](lambda request: httpx.Response(200, json=data))
    with pytest.raises(ingestion.DocumentInspectionError) as rejected:
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert rejected.value.status_code == 503


@pytest.mark.parametrize("field,value", [
    ("raw_text", "x" * (ingestion.MAX_TEXT_BYTES + 1)), ("sections", {"skills": []}),
    ("skills", [None]), ("experience_years", True), ("experience_years", -1),
    ("contact_info", {"credential": "must not be accepted"}),
])
def test_invalid_extraction_is_not_saved(worker, field, value):
    data = copy.deepcopy(_response())
    data["parsed"][field] = value
    worker[0](lambda request: httpx.Response(200, json=data))
    with pytest.raises(ingestion.DocumentInspectionError):
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")


@pytest.mark.parametrize("status,expected", [(422, 503), (302, 503), (401, 503), (500, 503), (503, 503)])
def test_error_bodies_are_not_logged_exposed_or_retried(worker, status, expected):
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(status, text="private token and candidate data", headers={"location": "https://other.example"})
    worker[0](response)
    with pytest.raises(ingestion.DocumentInspectionError) as rejected:
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert rejected.value.status_code == expected and len(calls) == 1
    assert "private" not in str(rejected.value) and "token" not in str(rejected.value)


def test_oversized_response_and_raw_transport_failure_are_fixed_errors(worker):
    worker[0](lambda request: httpx.Response(200, content=b"x" * (ingestion.MAX_RESPONSE_BYTES + 1), headers={"content-type": "application/json"}))
    with pytest.raises(ingestion.DocumentInspectionError):
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    def failure(request):
        raise RuntimeError("private candidate and secret token")
    worker[0](failure)
    with pytest.raises(ingestion.DocumentInspectionError) as rejected:
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert "private" not in str(rejected.value) and rejected.value.__suppress_context__


@pytest.mark.parametrize("refused", [True, False])
def test_actual_upload_safety_failure_creates_no_resume_or_analysis_hold(monkeypatch, refused):
    engine, factory, client = _client()
    def failure(*args, **kwargs):
        raise ingestion.DocumentInspectionError(refused=refused)
    monkeypatch.setattr(resume, "inspect_resume_document", failure)
    monkeypatch.setattr(resume, "parse_resume_file", lambda *a, **k: pytest.fail("API host must not parse PDF/DOCX"))
    monkeypatch.setattr(resume, "enrich_resume_skills", lambda *a, **k: pytest.fail("Safety refusal must precede AI"))
    try:
        response = client.post("/api/resume/parse", files={"file": ("source.pdf", _source_bytes("pdf"), "application/pdf")}, data={"enrich_skills": "true"})
        assert response.status_code == (422 if refused else 503)
        with factory() as db:
            assert db.query(models.Resume).count() == 2
            assert db.query(models.AnalysisRun).count() == db.query(models.UsageEvent).count() == 0
            assert db.get(models.User, 1).ai_credits == 50
    finally:
        client.close()
        engine.dispose()


def test_actual_pdf_upload_does_not_open_document_inside_api(monkeypatch):
    engine, factory, client = _client()
    original = _source_bytes("pdf")
    inspected = []
    def inspect(source, *, source_format):
        inspected.append((source, source_format))
        return "Synthetic text", {}, [], 0.0, {}
    monkeypatch.setattr(resume, "inspect_resume_document", inspect)
    monkeypatch.setattr(resume, "parse_resume_file", lambda *a, **k: pytest.fail("Host parsing forbidden"))
    monkeypatch.setattr(resume, "_validated_resume_upload", lambda *a, **k: pytest.fail("Host PDF/ZIP validation forbidden"))
    try:
        response = client.post("/api/resume/parse", files={"file": ("source.pdf", original, "application/pdf")})
        assert response.status_code == 200, response.text
        assert inspected == [(original, "pdf")]
        with factory() as db:
            assert db.get(models.Resume, response.json()["resume_id"]).source_document == original
    finally:
        client.close()
        engine.dispose()


# Typed refusals are meaningful only when they bind this exact invocation.
def _refusal_response(**changes):
    value = {
        "version": 1,
        "sha256": hashlib.sha256(SOURCE).hexdigest(),
        "size_bytes": len(SOURCE),
        "source_format": "pdf",
        "worker_image": IMAGE,
        "policy_sha256": POLICY,
        "error": "document_inspection_refused",
    }
    value.update(changes)
    return value


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("sha256", "c" * 64),
        ("size_bytes", 1),
        ("source_format", "docx"),
        ("worker_image", IMAGE.replace("a" * 64, "c" * 64)),
        ("policy_sha256", "c" * 64),
        ("error", "document_inspection_unavailable"),
        ("trace", "private-candidate-marker"),
    ],
)
def test_unbound_typed_refusal_is_unavailable(worker, field, value):
    worker[0](lambda request: httpx.Response(422, json=_refusal_response(**{field: value})))
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert caught.value.status_code == 503
    assert "private-candidate-marker" not in str(caught.value)


def test_valid_bound_typed_refusal_is_permanent(worker):
    worker[0](lambda request: httpx.Response(422, json=_refusal_response()))
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert caught.value.status_code == 422


@pytest.mark.parametrize("variant", ["plain", "duplicate", "oversize", "encoded"])
def test_untrusted_422_protocol_never_becomes_permanent(worker, variant):
    raw = json.dumps(_refusal_response()).encode()
    headers = {"content-type": "application/json"}
    if variant == "plain":
        raw = b"private-marker"
    if variant == "duplicate":
        raw = raw[:-1] + b',"version":1}'
    if variant == "oversize":
        raw = b" " * 4097 + raw
    if variant == "encoded":
        raw = gzip.compress(raw)
        headers["content-encoding"] = "gzip"
    worker[0](lambda request: httpx.Response(422, content=raw, headers=headers))
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert caught.value.status_code == 503 and "private-marker" not in str(caught.value)


@pytest.mark.parametrize("nested", [False, True])
def test_success_duplicate_keys_are_ambiguous_and_unavailable(worker, nested):
    raw = json.dumps(_response()).encode()
    if nested:
        raw = raw.replace(
            b'"name": "Synthetic Candidate"',
            b'"name": "Synthetic Candidate", "name": "Synthetic Candidate"',
        )
    else:
        raw = raw[:-1] + b',"version":1}'
    worker[0](
        lambda request: httpx.Response(
            200, content=raw, headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert caught.value.status_code == 503


def test_refusal_body_has_same_absolute_deadline_and_closes_stream(worker, monkeypatch):
    closed = []
    monkeypatch.setattr(ingestion, "HTTP_DEADLINE_SECONDS", 0.01)

    class SlowRefusal(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1)
            pytest.fail("No byte may be read after the absolute refusal deadline")
            yield b"never"

        async def aclose(self):
            closed.append(True)

    worker[0](
        lambda request: httpx.Response(
            422, stream=SlowRefusal(), headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        ingestion.inspect_resume_document(SOURCE, source_format="pdf")
    assert caught.value.status_code == 503 and closed == [True]


@pytest.mark.parametrize("with_receipt", [False, True])
def test_inspection_receipt_and_tuple_each_make_one_validated_request(worker, with_receipt):
    from dataclasses import FrozenInstanceError

    method = (
        ingestion.inspect_resume_document_with_receipt
        if with_receipt else ingestion.inspect_resume_document
    )
    result = method(SOURCE, source_format="pdf")
    expected = ("Synthetic Candidate\nPython", {"skills": "Python"}, ["Python"], 1.0,
                {"name": "Synthetic Candidate"})
    if with_receipt:
        assert isinstance(result, ingestion.InspectedResume) and result.parsed == expected
        assert result.sha256 == hashlib.sha256(SOURCE).hexdigest()
        assert result.size_bytes == len(SOURCE) and result.source_format == "pdf"
        assert result.worker_image == IMAGE and result.policy_sha256 == POLICY
        assert (result.scan_engine, result.scan_version, result.scan_definitions) == (
            "ClamAV", "1.4.3", "12345/frozen-test"
        )
        assert "Synthetic Candidate" not in repr(result)
        with pytest.raises(FrozenInstanceError):
            result.sha256 = "c" * 64
    else:
        assert type(result) is tuple and result == expected
    assert worker[1] == [URL.removesuffix("/inspect")]
    assert len(worker[2]) == len(worker[3]) == 1
    assert base64.b64decode(json.loads(worker[2][0].content)["content_base64"]) == SOURCE


@pytest.mark.parametrize("with_receipt", [False, True])
@pytest.mark.parametrize("scope,variant", [
    ("top", "extra"), ("top", "missing"), ("scan", "extra"), ("parsed", "extra"),
    ("scan", "duplicate"), ("parsed", "duplicate"),
    ("scan", "missing"), ("parsed", "missing"),
])
def test_receipt_requires_exact_duplicate_free_success_contract(worker, with_receipt, scope, variant):
    data = _response()
    node = data if scope == "top" else data[scope]
    if variant == "extra":
        node["private_untrusted_field"] = "candidate-private-marker"
    elif variant == "missing":
        node.pop("worker_image" if scope == "top" else "version" if scope == "scan" else "skills")
    raw = json.dumps(data).encode()
    if variant == "duplicate":
        marker = b'"engine": "ClamAV"' if scope == "scan" else b'"skills": ["Python"]'
        raw = raw.replace(marker, marker + b", " + marker)
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})
    worker[0](response)
    method = (ingestion.inspect_resume_document_with_receipt
              if with_receipt else ingestion.inspect_resume_document)
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        method(SOURCE, source_format="pdf")
    assert caught.value.status_code == 503 and len(calls) == 1
    assert "candidate-private-marker" not in str(caught.value)


@pytest.mark.parametrize("with_receipt", [False, True])
@pytest.mark.parametrize("field,value", [
    ("sha256", "c" * 64), ("size_bytes", len(SOURCE) + 1), ("source_format", "docx"),
    ("worker_image", IMAGE.replace("a" * 64, "c" * 64)), ("policy_sha256", "c" * 64),
    ("version", True),
    ("scan", {"engine": "unknown", "version": "1.4.3", "definitions": "fresh"}),
    ("scan", {"engine": "ClamAV", "version": "private\nmarker", "definitions": "fresh"}),
    ("scan", {"engine": "ClamAV", "version": "1.4.3", "definitions": ""}),
])
def test_no_receipt_or_tuple_from_unbound_or_unscanned_success(worker, with_receipt, field, value):
    data = _response()
    data[field] = value
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, json=data)
    worker[0](response)
    method = (ingestion.inspect_resume_document_with_receipt
              if with_receipt else ingestion.inspect_resume_document)
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        method(SOURCE, source_format="pdf")
    assert caught.value.status_code == 503 and len(calls) == 1
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("with_receipt", [False, True])
@pytest.mark.parametrize("status,bound,expected", [(422, True, 422), (422, False, 503), (503, True, 503)])
def test_receipt_never_exists_for_refusal_or_unavailable(worker, with_receipt, status, bound, expected):
    calls = []
    body = _refusal_response() if bound else {"error": "candidate-private-marker"}
    def response(request):
        calls.append(request)
        return httpx.Response(status, json=body)
    worker[0](response)
    method = (ingestion.inspect_resume_document_with_receipt
              if with_receipt else ingestion.inspect_resume_document)
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        method(SOURCE, source_format="pdf")
    assert caught.value.status_code == expected and len(calls) == 1
    assert "candidate-private-marker" not in str(caught.value)


def test_receipt_cannot_escape_absolute_deadline(worker, monkeypatch):
    closed = []
    monkeypatch.setattr(ingestion, "HTTP_DEADLINE_SECONDS", 0.01)
    class Delayed(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1)
            pytest.fail("Receipt must not outlive worker request deadline")
            yield b"never"
        async def aclose(self):
            closed.append(True)
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, stream=Delayed(), headers={"content-type": "application/json"})
    worker[0](response)
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        ingestion.inspect_resume_document_with_receipt(SOURCE, source_format="pdf")
    assert caught.value.status_code == 503 and closed == [True] and len(calls) == 1


@pytest.mark.parametrize("variant", ["empty", "invalid_config"])
def test_receipt_refuses_before_authentication_and_disclosure(worker, monkeypatch, variant):
    source = SOURCE
    if variant == "empty":
        source = b""
    else:
        monkeypatch.setenv("DOCUMENT_WORKER_URL", "https://untrusted.example/inspect")
    with pytest.raises(ingestion.DocumentInspectionError) as caught:
        ingestion.inspect_resume_document_with_receipt(source, source_format="pdf")
    assert caught.value.status_code == (422 if variant == "empty" else 503)
    assert not worker[1] and not worker[2] and not worker[3]
