import io
import zipfile

import pytest
from fastapi import HTTPException

from backend.app.routers.resume import _validated_resume_upload


def _minimal_docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<w:document />")
    return output.getvalue()


def test_upload_requires_matching_extension_mime_and_magic_bytes():
    assert _validated_resume_upload(
        "resume.pdf", "application/pdf", b"%PDF-1.7\nminimal"
    ) == ("resume.pdf", "pdf")
    assert _validated_resume_upload(
        "resume.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _minimal_docx(),
    ) == ("resume.docx", "docx")

    with pytest.raises(HTTPException) as spoofed:
        _validated_resume_upload("resume.pdf", "application/pdf", b"not a pdf")
    assert spoofed.value.status_code == 400

    with pytest.raises(HTTPException) as mismatch:
        _validated_resume_upload("resume.exe", "application/pdf", b"%PDF-1.7")
    assert mismatch.value.status_code == 400


def test_upload_strips_client_paths_and_rejects_unsafe_docx_entries():
    safe_name, kind = _validated_resume_upload(
        r"C:\Users\name\resume.pdf", "application/pdf", b"%PDF-1.7"
    )
    assert safe_name == "resume.pdf"
    assert kind == "pdf"

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<w:document />")
        archive.writestr("../escape.exe", "bad")
    with pytest.raises(HTTPException) as unsafe:
        _validated_resume_upload("resume.docx", "application/octet-stream", output.getvalue())
    assert unsafe.value.status_code == 400
