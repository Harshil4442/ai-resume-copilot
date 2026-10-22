"""Synthetic private transports exercise exact native scan/compile composition."""
from __future__ import annotations

import base64
import json
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest
from backend.app.services import document_ingestion, native_tex, native_tex_transport, parsing
from backend.app.services.document_ingestion import DocumentInspectionError, InspectedResume
from backend.app.services.resume_layout import ResumeLayoutError, ResumeProcessingUnavailable

SOURCE = b"\\documentclass{article}\\begin{document}\nBuilt Python reports.\n\\end{document}"
PDF = b"%PDF-synthetic-private-worker-output"
IMAGE = "reviewed/native-tex@sha256:" + "1" * 64
POLICY = "2" * 64
WORKER_IMAGE = "reviewed/document@sha256:" + "3" * 64
WORKER_POLICY = "4" * 64
PARSED = ("Private parsed facts", {"skills": "Python"}, ["Python"], 1.0, {"name": None})


def receipt(data, kind):
    return InspectedResume(
        parsed=PARSED if kind == "pdf" else ("", {}, [], 0.0, {}),
        sha256=native_tex.digest(data), size_bytes=len(data), source_format=kind,
        worker_image=WORKER_IMAGE, policy_sha256=WORKER_POLICY,
        scan_engine="ClamAV", scan_version="synthetic-v1", scan_definitions="synthetic-fixture",
    )


@pytest.fixture
def composition(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DOCUMENT_WORKER_URL", "https://document.run.app/inspect")
    monkeypatch.setenv("DOCUMENT_WORKER_IMAGE_DIGEST", WORKER_IMAGE)
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", WORKER_POLICY)
    monkeypatch.setenv("NATIVE_TEX_COMPILER_URL", "https://native-tex.run.app/compile")
    monkeypatch.setenv("NATIVE_TEX_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("NATIVE_TEX_ISOLATION_POLICY_SHA256", POLICY)
    events = []

    def inspect(data, *, source_format):
        events.append(("inspect", source_format, data))
        return receipt(data, source_format)

    def compile(payload, *, url, image, policy):
        events.append(("compile", payload))
        return {"protocol": "hirewiz-tex-v1", "pdf": base64.b64encode(PDF).decode(),
                "sha256": native_tex.digest(PDF), "size": len(PDF),
                "renderer": "pdftex-no-shell-escape-v1", "request_sha256": native_tex.digest(payload),
                "renderer_image": image, "isolation_policy_sha256": policy}

    monkeypatch.setattr(document_ingestion, "inspect_resume_document_with_receipt", inspect)
    monkeypatch.setattr(native_tex_transport, "remote_compile", compile)
    return events, inspect, compile


def test_source_then_compiler_then_exact_pdf_are_inspected_once(composition):
    events, _, _ = composition
    result = native_tex.compile_project_inspected(SOURCE, "tex")
    assert [event[:2] if event[0] == "inspect" else event[:1] for event in events] == [
        ("inspect", "tex"), ("compile",), ("inspect", "pdf")]
    assert events[0][2] == SOURCE and events[2][2] == PDF
    assert result.pdf == PDF and result.renderer_image == IMAGE
    assert result.renderer_policy_sha256 == POLICY
    assert result.source_inspection.sha256 == native_tex.digest(SOURCE)
    assert result.pdf_inspection.sha256 == native_tex.digest(PDF)
    assert result.pdf_inspection.parsed == PARSED
    assert "Private parsed facts" not in repr(result) and PDF.decode() not in repr(result)


def test_tuple_wrapper_preserves_api_without_duplicate_scans(composition):
    assert native_tex.compile_project(SOURCE, "tex") == (PDF, IMAGE)
    assert len(composition[0]) == 3


@pytest.mark.parametrize("refused", [True, False])
def test_original_refusal_or_outage_prevents_project_parsing_and_compilation(composition, monkeypatch, refused):
    def reject(*args, **kwargs):
        raise DocumentInspectionError(refused=refused)
    monkeypatch.setattr(document_ingestion, "inspect_resume_document_with_receipt", reject)
    monkeypatch.setattr(native_tex, "read_project", lambda *args: pytest.fail("uninspected source parsed"))
    with pytest.raises(ResumeLayoutError) as failure:
        native_tex.compile_project_inspected(SOURCE, "tex")
    assert isinstance(failure.value, ResumeProcessingUnavailable) is not refused
    assert composition[0] == []


def test_pdf_refusal_prevents_layout_and_sealing(composition, monkeypatch):
    def inspect(data, *, source_format):
        if source_format == "pdf":
            raise DocumentInspectionError(refused=True)
        return receipt(data, source_format)
    monkeypatch.setattr(document_ingestion, "inspect_resume_document_with_receipt", inspect)
    monkeypatch.setattr(native_tex, "validate_layout", lambda *args: pytest.fail("uninspected PDF parsed"))
    with pytest.raises(ResumeLayoutError, match="private resume safety") as failure:
        native_tex.prepare_artifact(SOURCE, "tex", [])
    assert not isinstance(failure.value, ResumeProcessingUnavailable)


@pytest.mark.parametrize("kind", ["tex", "pdf"])
@pytest.mark.parametrize("changed", [
    {"sha256": "f" * 64}, {"size_bytes": 1}, {"source_format": "docx"},
    {"worker_image": "other@sha256:" + "3" * 64}, {"policy_sha256": "f" * 64},
    {"scan_engine": "other"}, {"parsed": None}, {"parsed": (1, {}, [], 0.0, {})},
])
def test_wrong_receipt_metadata_or_parsed_shape_is_unavailable_before_release(composition, monkeypatch, kind, changed):
    def inspect(data, *, source_format):
        result = receipt(data, source_format)
        return replace(result, **changed) if source_format == kind else result
    monkeypatch.setattr(document_ingestion, "inspect_resume_document_with_receipt", inspect)
    with pytest.raises(ResumeProcessingUnavailable, match="receipt could not be verified"):
        native_tex.compile_project_inspected(SOURCE, "tex")
    if kind == "tex":
        assert not composition[0]


def test_native_source_receipt_cannot_supply_candidate_facts(composition, monkeypatch):
    monkeypatch.setattr(document_ingestion, "inspect_resume_document_with_receipt",
                        lambda data, *, source_format: replace(receipt(data, source_format), parsed=PARSED))
    with pytest.raises(ResumeProcessingUnavailable):
        native_tex.compile_project_inspected(SOURCE, "tex")
    assert composition[0] == []


def test_wrong_compiler_image_or_policy_cannot_be_sealed(composition, monkeypatch):
    def compile(*args, **kwargs):
        return {**composition[2](*args, **kwargs), "isolation_policy_sha256": "f" * 64}
    monkeypatch.setattr(native_tex_transport, "remote_compile", compile)
    with pytest.raises(ResumeProcessingUnavailable, match="invalid artifact"):
        native_tex.prepare_artifact(SOURCE, "tex", [])


def test_oversized_compiled_pdf_refuses_before_private_pdf_request(composition, monkeypatch):
    def compile(*args, **kwargs):
        large = b"%PDF-" + b"x" * document_ingestion.MAX_SOURCE_BYTES
        return {**composition[2](*args, **kwargs), "pdf": base64.b64encode(large).decode(),
                "sha256": native_tex.digest(large), "size": len(large)}
    monkeypatch.setattr(native_tex_transport, "remote_compile", compile)
    with pytest.raises(ResumeLayoutError, match="5 MB private safety-check limit"):
        native_tex.compile_project_inspected(SOURCE, "tex")
    assert len(composition[0]) == 2


def test_native_parsing_uses_private_pdf_facts_without_application_pdf_parser(composition, monkeypatch):
    monkeypatch.setattr(parsing, "extract_text_and_sections_from_pdf", lambda *args: pytest.fail("API PDF parsing"))
    monkeypatch.setattr(parsing, "enrich_resume_skills", lambda *args: pytest.fail("unexpected model call"))
    assert parsing.parse_resume_file(SOURCE, "source.tex", use_llm=True) == PARSED


def test_unchanged_snapshot_reuses_inspected_original_without_another_scan(composition, monkeypatch):
    def validate(before, after, units, replacements):
        assert before == after == PDF and composition[0][-1] == ("inspect", "pdf", PDF)
        return {"changed_spans": 0}
    monkeypatch.setattr(native_tex, "validate_layout", validate)
    seal = native_tex.prepare_artifact(SOURCE, "tex", [])
    assert seal["classification"] == "unchanged_source_snapshot"
    assert base64.b64decode(seal["native_base64"]) == SOURCE
    assert base64.b64decode(seal["pdf_base64"]) == PDF
    assert seal["renderer_policy_sha256"] == POLICY
    assert seal["inspection_version"] == 1
    assert seal["inspections"]["source"] == seal["inspections"]["candidate"]
    assert seal["inspections"]["source_pdf"] == seal["inspections"]["candidate_pdf"]
    assert len(composition[0]) == 3


@pytest.fixture
def changed_snapshot(composition, monkeypatch):
    from backend.app.services.resume_layout import _candidate

    data = SOURCE.replace(b"Built Python reports.", b"Built Python services for customer reporting.")
    unit = native_tex.source_units(data, "tex", _candidate)[0]
    edits = [{"unit_id": unit["unit_id"], "original_text": unit["text"],
              "replacement_text": unit["text"].replace("Built", "Developed"), "evidence_ids": []}]
    candidate_pdf = b"%PDF-synthetic-inspected-candidate-output"
    calls = 0

    def compile(*args, **kwargs):
        nonlocal calls
        response = composition[2](*args, **kwargs)
        calls += 1
        pdf = PDF if calls == 1 else candidate_pdf
        return {**response, "pdf": base64.b64encode(pdf).decode(), "sha256": native_tex.digest(pdf), "size": len(pdf)}

    def validate(before, after, units, replacements):
        assert before == PDF and after == candidate_pdf
        assert composition[0][-1] == ("inspect", "pdf", after)
        return {"changed_spans": 1}

    monkeypatch.setattr(native_tex_transport, "remote_compile", compile)
    monkeypatch.setattr(native_tex, "validate_layout", validate)
    seal = native_tex.prepare_artifact(data, "tex", edits)
    return data, edits, seal, composition[0], candidate_pdf


def test_changed_snapshot_retains_exact_metadata_only_source_and_pdf_receipts(changed_snapshot, monkeypatch):
    data, edits, seal, events, candidate_pdf = changed_snapshot
    candidate = data.replace(b"Built", b"Developed")
    assert seal["inspection_version"] == 1 and seal["classification"] == "tailored"
    assert [event[0] for event in events] == ["inspect", "compile", "inspect"] * 2
    assert events[0] == ("inspect", "tex", data) and events[3] == ("inspect", "tex", candidate)
    for name, content, kind in (("source", data, "tex"), ("source_pdf", PDF, "pdf"),
                                ("candidate", candidate, "tex"), ("candidate_pdf", candidate_pdf, "pdf")):
        assert seal["inspections"][name] == {
            "sha256": native_tex.digest(content), "size_bytes": len(content), "source_format": kind,
            "worker_image": WORKER_IMAGE, "policy_sha256": WORKER_POLICY,
            "scan_engine": "ClamAV", "scan_version": "synthetic-v1", "scan_definitions": "synthetic-fixture"}
    assert "Private parsed facts" not in json.dumps(seal) and "parsed" not in json.dumps(seal)
    monkeypatch.setattr(native_tex, "compile_project_inspected", lambda *a: pytest.fail("sealed bytes regenerated"))
    assert native_tex.sealed_bytes(seal, data, "tex", edits, "pdf") == candidate_pdf
    assert native_tex.sealed_bytes(seal, data, "tex", edits, "tex") == candidate


@pytest.mark.parametrize("path,value", [
    (("inspection_version",), True), (("inspection_version",), None), (("inspection_version",), 2),
    (("inspections",), {}), (("renderer_policy_sha256",), "mutable-policy"),
    (("renderer_image",), "native-tex:latest"), (("size",), 1),
    (("inspections", "source", "sha256"), "f" * 64),
    (("inspections", "candidate", "size_bytes"), 1),
    (("inspections", "candidate", "source_format"), "pdf"),
    (("inspections", "source_pdf", "size_bytes"), True),
    (("inspections", "candidate_pdf", "sha256"), "f" * 64),
    (("inspections", "candidate_pdf", "size_bytes"), 1),
    (("inspections", "candidate_pdf", "worker_image"), "other@sha256:" + "5" * 64),
    (("inspections", "candidate_pdf", "policy_sha256"), "5" * 64),
    (("inspections", "candidate_pdf", "scan_engine"), "unverified"),
    (("inspections", "candidate_pdf", "scan_version"), "invalid\nversion"),
    (("inspections", "candidate_pdf", "scan_definitions"), ""),
    (("inspections", "candidate_pdf", "parsed"), {}),
])
def test_declared_inspection_seal_rejects_malformed_or_unbound_metadata(changed_snapshot, path, value):
    data, edits, seal, _, _ = changed_snapshot
    tampered = deepcopy(seal)
    parent = tampered
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(ResumeLayoutError, match="integrity verification"):
        native_tex.sealed_bytes(tampered, data, "tex", edits, "pdf")


@pytest.mark.parametrize("changed,requested", [("pdf", "tex"), ("native", "pdf")])
def test_each_download_checks_the_other_output_against_its_inspection_receipt(changed_snapshot, changed, requested):
    data, edits, seal, _, _ = changed_snapshot
    tampered = deepcopy(seal)
    content = b"different-output"
    tampered[changed + "_base64"] = base64.b64encode(content).decode()
    tampered[changed + "_sha256"] = native_tex.digest(content)
    if changed == "pdf":
        tampered["size"] = len(content)
    with pytest.raises(ResumeLayoutError, match="integrity verification"):
        native_tex.sealed_bytes(tampered, data, "tex", edits, requested)


@pytest.mark.parametrize("changed", [{"renderer_image": "other@sha256:" + "6" * 64},
                                    {"renderer_policy_sha256": "6" * 64}])
def test_renderer_change_between_original_and_candidate_cannot_reach_layout(composition, monkeypatch, changed):
    from backend.app.services.resume_layout import _candidate

    data = SOURCE.replace(b"Built Python reports.", b"Built Python services for customer reporting.")
    unit = native_tex.source_units(data, "tex", _candidate)[0]
    edits = [{"unit_id": unit["unit_id"], "original_text": unit["text"],
              "replacement_text": unit["text"].replace("Built", "Developed"), "evidence_ids": []}]
    compile = native_tex.compile_project_inspected
    calls = 0
    def inspected(*args):
        nonlocal calls
        calls += 1
        result = compile(*args)
        return replace(result, **changed) if calls == 2 else result
    monkeypatch.setattr(native_tex, "compile_project_inspected", inspected)
    monkeypatch.setattr(native_tex, "validate_layout", lambda *a: pytest.fail("renderer drift reached layout"))
    with pytest.raises(ResumeProcessingUnavailable, match="renderer changed"):
        native_tex.prepare_artifact(data, "tex", edits)
    assert calls == 2


def test_changed_candidate_pdf_refusal_prevents_layout_and_seal(composition, monkeypatch):
    from backend.app.services.resume_layout import _candidate

    data = SOURCE.replace(b"Built Python reports.", b"Built Python services for customer reporting.")
    unit = native_tex.source_units(data, "tex", _candidate)[0]
    edits = [{"unit_id": unit["unit_id"], "original_text": unit["text"],
              "replacement_text": unit["text"].replace("Built", "Developed"), "evidence_ids": []}]
    pdf_calls = 0
    def inspect(content, *, source_format):
        nonlocal pdf_calls
        if source_format == "pdf":
            pdf_calls += 1
            if pdf_calls == 2:
                raise DocumentInspectionError(refused=True)
        return composition[1](content, source_format=source_format)
    monkeypatch.setattr(document_ingestion, "inspect_resume_document_with_receipt", inspect)
    monkeypatch.setattr(native_tex, "validate_layout", lambda *a: pytest.fail("refused candidate PDF reached layout"))
    with pytest.raises(ResumeLayoutError, match="private resume safety") as failure:
        native_tex.prepare_artifact(data, "tex", edits)
    assert not isinstance(failure.value, ResumeProcessingUnavailable) and pdf_calls == 2


def test_inspector_pin_drift_between_compilations_cannot_reach_layout(composition, monkeypatch):
    from backend.app.services.resume_layout import _candidate

    data = SOURCE.replace(b"Built Python reports.", b"Built Python services for customer reporting.")
    unit = native_tex.source_units(data, "tex", _candidate)[0]
    edits = [{"unit_id": unit["unit_id"], "original_text": unit["text"],
              "replacement_text": unit["text"].replace("Built", "Developed"), "evidence_ids": []}]
    compile = native_tex.compile_project_inspected
    calls = 0
    def inspected(*args):
        nonlocal calls
        calls += 1
        result = compile(*args)
        if calls == 2:
            return replace(result,
                source_inspection=replace(result.source_inspection, policy_sha256="6" * 64),
                pdf_inspection=replace(result.pdf_inspection, policy_sha256="6" * 64))
        return result
    monkeypatch.setattr(native_tex, "compile_project_inspected", inspected)
    monkeypatch.setattr(native_tex, "validate_layout", lambda *a: pytest.fail("inspector drift reached layout"))
    with pytest.raises(ResumeProcessingUnavailable, match="receipts could not be verified"):
        native_tex.prepare_artifact(data, "tex", edits)
    assert calls == 2


def test_new_seal_retrieval_uses_retained_pins_after_configuration_rotation(changed_snapshot, monkeypatch):
    data, edits, seal, _, candidate_pdf = changed_snapshot
    monkeypatch.setattr(document_ingestion, "worker_config", lambda: pytest.fail("new seal checked against current policy"))
    monkeypatch.setenv("NATIVE_TEX_ISOLATION_POLICY_SHA256", "7" * 64)
    assert native_tex.sealed_bytes(seal, data, "tex", edits, "pdf") == candidate_pdf


def test_historical_v2_retrieval_keeps_existing_policy_without_new_receipts(monkeypatch):
    historical = {"validator": native_tex.VALIDATOR, "source_sha256": native_tex.digest(SOURCE),
        "edits_sha256": native_tex.digest(b"[]"), "renderer_image": "historical-renderer",
        "pdf_base64": base64.b64encode(PDF).decode(), "pdf_sha256": native_tex.digest(PDF),
        "native_base64": base64.b64encode(SOURCE).decode(), "native_sha256": native_tex.digest(SOURCE)}
    monkeypatch.setenv("DOCUMENT_WORKER_IMAGE_DIGEST", "rotated-current-policy")
    monkeypatch.setattr(native_tex, "compile_project_inspected", lambda *a: pytest.fail("historical artifact regenerated"))
    monkeypatch.setattr(document_ingestion, "worker_config", lambda: pytest.fail("historical artifact checked against current policy"))
    assert native_tex.sealed_bytes(historical, SOURCE, "tex", [], "pdf") == PDF
    assert native_tex.sealed_bytes(historical, SOURCE, "tex", [], "tex") == SOURCE


@pytest.mark.parametrize("status,body,unavailable", [
    (422, {"error": "compile_refused"}, False),
    (422, {"error": "compiler_unavailable"}, True),
    (503, {"error": "compiler_unavailable"}, True),
    (500, {"error": "compile_refused"}, True),
    (302, {}, True),
])
def test_compile_transport_distinguishes_verified_refusal_from_outage(status, body, unavailable, monkeypatch):
    from google.oauth2 import id_token

    monkeypatch.setattr(id_token, "fetch_id_token", lambda *args: "synthetic-token")
    client = httpx.Client
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, json=body)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(respond)))
    with pytest.raises(ResumeLayoutError) as failure:
        native_tex_transport.remote_compile(b"synthetic-source", url="https://native-tex.run.app/compile", image=IMAGE, policy=POLICY)
    assert isinstance(failure.value, ResumeProcessingUnavailable) is unavailable
    assert len(calls) == 1


def test_invalid_compiler_configuration_is_retryable_before_identity_disclosure(monkeypatch):
    from google.oauth2 import id_token

    monkeypatch.setattr(id_token, "fetch_id_token", lambda *args: pytest.fail("invalid config disclosed source"))
    with pytest.raises(ResumeProcessingUnavailable, match="isolation contract"):
        native_tex_transport.remote_compile(b"synthetic-source", url="https://native-tex.run.app:broken/compile", image=IMAGE, policy=POLICY)
