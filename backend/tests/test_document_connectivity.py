"""Isolated routing/protocol contracts; no provider calls or app imports."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = "PRIVATE_TOKEN_RESPONSE_CUSTOMER_EXCEPTION"


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def diagnostic():
    return load("document_connectivity_test", "infra/gcp/document_connectivity.py")


def test_offline_contract_matches_current_worker_and_network(diagnostic, monkeypatch):
    worker = load("diagnostic_worker_contract", "infra/gcp/document_worker.py")
    network = load("diagnostic_network_contract", "infra/gcp/document_network.py")
    monkeypatch.setattr(diagnostic, "_request", lambda *args, **kwargs: pytest.fail("offline network call"))
    result = diagnostic.plan()
    assert (diagnostic.ORIGIN, diagnostic.IMAGE, diagnostic.POLICY, diagnostic.RENDERER,
            diagnostic.FONTS, diagnostic.IDENTITY) == (
        worker.ACCEPTED_ORIGIN, worker.ACCEPTED_IMAGE, worker.POLICY_SHA256,
        worker.ACCEPTED_RENDERER, worker.ACCEPTED_FONT_MANIFEST, worker.API_INVOKER)
    assert diagnostic.PRIVATE_IPS == frozenset(network.PRIVATE_GOOGLE_IPS)
    assert (result["network"], result["subnet"]) == (network.NETWORK, network.SUBNET)
    assert result["native_calls"] == result["processing_post_retries"] == result["job_task_retries"] == 0
    assert result["native_http_acceptance"] is result["release_acceptance"] is False


@pytest.mark.parametrize("address,family", [("8.8.8.8", 2), ("::1", 10), ("199.36.153.12", 2)])
def test_dns_rejects_public_ipv6_or_adjacent_address(diagnostic, monkeypatch, address, family):
    monkeypatch.setattr(diagnostic.socket, "getaddrinfo", lambda *args: [(family, 1, 6, "", (address, 443))])
    with pytest.raises(diagnostic.ConnectivityDenied):
        diagnostic._private_dns()


def test_private_dns_accepts_only_google_vips(diagnostic, monkeypatch):
    monkeypatch.setattr(diagnostic.socket, "getaddrinfo", lambda *args: [
        (diagnostic.socket.AF_INET, 1, 6, "", (ip, 443)) for ip in diagnostic.PRIVATE_IPS])
    diagnostic._private_dns()


@pytest.mark.parametrize("job,attempt", [("other-job", "0"), ("hirewiz-document-connectivity-20261014", "1")])
def test_identity_refuses_wrong_job_or_retry_before_metadata(diagnostic, monkeypatch, job, attempt):
    monkeypatch.setenv("CLOUD_RUN_JOB", job)
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", attempt)
    monkeypatch.setattr(diagnostic, "_metadata", lambda *args: pytest.fail("metadata should remain untouched"))
    with pytest.raises(diagnostic.ConnectivityDenied):
        diagnostic._identity_token()


def test_identity_requires_exact_project_and_runtime_before_token(diagnostic, monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_JOB", diagnostic.JOB)
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", "0")
    calls = []

    def metadata(path, limit):
        calls.append(path)
        return {"project/project-id": diagnostic.PROJECT,
                "project/numeric-project-id": diagnostic.PROJECT_NUMBER,
                "instance/service-accounts/default/email": "old-runtime@example.test"}.get(path, PRIVATE)

    monkeypatch.setattr(diagnostic, "_metadata", metadata)
    with pytest.raises(diagnostic.ConnectivityDenied):
        diagnostic._identity_token()
    assert len(calls) == 3 and not any("identity?" in path for path in calls)


def test_fixed_audience_and_metadata_destination(diagnostic, monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_JOB", diagnostic.JOB)
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", "0")
    calls = []

    def metadata(path, limit):
        calls.append((path, limit))
        return {"project/project-id": diagnostic.PROJECT,
                "project/numeric-project-id": diagnostic.PROJECT_NUMBER,
                "instance/service-accounts/default/email": diagnostic.IDENTITY}.get(path, PRIVATE)

    monkeypatch.setattr(diagnostic, "_metadata", metadata)
    assert diagnostic._identity_token() == PRIVATE
    assert calls[-1] == ("instance/service-accounts/default/identity?audience="
                         "https%3A%2F%2Fhirewiz-document-inspector-mqff62d75a-uc.a.run.app&format=full", 16384)
    with pytest.raises(diagnostic.ConnectivityDenied):
        diagnostic._request(diagnostic.EXTERNAL_ORIGIN, "/", token=PRIVATE)
    with pytest.raises(diagnostic.ConnectivityDenied):
        diagnostic._request("https://evil.test", "/")


def test_readiness_retries_only_get_then_processing_does_not_retry(diagnostic, monkeypatch):
    monkeypatch.setattr(diagnostic, "_private_dns", lambda **kwargs: None)
    now = [0.0]
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(diagnostic.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs.get("method", "GET"))
        if len(calls) == 1:
            raise RuntimeError(PRIVATE)
        return 404, {"content-type": "application/json"}, b'{"error":"document_inspection_refused"}'

    monkeypatch.setattr(diagnostic, "_request", request)
    assert diagnostic._startup(PRIVATE, seconds=5) == 2 and calls == ["GET", "GET"]
    with pytest.raises(diagnostic.ConnectivityDenied):
        diagnostic._post("/inspect", b"{}", PRIVATE)
    assert calls == ["GET", "GET", "POST"]


def test_readiness_has_finite_deadline(diagnostic, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(diagnostic.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(diagnostic, "_private_dns", lambda **kwargs: (_ for _ in ()).throw(RuntimeError(PRIVATE)))
    with pytest.raises(diagnostic.ConnectivityDenied):
        diagnostic._startup(PRIVATE, seconds=5)
    assert now[0] == 5


def run_setup(diagnostic, monkeypatch):
    monkeypatch.setattr(diagnostic, "_identity_token", lambda: PRIVATE)
    monkeypatch.setattr(diagnostic, "_startup", lambda token: 1)
    monkeypatch.setattr(diagnostic, "_request", lambda *args, **kwargs: (200, {}, b"external"))
    monkeypatch.setattr(diagnostic, "_fixtures", lambda: (b"synthetic source", b"synthetic candidate", [{}]))
    monkeypatch.setattr(diagnostic, "_software", lambda name: SimpleNamespace(
        WorkerConfig=lambda *args: args, _decode_receipt=lambda *args: SimpleNamespace(
            parsed=("Synthetic Routing Check Built scalable systems with Python and SQL.",))))


def test_unknown_post_stops_with_sanitized_partial_outcome(diagnostic, monkeypatch):
    run_setup(diagnostic, monkeypatch)
    calls = []

    def failed_post(path, payload, token):
        calls.append(path)
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(diagnostic, "_post", failed_post)
    result = diagnostic.run()
    assert calls == ["/inspect"] and result["processing_posts_attempted"] == 1
    assert result["stage"] == "inspect" and result["external_https"] is True
    assert result["inspect"] is result["validate_docx"] is result["native_http_acceptance"] is False
    assert PRIVATE not in json.dumps(result)


def test_native_protocol_success_remains_separate_from_release_acceptance(diagnostic, monkeypatch):
    run_setup(diagnostic, monkeypatch)
    calls = []
    monkeypatch.setattr(diagnostic, "_post", lambda path, payload, token: calls.append(path) or b"{}")
    monkeypatch.setattr(diagnostic, "_layout_receipt", lambda *args: None)
    result = diagnostic.run()
    assert calls == ["/inspect", "/validate-docx"] and result["processing_posts_attempted"] == 2
    assert result["native_http_acceptance"] is True and result["release_acceptance"] is False
    assert PRIVATE not in json.dumps(result)


def test_duplicate_or_nonfinite_receipt_never_accepted(diagnostic):
    for value in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]'):
        with pytest.raises(diagnostic.ConnectivityDenied):
            diagnostic._decode(value)


@pytest.mark.parametrize("field", ["worker_image", "policy_sha256", "sha256", "candidate_size_bytes", "scan"])
def test_layout_requires_exact_scan_and_byte_bound_receipt(diagnostic, monkeypatch, field):
    _, binding = diagnostic._layout_payload(b"synthetic source", b"synthetic candidate", [{}])
    scan = {"engine": "ClamAV", "version": "1.4.3", "definitions": "synthetic-only"}
    receipt = {**binding, "worker_image": diagnostic.IMAGE, "policy_sha256": diagnostic.POLICY,
               "scan": scan, "candidate_scan": scan,
               "layout": {"pages": 1, "changed_paragraphs": 1, "protected_glyphs": 10}}
    receipt[field] = PRIVATE
    monkeypatch.setattr(diagnostic, "_software", lambda name: pytest.fail("mismatch must precede validator"))
    with pytest.raises(diagnostic.ConnectivityDenied):
        diagnostic._layout_receipt(diagnostic._canonical(receipt), binding)


def test_layout_passes_accepted_renderer_and_font_pins_to_existing_validator(diagnostic, monkeypatch):
    _, binding = diagnostic._layout_payload(b"synthetic source", b"synthetic candidate", [{}])
    scan = {"engine": "ClamAV", "version": "1.4.3", "definitions": "synthetic-only"}
    receipt = {**binding, "worker_image": diagnostic.IMAGE, "policy_sha256": diagnostic.POLICY,
               "scan": scan, "candidate_scan": scan,
               "layout": {"pages": 1, "changed_paragraphs": 1, "protected_glyphs": 10}}
    supplied = []
    monkeypatch.setattr(diagnostic, "_software", lambda name: SimpleNamespace(
        check_layout_receipt=lambda value, **kwargs: supplied.append(kwargs)))
    diagnostic._layout_receipt(diagnostic._canonical(receipt), binding)
    assert supplied[0]["renderer_version"] == diagnostic.RENDERER
    assert supplied[0]["font_manifest_sha256"] == diagnostic.FONTS
    assert supplied[0]["image"] == diagnostic.IMAGE and supplied[0]["policy"] == diagnostic.POLICY


@pytest.mark.parametrize("field,value", [("pages", 2), ("changed_paragraphs", 2), ("protected_glyphs", 0)])
def test_valid_general_receipt_must_match_fixed_synthetic_layout(diagnostic, monkeypatch, field, value):
    _, binding = diagnostic._layout_payload(b"synthetic source", b"synthetic candidate", [{}])
    scan = {"engine": "ClamAV", "version": "1.4.3", "definitions": "synthetic-only"}
    layout = {"pages": 1, "changed_paragraphs": 1, "protected_glyphs": 10, field: value}
    receipt = {**binding, "worker_image": diagnostic.IMAGE, "policy_sha256": diagnostic.POLICY,
               "scan": scan, "candidate_scan": scan, "layout": layout}
    monkeypatch.setattr(diagnostic, "_software", lambda name: SimpleNamespace(check_layout_receipt=lambda *args, **kwargs: None))
    with pytest.raises(diagnostic.ConnectivityDenied):
        diagnostic._layout_receipt(diagnostic._canonical(receipt), binding)


def test_empty_parser_receipt_does_not_establish_native_acceptance(diagnostic, monkeypatch):
    run_setup(diagnostic, monkeypatch)
    monkeypatch.setattr(diagnostic, "_software", lambda name: SimpleNamespace(
        WorkerConfig=lambda *args: args, _decode_receipt=lambda *args: SimpleNamespace(parsed=("",))))
    calls = []
    monkeypatch.setattr(diagnostic, "_post", lambda path, payload, token: calls.append(path) or b"{}")
    result = diagnostic.run()
    assert calls == ["/inspect"] and result["stage"] == "inspect"
    assert result["inspect"] is result["native_http_acceptance"] is False
