"""Native R2 truth scope, inert PDF objects and structured refusal boundaries."""
from __future__ import annotations

import base64
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from backend.app.domains.employer.artifacts import materialize
from backend.app.models import Resume, ResumeVersion
from backend.app.services import native_tex
from backend.app.services.resume_layout import ResumeLayoutError
from backend.tests.native_tex_ci_fixtures import configure_native_compiler
from backend.tests.test_career_workspace import _client, _native_source
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject
from reportlab.pdfgen.canvas import Canvas

MOCK_IMAGE = "sha256:" + "1" * 64


def source(text: str) -> bytes:
    return (r"\documentclass{article}\usepackage[T1]{fontenc}\usepackage{lmodern}"
        r"\pagestyle{empty}\begin{document}" + "\n" + text + "\n" + r"\end{document}").encode()


@pytest.mark.parametrize("text", [r"Updated \today.", r"Updated \number\year.",
    r"Updated \month/\day.", r"Updated \the\time.", r"\maketitle",
    r"Updated \pdfcreationdate."])
def test_dynamic_date_refusal_preserves_original_source_and_never_starts_compiler(text, monkeypatch):
    data = source(text)
    before = data[:]
    monkeypatch.setattr(native_tex.subprocess, "run", lambda *a, **kw: pytest.fail("refused source must not start a compiler"))
    with pytest.raises(ResumeLayoutError, match="dynamic date.*original/custom PDF"):
        native_tex.prepare_artifact(data, "tex", [])
    assert data == before


def test_existing_dynamic_date_version_api_refuses_without_saving_a_version_or_changing_source(monkeypatch):
    data = source(r"Updated \today.")
    engine, factory, client = _client()
    monkeypatch.setattr(native_tex.subprocess, "run", lambda *a, **kw: pytest.fail("API must refuse before compiling"))
    try:
        with factory() as db:
            resume = db.get(Resume, 10)
            resume.source_document, resume.source_format = data, "tex"
            db.commit()
        response = client.post("/api/v1/resume-versions", json={"resume_id": 10,
            "structured_content": {"format_preservation": "source", "source_format": "tex", "source_edits": []}})
        assert response.status_code == 422
        assert "dynamic date" in response.json()["detail"]
        assert "original/custom PDF" in response.json()["detail"]
        with factory() as db:
            assert db.get(Resume, 10).source_document == data
            assert db.query(ResumeVersion).filter(ResumeVersion.resume_id == 10).count() == 0
        for kind in ("pdf", "docx"):
            original = _native_source(kind)
            original_resume = SimpleNamespace(source_document=original, source_format=kind, original_filename=f"original.{kind}")
            attached = materialize(original_resume)
            assert attached.content == original and attached.sha256 == native_tex.digest(original)
    finally:
        client.close()
        engine.dispose()


def test_literal_intended_date_compiles_and_snapshot_source_remains_exact(monkeypatch):
    configure_native_compiler(monkeypatch)
    data = source("Updated October 9, 2026.")
    seal = native_tex.prepare_artifact(data, "tex", [])
    assert native_tex.sealed_bytes(seal, data, "tex", [], "tex") == data
    pdf = base64.b64decode(seal["pdf_base64"])
    assert "Updated October 9, 2026." in PdfReader(BytesIO(pdf)).pages[0].extract_text()
    assert seal["classification"] == "unchanged_source_snapshot"


@pytest.mark.parametrize("original,replacement", [
    ("Built Python reports. Did not manage teams.", "Built not Python reports. Did manage teams."),
    ("Only assisted customer teams with Python reports.", "Assisted customer teams with only Python reports."),
    ("Built Python reports without managing customer teams.", "Built Python reports managing customer teams without."),
    ("Built Python reports and supported customer teams.", "Supported Python reports and built customer teams."),
    ("Built Python reports for customer teams.", "Built customer reports for Python teams."),
])
def test_word_reuse_does_not_certify_changed_claim_relationships(original, replacement):
    edit = {"unit_id": "claim", "original_text": original, "replacement_text": replacement, "evidence_ids": ["fact"]}
    with pytest.raises(ResumeLayoutError, match="qualifier|unsupported responsibility"):
        native_tex.validate_claims([edit], [{"id": "fact", "text": original, "skills": ["Python"]}])


def test_creation_synonym_preserves_qualified_scope_and_complete_approved_statement_is_supported():
    original = "Developed Python reports. Did not manage customer teams."
    native_tex.validate_claims([{"unit_id": "claim", "original_text": original,
        "replacement_text": "Built Python reports. Did not manage customer teams.", "evidence_ids": []}], [])
    native_tex.validate_claims([{"unit_id": "claim", "original_text": "Built Python reports.",
        "replacement_text": "Built Python reporting services.", "evidence_ids": ["verified"]}],
        [{"id": "verified", "text": "Developed Python reporting services.", "skills": ["Python"]}])


def pdf_with_objects(location: str, action: DictionaryObject) -> bytes:
    output = BytesIO()
    canvas = Canvas(output)
    canvas.drawString(60, 700, "Readable synthetic resume text")
    canvas.save()
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(output.getvalue())))
    reference = writer._add_object(action)
    if location == "extension":
        writer.root_object[NameObject("/SyntheticExtension")] = DictionaryObject({NameObject("/Nested"): reference})
    elif location == "opening":
        writer.root_object[NameObject("/OpenAction")] = reference
    else:
        writer.pages[0][NameObject("/Annots")] = ArrayObject([DictionaryObject({
            NameObject("/Subtype"): NameObject("/Link"), NameObject("/A"): reference,
            NameObject("/Rect"): ArrayObject()})])
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


@pytest.mark.parametrize("location", ["annotation", "opening", "extension"])
def test_indirect_nested_inert_action_is_refused_before_any_render(location):
    # Never execute or render this fixture; the marker is deliberately inert.
    chained = DictionaryObject({NameObject("/S"): NameObject("/URI"),
        NameObject("/URI"): TextStringObject("https://example.test/profile"),
        NameObject("/Next"): ArrayObject([DictionaryObject({NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("/* inert marker */")})])})
    data = pdf_with_objects(location, chained)
    with pytest.raises(ResumeLayoutError, match="active content|interactive action"):
        native_tex._pdf_view(data)


def test_passive_uri_links_and_outline_sibling_objects_remain_supported():
    action = DictionaryObject({NameObject("/S"): NameObject("/URI"),
        NameObject("/URI"): TextStringObject("https://example.test/profile")})
    data = pdf_with_objects("annotation", action)
    assert native_tex.validate_layout(data, data, [], {})["links"] == 1
    sibling = DictionaryObject({NameObject("/Title"): TextStringObject("Second")})
    reader = SimpleNamespace(trailer=DictionaryObject({NameObject("/Outlines"): DictionaryObject({
        NameObject("/Title"): TextStringObject("First"), NameObject("/Next"): sibling})}))
    native_tex._validate_pdf_actions(reader)


def test_action_graph_work_and_cycles_are_bounded():
    node = DictionaryObject()
    node[NameObject("/SyntheticCycle")] = node
    native_tex._validate_pdf_actions(SimpleNamespace(trailer=node))
    nested = DictionaryObject()
    for _ in range(130):
        nested = DictionaryObject({NameObject("/Nested"): nested})
    with pytest.raises(ResumeLayoutError, match="active content|interactive action"):
        native_tex._validate_pdf_actions(SimpleNamespace(trailer=nested))


@pytest.mark.parametrize("response", [None, [], 1, True, "pdf", {"pdf": None},
    {"protocol": "hirewiz-tex-v1", "pdf": [], "sha256": "0" * 64, "size": 1},
    {"protocol": "hirewiz-tex-v1", "pdf": "JVBERi0=", "sha256": "0" * 64, "size": True}])
def test_malformed_private_compiler_contract_is_a_structured_refusal(response):
    with pytest.raises(ResumeLayoutError, match="invalid artifact"):
        native_tex._compiler_pdf(response)


@pytest.mark.parametrize("response", [None, []])
def test_malformed_local_compiler_json_cleans_only_owned_container(response, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("NATIVE_TEX_IMAGE", MOCK_IMAGE)
    monkeypatch.delenv("NATIVE_TEX_COMPILER_URL", raising=False)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(response).encode())
    monkeypatch.setattr(native_tex.subprocess, "run", run)
    with pytest.raises(ResumeLayoutError, match="invalid artifact"):
        native_tex.compile_project(source("Built Python reports."), "tex")
    owned = calls[0][calls[0].index("--name") + 1]
    assert calls[-1] == ["docker", "rm", "-f", owned]


def test_pre_r2_seals_require_new_safe_preparation():
    data = source("Built Python reports.")
    old_seal = {"validator": "hirewiz-native-tex-v1", "source_sha256": native_tex.digest(data),
        "edits_sha256": native_tex.digest(b"[]")}
    with pytest.raises(ResumeLayoutError, match="review it again"):
        native_tex.sealed_bytes(old_seal, data, "tex", [], "pdf")


def test_malformed_pdf_contract_does_not_escape_as_parser_error():
    with pytest.raises(ResumeLayoutError, match="malformed.*original/custom PDF"):
        native_tex._pdf_view(b"%PDF-not a valid document")
