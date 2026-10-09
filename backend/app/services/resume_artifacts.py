from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .. import models
from .resume_layout import apply_source_edits

ArtifactFormat = Literal["pdf", "docx", "tex", "texzip"]


class ResumeArtifactError(ValueError):
    """A version cannot be exported faithfully from its original source."""


@dataclass(frozen=True)
class RenderedResumeArtifact:
    content: bytes
    media_type: str
    filename: str


def render_resume_version(
    version: models.ResumeVersion,
    resume: models.Resume,
    artifact_format: ArtifactFormat,
) -> RenderedResumeArtifact:
    if not resume.source_document or resume.source_format not in {"pdf", "docx", "tex", "texzip"}:
        raise ResumeArtifactError(
            "The original resume file is unavailable. Upload it again and generate a new "
            "version to preserve its formatting."
        )
    content = version.structured_content or {}
    if (
        content.get("format_preservation") != "source"
        or content.get("source_format") != resume.source_format
        or not isinstance(content.get("source_edits"), list)
    ):
        raise ResumeArtifactError(
            "This version was created with the old resume template. Generate a new version "
            "from the original source to preserve its formatting."
        )
    if resume.source_format in {"tex", "texzip"}:
        from .native_tex import sealed_bytes
        if artifact_format not in {"pdf", resume.source_format}:
            raise ResumeArtifactError("Download the sealed PDF or the preserved native TeX source project.")
        rendered = sealed_bytes(content.get("sealed_native_artifact"), resume.source_document,
            resume.source_format, content["source_edits"], artifact_format)
        media_type = "application/pdf" if artifact_format == "pdf" else "application/zip" if artifact_format == "texzip" else "application/x-tex"
        suffix = "zip" if artifact_format == "texzip" else artifact_format
        return RenderedResumeArtifact(rendered, media_type, f"resume-v{version.version_number}.{suffix}")
    if artifact_format != resume.source_format:
        raise ResumeArtifactError(
            f"Download this resume as {resume.source_format.upper()} to retain its original formatting."
        )
    rendered = apply_source_edits(
        resume.source_document, resume.source_format, content["source_edits"],
    )
    stem = re.sub(r"[^A-Za-z0-9]+", "-", str(version.label or "")).strip("-").lower()[:72]
    return RenderedResumeArtifact(
        content=rendered,
        media_type="application/pdf" if artifact_format == "pdf" else
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{stem or 'tailored-resume'}-v{version.version_number}.{artifact_format}",
    )
