"""Finite public guidance for a failed resume-writing operation.

Persist only an internal versioned token in the existing failure column. Source
text, paths, unit IDs and provider details never form part of the public contract.
"""
from __future__ import annotations

from typing import Literal

TailoringFailureReason = Literal[
    "source_required", "editable_source_required", "source_unreadable", "source_too_large",
    "source_protected", "source_format_unsupported",
    "font_unsupported", "replacement_does_not_fit", "source_layout_changed",
    "source_span_unavailable", "source_links_protected", "evidence_changes_unsupported",
    "tex_dependency_unsupported", "tex_compile_failed", "tex_worker_unavailable",
    "tex_artifact_invalid", "no_supported_changes", "validation_failed",
]
TailoringNextAction = Literal[
    "choose_source", "upload_editable_source", "check_source_file", "reduce_source_size",
    "use_supported_font", "shorten_wording", "keep_original_layout", "edit_source_manually",
    "keep_link_text", "review_evidence", "check_tex_dependencies", "check_tex_source",
    "use_original_or_custom",
]

_ACTIONS: dict[str, TailoringNextAction] = {
    "source_required": "choose_source",
    "source_protected": "use_original_or_custom",
    "source_format_unsupported": "upload_editable_source",
    "editable_source_required": "upload_editable_source",
    "source_unreadable": "check_source_file",
    "source_too_large": "reduce_source_size",
    "font_unsupported": "use_supported_font",
    "replacement_does_not_fit": "shorten_wording",
    "source_layout_changed": "keep_original_layout",
    "source_span_unavailable": "edit_source_manually",
    "source_links_protected": "keep_link_text",
    "evidence_changes_unsupported": "review_evidence",
    "tex_dependency_unsupported": "check_tex_dependencies",
    "tex_compile_failed": "check_tex_source",
    "tex_worker_unavailable": "use_original_or_custom",
    "tex_artifact_invalid": "use_original_or_custom",
    "no_supported_changes": "use_original_or_custom",
    "validation_failed": "use_original_or_custom",
}
_PREFIX = "resume-tailoring-failure/v1:"

# Exact fixed engine messages only. Unknown exceptions never use substring
# matching, inferred file paths or the exception's arbitrary reason_code.
_MESSAGES: dict[str, str] = {
    "Layout-preserving tailoring supports PDF, DOCX and supported native TeX projects.": "source_format_unsupported",
    "Encrypted PDFs cannot be tailored in place.": "source_protected",
    "Signed PDFs cannot be tailored without invalidating the signature.": "source_protected",
    "PDF forms cannot be tailored in place.": "source_protected",
    "Signed DOCX files cannot be tailored without invalidating the signature.": "source_protected",
    'The replacement changes an original DOCX hyperlink label.': "source_links_protected",
    'The replacement changes text covered by an original PDF link.': "source_links_protected",
    "Upload the original resume before tailoring": "source_required",
    "The original resume document is missing or too large.": "source_too_large",
    "No source text can be edited while preserving this layout. Keep the original/custom resume or upload editable DOCX/TeX source; protected fonts, macros or image-only text need manual editing.": "editable_source_required",
    "The original PDF cannot be read safely.": "source_unreadable",
    "The original DOCX cannot be read safely.": "source_unreadable",
    "The original PDF font cannot safely encode this character.": "font_unsupported",
    "The original PDF font is missing a required glyph.": "font_unsupported",
    "The PDF text style cannot be verified.": "font_unsupported",
    "Use only characters available in the source font": "font_unsupported",
    "The compiled TeX PDF contains glyphs without readable font mappings. Use a Unicode-mapped font such as Latin Modern or a readable original/custom PDF.": "font_unsupported",
    "The replacement does not fit its original PDF text slot.": "replacement_does_not_fit",
    "Saved PDF text exceeds its original slot.": "replacement_does_not_fit",
    "The replacement overlaps neighboring PDF content.": "replacement_does_not_fit",
    "The replacement is too long for its original DOCX paragraph.": "replacement_does_not_fit",
    "Replacements must change the text and fit its original length": "replacement_does_not_fit",
    "Replacements must fit their original length including boundary spacing": "replacement_does_not_fit",
    "The TeX replacement cannot fit the original font and region. Shorten it or keep the original/custom resume.": "replacement_does_not_fit",
    "The PDF changed visually outside the accepted text slots.": "source_layout_changed",
    "Unedited PDF text moved during source editing.": "source_layout_changed",
    "The PDF page count changed during source editing.": "source_layout_changed",
    "Saved PDF text or font differs from the accepted source edit.": "source_layout_changed",
    "The DOCX edit could not preserve the original document.": "source_layout_changed",
    "The TeX edit changed pages, page geometry or links. Shorten the replacement or keep the original/custom resume.": "source_layout_changed",
    "The TeX edit changed unexpected text or reading order. Keep the original/custom resume or edit the source manually.": "source_layout_changed",
    "The TeX edit moved protected text or changed typography. Shorten the edit or keep the original/custom resume.": "source_layout_changed",
    "The TeX edit changed visible layout outside its text span. Keep the original/custom resume or edit the source manually.": "source_layout_changed",
    "The original source text no longer matches the proposed edit.": "source_span_unavailable",
    "An edit refers to an unknown, protected or repeated source unit.": "source_span_unavailable",
    "Copy original_text exactly from the source location": "source_span_unavailable",
    "The TeX source span is not uniquely readable in the compiled PDF. Edit this source manually or use the original PDF.": "source_span_unavailable",
    "Every replacement must cite supplied approved evidence": "evidence_changes_unsupported",
    "Every factual skill or named term must be supported by the original text or cited evidence": "evidence_changes_unsupported",
    "Source edits must preserve all original numbers and dates.": "evidence_changes_unsupported",
    "Source edits cannot add or change contact information.": "evidence_changes_unsupported",
    "Preserve all original numbers, dates, and metrics exactly": "evidence_changes_unsupported",
    "The TeX edit adds unsupported responsibility or achievement wording, or removes a source qualifier. Use wording supported by cited approved evidence or keep the original.": "evidence_changes_unsupported",
    "The TeX edit changes a source qualifier or its claim scope. Keep the original qualified statement or edit and verify the source manually.": "evidence_changes_unsupported",
    "The TeX edit recombines supported words into an unsupported responsibility or achievement. Use a complete cited approved statement or keep the original.": "evidence_changes_unsupported",
    "The TeX project needs an unsupported dependency. Use the supported package list or upload the original PDF.": "tex_dependency_unsupported",
    "An included TeX source or asset is missing. Include every project file in the ZIP or upload PDF.": "tex_dependency_unsupported",
    "The TeX source did not compile safely or overflowed its layout. Check missing assets, unsupported fonts/dependencies and text length, or use the original/custom PDF.": "tex_compile_failed",
    "The isolated TeX compiler is unavailable. Upload the original compiled PDF or a custom PDF/DOCX; your source is not converted on the API host.": "tex_worker_unavailable",
    "The isolated TeX worker returned an invalid artifact. Use the original/custom PDF until the worker is checked.": "tex_artifact_invalid",
    "The sealed TeX artifact failed integrity verification. Prepare a new version; do not use this file.": "tex_artifact_invalid",
    "Return between one and twelve useful source replacements": "no_supported_changes",
    "at least one source replacement is required": "no_supported_changes",
}


def persist_tailoring_failure(exc: Exception) -> str:
    """Keep an allowlisted reason from the native cause, with bounded traversal."""
    from ...services.llm_client import TailoringOutputError
    from ...services.resume_layout import ResumeLayoutError

    current: BaseException | None = exc
    seen: set[int] = set()
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, (ResumeLayoutError, TailoringOutputError)):
            reason = _MESSAGES.get(str(current))
            if reason is not None:
                return _PREFIX + reason
        current = current.__cause__ or current.__context__
    return _PREFIX + "validation_failed"


def public_tailoring_guidance(operation: str, status: str, stored_message: object) -> dict[str, str] | None:
    """Legacy/unknown failures receive useful generic guidance, never reflection."""
    if operation != "resume_tailor" or status != "failed":
        return None
    reason = stored_message.removeprefix(_PREFIX) if isinstance(stored_message, str) and stored_message.startswith(_PREFIX) else "validation_failed"
    if reason not in _ACTIONS:
        reason = "validation_failed"
    return {"reason_code": reason, "next_action": _ACTIONS[reason]}
