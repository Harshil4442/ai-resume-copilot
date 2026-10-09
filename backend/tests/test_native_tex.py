"""Native source, exact artifact and actual isolated compiler boundary tests."""
from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile, ZipInfo

import pytest
from backend.app.domains.employer.artifacts import materialize
from backend.app.routers.resume import _validated_resume_upload
from backend.app.services import native_tex
from backend.app.services.resume_artifacts import render_resume_version
from backend.app.services.resume_layout import ResumeLayoutError, extract_source_units
from backend.tests.native_tex_ci_fixtures import configure_native_compiler

FIXTURE = Path(__file__).parent / "fixtures/native_tex"


def project():
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for file in sorted(FIXTURE.rglob("*")):
            if file.is_file():
                archive.writestr(file.relative_to(FIXTURE).as_posix(), file.read_bytes())
    return output.getvalue()


def edit(data):
    unit = next(u for u in extract_source_units(data, "texzip") if u["text"].startswith("Developed"))
    return {"unit_id": unit["unit_id"], "original_text": unit["text"],
        "replacement_text": "Built Python services for customer reporting.",
        "evidence_ids": ["approved-original"], "reason": "Clearer evidence-backed action wording"}


@pytest.fixture
def compiler(monkeypatch):
    return configure_native_compiler(monkeypatch)


def test_project_sources_and_known_text_spans_are_stable_and_preserve_other_bytes():
    data = project()
    parsed = native_tex.read_project(data, "texzip")
    units = extract_source_units(data, "texzip")
    proposed = edit(data)
    replacements = {proposed["unit_id"]: proposed["replacement_text"]}
    changed = native_tex.patch_project(data, "texzip", units, replacements)
    result = native_tex.read_project(changed, "texzip")
    assert parsed.main == "resume.tex"
    assert result.files["resume.tex"] == parsed.files["resume.tex"]
    assert result.files["resumestyle.sty"] == parsed.files["resumestyle.sty"]
    assert result.files["sections/experience.tex"] == parsed.files["sections/experience.tex"].replace(
        proposed["original_text"].encode(), proposed["replacement_text"].encode())
    assert units == extract_source_units(data, "texzip")
    assert _validated_resume_upload("source.zip", "application/zip", data) == ("source.zip", "texzip")


@pytest.mark.parametrize("path", ["../resume.tex", "/resume.tex", "a/../resume.tex", "a\\resume.tex", ".hidden.tex", "bad.exe"])
def test_archive_traversal_absolute_hidden_and_executable_files_are_refused(path):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(path, b"not executable")
    with pytest.raises(ResumeLayoutError):
        native_tex.read_project(output.getvalue(), "texzip")


def test_archive_symlinks_duplicates_and_expansion_are_refused():
    for mode in ("link", "duplicate", "large"):
        output = BytesIO()
        with ZipFile(output, "w") as archive:
            if mode == "link":
                info = ZipInfo("resume.tex")
                info.external_attr = 0o120777 << 16
                archive.writestr(info, b"/etc/passwd")
            elif mode == "duplicate":
                archive.writestr("resume.tex", b"first")
                with pytest.warns(UserWarning):
                    archive.writestr("resume.tex", b"second")
            else:
                archive.writestr("resume.tex", b"a" * (native_tex.MAX_PROJECT_BYTES + 1))
        with pytest.raises(ResumeLayoutError):
            native_tex.read_project(output.getvalue(), "texzip")


@pytest.mark.parametrize("command", [r"\input{/etc/passwd}", r"\write18{touch sentinel}", r"\usepackage{minted}", r"\directlua{os.execute('x')}"])
def test_file_execution_and_unsupported_dependencies_fail_before_compilation(command):
    data = (r"\documentclass{article}\begin{document}" + command + r"\end{document}").encode()
    with pytest.raises(ResumeLayoutError):
        native_tex.read_project(data, "tex")


def test_unavailable_or_mutable_compiler_does_not_execute_host_tex(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("NATIVE_TEX_IMAGE", "tex:latest")
    monkeypatch.delenv("NATIVE_TEX_COMPILER_URL", raising=False)
    monkeypatch.setattr(native_tex.subprocess, "run", lambda *a, **k: pytest.fail("host execution is forbidden"))
    with pytest.raises(ResumeLayoutError, match="original compiled PDF"):
        native_tex.compile_project(project(), "texzip")


def test_command_injection_into_plain_source_span_is_refused():
    data = project()
    proposed = edit(data)
    with pytest.raises(ResumeLayoutError, match="plain text"):
        native_tex.patch_project(data, "texzip", extract_source_units(data, "texzip"),
            {proposed["unit_id"]: r"Built \input{secret} services"})


def test_sealed_source_pdf_download_and_application_use_identical_bytes_without_recompile(compiler, monkeypatch):
    data = project()
    proposed = edit(data)
    seal = native_tex.prepare_artifact(data, "texzip", [proposed])
    assert seal["classification"] == "tailored"
    assert seal["validation"]["pages"] == 1
    assert seal["validation"]["links"] == 2
    assert seal["validation"]["protected_glyphs"] > 100
    assert seal["validation"]["changed_spans"] == 1
    resume = SimpleNamespace(source_document=data, source_format="texzip", original_filename="source.zip")
    version = SimpleNamespace(structured_content={"format_preservation": "source", "source_format": "texzip",
        "source_edits": [proposed], "sealed_native_artifact": seal}, label="Reviewed", version_number=1)
    monkeypatch.setattr(native_tex, "compile_project", lambda *a, **k: pytest.fail("sealed files cannot regenerate"))
    pdf = render_resume_version(version, resume, "pdf")
    sealed = materialize(resume, version)
    assert pdf.content == base64.b64decode(seal["pdf_base64"]) == sealed.content
    assert sealed.sha256 == seal["pdf_sha256"]
    assert render_resume_version(version, resume, "texzip").content == base64.b64decode(seal["native_base64"])
    tampered = {**seal, "pdf_base64": base64.b64encode(b"bad").decode()}
    version.structured_content["sealed_native_artifact"] = tampered
    with pytest.raises(ResumeLayoutError, match="integrity"):
        render_resume_version(version, resume, "pdf")
    version.structured_content["sealed_native_artifact"] = seal
    version.structured_content["source_edits"] = [{**proposed, "replacement_text": "Different unreviewed text"}]
    with pytest.raises(ResumeLayoutError, match="changed after review"):
        render_resume_version(version, resume, "pdf")


def test_zero_edits_are_an_unchanged_source_snapshot_and_original_source_stays_exact(compiler):
    data = project()
    seal = native_tex.prepare_artifact(data, "texzip", [])
    assert seal["classification"] == "unchanged_source_snapshot"
    assert base64.b64decode(seal["native_base64"]) == data
    assert seal["validation"]["changed_spans"] == 0


def test_standalone_two_page_computer_modern_source_preserves_font_pages_and_links(compiler):
    data = (FIXTURE.parent / "native_tex_single/resume.tex").read_bytes()
    unit = next(u for u in extract_source_units(data, "tex") if u["text"].startswith("Developed"))
    proposed = {"unit_id": unit["unit_id"], "original_text": unit["text"],
        "replacement_text": "Built Python services for customer reporting.", "evidence_ids": [],
        "reason": "Clearer original action wording"}
    seal = native_tex.prepare_artifact(data, "tex", [proposed])
    assert seal["validation"]["pages"] == 2 and seal["validation"]["links"] == 2
    assert all("CM" in font or "SFRM" in font or "SFBX" in font for font in seal["validation"]["fonts"])
    assert native_tex.sealed_bytes(seal, data, "tex", [proposed], "tex") == data.replace(
        proposed["original_text"].encode(), proposed["replacement_text"].encode())


def test_real_compiler_rejects_bad_glyphs_missing_assets_and_runaway_work(compiler):
    base = r"\documentclass{article}\begin{document}%s\end{document}"
    for text in ("Unsupported glyph \u4f60", r"\loop\iftrue\repeat"):
        with pytest.raises(ResumeLayoutError, match="did not compile safely"):
            native_tex.compile_project((base % text).encode(), "tex")


def test_real_edit_overflow_or_reflow_cannot_be_sealed_as_preserving(compiler):
    data = project()
    proposed = edit(data)
    proposed["replacement_text"] = "Built " + "customer reporting services " * 8
    with pytest.raises(ResumeLayoutError):
        native_tex.prepare_artifact(data, "texzip", [proposed])


def test_invalid_remote_contract_cannot_fetch_identity_or_send_source(monkeypatch):
    from backend.app.services.native_tex_transport import remote_compile
    with pytest.raises(ResumeLayoutError, match="isolation contract"):
        remote_compile(b"{}", url="https://public.example/compile", image="tag:latest", policy="unreviewed")


def test_compiler_response_hash_and_size_are_verified():
    response = {"protocol": "hirewiz-tex-v1", "pdf": base64.b64encode(b"%PDF-synthetic").decode(),
        "sha256": "0" * 64, "size": 14}
    with pytest.raises(ResumeLayoutError, match="invalid artifact"):
        native_tex._compiler_pdf(response)
    assert json.loads(json.dumps(response)) == response


def test_owned_native_version_truthful_edits_and_exact_pdf_approval(compiler, monkeypatch):
    from backend.app.models import EvidenceItem, Resume
    from backend.tests.test_career_workspace import _client
    engine, factory, client = _client()
    try:
        data = project()
        proposed = edit(data)
        with factory() as db:
            resume = db.get(Resume, 10)
            resume.source_format, resume.source_document = "texzip", data
            db.add(EvidenceItem(id="approved-original", user_id=1, resume_id=10,
                category="experience", title="Source experience", evidence_text=proposed["original_text"],
                skills=["python"], approval_state="approved"))
            db.commit()
        content = {"format_preservation": "source", "source_format": "texzip", "source_edits": [proposed]}
        unsupported = {**proposed, "replacement_text": "Built Java services for customer reporting."}
        response = client.post("/api/v1/resume-versions", json={"resume_id": 10,
            "structured_content": {**content, "source_edits": [unsupported]}, "evidence_ids": ["approved-original"]})
        assert response.status_code == 422 and "factual skill" in response.json()["detail"]
        response = client.post("/api/v1/resume-versions", json={"resume_id": 10,
            "structured_content": content, "evidence_ids": ["approved-original"]})
        assert response.status_code == 201
        version = response.json()
        monkeypatch.setattr(native_tex, "compile_project", lambda *a, **k: pytest.fail("approved artifacts must not recompile"))
        pdf = client.get(f'/api/v1/resume-versions/{version["id"]}/download?format=pdf')
        assert pdf.status_code == 200 and native_tex.digest(pdf.content) == version["structured_content"]["sealed_native_artifact"]["pdf_sha256"]
        source = client.get(f'/api/v1/resume-versions/{version["id"]}/download?format=texzip')
        assert source.status_code == 200 and source.headers["content-type"] == "application/zip"
        approved = client.patch(f'/api/v1/resume-versions/{version["id"]}', json={"approval_state": "approved"})
        assert approved.status_code == 200 and approved.json()["approval_state"] == "approved"
        private = client.post("/api/v1/resume-versions", json={"resume_id": 20})
        assert private.status_code == 404
    finally:
        client.close()
        engine.dispose()


def test_conditional_macro_decoration_changes_outside_text_are_refused(compiler):
    parsed = native_tex.read_project(project(), "texzip")
    files = dict(parsed.files)
    files["resumestyle.sty"] = (r"\ProvidesPackage{resumestyle}" + "\n" +
        r"\newcommand{\resumeItem}[1]{\item #1\def\original{Developed Python services for customer reporting.}\def\actual{#1}\ifx\original\actual\rlap{\raisebox{-20pt}[0pt][0pt]{\rule{20pt}{1pt}}}\fi}").encode()
    data = native_tex.Project(files, parsed.main, parsed.kind).bytes()
    with pytest.raises(ResumeLayoutError, match="visible layout outside"):
        native_tex.prepare_artifact(data, "texzip", [edit(data)])


def test_native_truth_guard_rejects_unproved_leadership_and_removed_qualifiers():
    proposed = edit(project())
    with pytest.raises(ResumeLayoutError, match="unsupported responsibility"):
        native_tex.validate_claims([{**proposed, "replacement_text": "Led Python services for customer reporting."}], [])
    native_tex.validate_claims([proposed], [])
    qualified = {**proposed, "original_text": "Supported Python services for customer reporting.", "replacement_text": "Built Python services for customer reporting."}
    with pytest.raises(ResumeLayoutError, match="source qualifier"):
        native_tex.validate_claims([qualified], [])


@pytest.mark.parametrize("active", ["catalog", "link"])
def test_native_original_and_version_checks_refuse_pdf_active_content(active):
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    action = DictionaryObject({NameObject("/S"): NameObject("/JavaScript"),
        NameObject("/JS"): TextStringObject("app.alert('untrusted')")})
    if active == "catalog":
        writer.root_object[NameObject("/OpenAction")] = action
    else:
        action = DictionaryObject({NameObject("/S"): NameObject("/URI"),
            NameObject("/URI"): TextStringObject("javascript:alert('untrusted')")})
        page[NameObject("/Annots")] = ArrayObject([DictionaryObject({
            NameObject("/Subtype"): NameObject("/Link"), NameObject("/A"): action})])
    output = BytesIO()
    writer.write(output)
    with pytest.raises(ResumeLayoutError, match="active content|interactive action"):
        native_tex.validate_layout(output.getvalue(), output.getvalue(), [], {})


def test_worker_timeout_kills_owned_process_group_before_reusing_instance(monkeypatch):
    import importlib.util
    import signal
    import subprocess

    path = Path(__file__).parents[2] / "infra/native-tex/service.py"
    spec = importlib.util.spec_from_file_location("native_tex_worker", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    calls, kills = [], []

    class Child:
        pid = 12345
        returncode = None

        def communicate(self, *args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("owned-processor", 25)
            return b"", None

    options = {}

    def spawn(command, **kwargs):
        options.update(kwargs)
        assert command == ["python3", "-I", "/processor.py"]
        return Child()

    monkeypatch.setattr(worker.subprocess, "Popen", spawn)
    monkeypatch.setattr(worker.os, "killpg", lambda pid, sig: kills.append((pid, sig)))
    with pytest.raises(subprocess.TimeoutExpired):
        worker.compile_payload(b"bounded-source")
    assert options["start_new_session"] is True and options["env"] == {"PATH": "/usr/bin:/bin"}
    assert kills == [(12345, signal.SIGKILL)] and len(calls) == 2
