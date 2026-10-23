"""Native source admission controls; synthetic scan replies are not AV proof."""

from __future__ import annotations

import hashlib
import io
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from test_document_processor import load, payload, pdf

SOURCE = rb"\documentclass{article}\begin{document}Jordan Example, Python\end{document}"
SCAN = {"engine": "ClamAV", "version": "1.4.3", "definitions": "1/current"}
EMPTY_PARSED = {
    "raw_text": "", "sections": {}, "skills": [], "experience_years": 0.0,
    "contact_info": {name: None for name in ("name", "email", "phone", "linkedin", "github")},
}


def project(files: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in (files or {"resume.tex": SOURCE}).items():
            archive.writestr(name, content)
    return output.getvalue()


@pytest.fixture
def processor():
    return load("processor")


@pytest.mark.parametrize("kind,content", [("tex", SOURCE), ("texzip", project())])
def test_native_original_scanned_before_real_project_validation_without_compiler_or_extractor(
    processor, monkeypatch, kind, content,
):
    from app.services import native_project, native_tex, parsing

    calls = []
    real_validation = native_project.read_project

    def scan(path):
        calls.append(("scan", path.read_bytes()))
        return SCAN

    def validate(source, source_kind):
        assert calls == [("scan", content)]
        calls.append(("validate", source, source_kind))
        return real_validation(source, source_kind)

    monkeypatch.setattr(processor, "scan_original", scan)
    monkeypatch.setattr(native_project, "read_project", validate)
    monkeypatch.setattr(native_tex, "compile_project", lambda *a, **k: pytest.fail("compiler reached"))
    monkeypatch.setattr(parsing, "parse_resume_file", lambda *a, **k: pytest.fail("extractor reached"))

    result = processor.inspect(payload(content, kind))
    assert calls == [("scan", content), ("validate", content, kind)]
    assert result == {
        "version": 1, "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content), "source_format": kind, "scan": SCAN, "parsed": EMPTY_PARSED,
    }


@pytest.mark.parametrize("kind,content", [("tex", SOURCE), ("texzip", project())])
@pytest.mark.parametrize("outcome", ["refused", "unavailable"])
def test_native_scan_refusal_or_unavailability_prevents_project_parsing(
    processor, monkeypatch, kind, content, outcome,
):
    from app.services import native_project

    error = processor.InspectionDenied if outcome == "refused" else processor.InspectionUnavailable

    def scan(path):
        assert path.read_bytes() == content
        raise error()

    monkeypatch.setattr(processor, "scan_original", scan)
    monkeypatch.setattr(native_project, "read_project", lambda *a: pytest.fail("native parser reached"))
    with pytest.raises(error):
        processor.inspect(payload(content, kind))


@pytest.mark.parametrize("kind,content", [
    ("tex", b"\xff"),
    ("tex", SOURCE.replace(b"Jordan Example, Python", rb"\write18{arbitrary-command}")),
    ("tex", SOURCE.replace(b"Jordan Example, Python", rb"\usepackage{minted}")),
    ("texzip", b"not a ZIP"),
    ("texzip", project({"../resume.tex": SOURCE})),
    ("texzip", project({"resume.tex": SOURCE, "bad.exe": b"unsupported"})),
    ("texzip", project({"resume.tex": SOURCE, "extra.tex": b"a" * (5 * 1024**2)})),
    ("texzip", project({"resume.tex": SOURCE, **{f"part{i}.tex": b"text" for i in range(64)}})),
])
def test_unsupported_native_sources_are_refused_after_scan(processor, monkeypatch, kind, content):
    scanned = []
    monkeypatch.setattr(processor, "scan_original", lambda path: scanned.append(path.read_bytes()) or SCAN)
    with pytest.raises(processor.InspectionDenied):
        processor.inspect(payload(content, kind))
    assert scanned == [content]


@pytest.mark.parametrize("active", [False, True])
def test_pdf_asset_uses_existing_passive_pdf_validator(processor, monkeypatch, active):
    source = SOURCE.replace(b"Jordan Example, Python", rb"\includegraphics{asset.pdf}")
    content = project({"resume.tex": source, "asset.pdf": pdf(action="/JavaScript" if active else None)})
    monkeypatch.setattr(processor, "scan_original", lambda path: SCAN)
    if active:
        with pytest.raises(processor.InspectionDenied):
            processor.inspect(payload(content, "texzip"))
    else:
        assert processor.inspect(payload(content, "texzip"))["parsed"] == EMPTY_PARSED


@pytest.mark.parametrize("kind", ["tex", "texzip"])
def test_native_hash_mismatch_refuses_before_any_scan(processor, monkeypatch, kind):
    monkeypatch.setattr(processor, "scan_original", lambda path: pytest.fail("scanner reached"))
    with pytest.raises(processor.InspectionUnavailable):
        processor.inspect(payload(SOURCE, kind, sha256="0" * 64))


@pytest.mark.parametrize("kind", ["tex", "texzip"])
def test_http_host_native_identity_never_opens_a_project(monkeypatch, kind):
    from app.services import native_project

    service = load("service")
    monkeypatch.setattr(native_project, "read_project", lambda *a: pytest.fail("HTTP host parsed project"))
    # Structural validation belongs to the sandbox, not the HTTP identity step.
    content = b"not a valid project"
    assert service._request_identity(payload(content, kind)) == (
        hashlib.sha256(content).hexdigest(), len(content), kind,
    )


@pytest.mark.parametrize("kind", ["tex", "texzip"])
def test_http_host_native_identity_hash_mismatch_is_refused(kind):
    service = load("service")
    with pytest.raises(service.InspectionDenied):
        service._request_identity(payload(SOURCE, kind, sha256="0" * 64))
