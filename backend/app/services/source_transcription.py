"""Pure readability/context checks; no parser, review writer or admission authority.

Consumers must supply the owned immutable source and its actual native extraction,
fresh owned evidence rows, and explicit review records loaded from server-owned
persistence. Ordinary evidence approval is not a transcription-review record.
Constructing the record below proves neither persistence nor candidate consent.
No function here issues such a record, changes an approval, or edits source bytes.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal

from ..domains.career.schemas import evidence_snapshot_fingerprint

VERSION = "reviewed-source-transcription-v1"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
# A native line boundary supplies no proof that a hyphen is discretionary.
# Keep ordinary same-line compounds, dates and identifiers out of this detector.
_LINE_END = re.compile(r"(?<![^\W\d_])[^\W\d_]+[-\u00ad\u2010\u2011][ \t]*\r?\n[ \t]*[^\W\d_]+")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fingerprint(value: object) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False).encode())


class SourceTranscriptionChanged(ValueError):
    def __init__(self) -> None:
        super().__init__("source_transcription_changed")


@dataclass(frozen=True)
class SectionBinding:
    section: str
    start: int
    end: int
    original_sha256: str


@dataclass(frozen=True)
class Assessment:
    user_id: int
    resume_id: int
    source_format: str
    source_sha256: str
    source_bytes: bytes = field(repr=False)
    raw_text: str = field(repr=False)
    sections: tuple[tuple[str, str], ...] = field(repr=False)
    affected_sections: tuple[SectionBinding, ...]
    issue_count: int
    editable_source_required: bool
    fingerprint: str


@dataclass(frozen=True)
class ExplicitTranscriptionReview:
    """Input from a future persisted, explicit review action, never a request DTO.

    The loader must independently verify the actual persisted action and actor.
    Its record identity and exact source/section/text/evidence version are bound
    again here. Legacy approved rows alone must never manufacture this input.
    """
    record_id: str
    user_id: int
    resume_id: int
    source_sha256: str
    section: str
    original_section_sha256: str
    approved_text_sha256: str
    evidence_id: str
    evidence_fingerprint: str
    action: Literal["correct_transcription", "confirm_visible_text"]


@dataclass(frozen=True)
class ResolvedTranscription:
    status: Literal["ready", "needs_review", "editable_source_required"]
    reason: str | None
    raw_text: str | None = field(repr=False)
    sections: tuple[tuple[str, str], ...] = field(repr=False)
    assessment_fingerprint: str
    review_version_fingerprint: str | None
    context_fingerprint: str | None
    # This is a context compatibility decision, never native layout acceptance.
    # An ambiguous native source cannot gain ordinary tailoring compatibility
    # from a logical correction/confirmation or from a model response.
    physical_edit_compatible: bool


def assess_source(*, user_id: int, resume_id: int, source_bytes: bytes,
                  source_format: str, raw_text: str, sections: dict[str, str]) -> Assessment:
    """Find conservative ambiguity in native extracted text without joining it."""
    if (type(user_id) is not int or user_id <= 0 or type(resume_id) is not int or resume_id <= 0
            or type(source_bytes) is not bytes or not 0 < len(source_bytes) <= 5 * 1024**2
            or type(source_format) is not str or source_format not in {"pdf", "docx", "tex", "texzip"}
            or type(raw_text) is not str or not raw_text.strip() or "\x00" in raw_text or len(raw_text.encode()) > 262144
            or type(sections) is not dict or len(sections) > 30
            or any(type(k) is not str or not 0 < len(k.encode()) <= 80 or type(v) is not str
                   or "\x00" in v for k, v in sections.items())
            or sum(len(v.encode()) for v in sections.values()) > 262144):
        raise ValueError("source_transcription_input_invalid")
    original = tuple(sections.items())
    matches: list[re.Match[str]] = []
    count = 0
    for match in _LINE_END.finditer(raw_text):
        count += 1
        if len(matches) < 256:
            matches.append(match)
    affected: dict[str, SectionBinding] = {}
    unmapped = count > 256
    locations = []
    for section, text in original:
        start = raw_text.find(text) if text else -1
        if start >= 0 and raw_text.find(text, start + 1) == -1:
            locations.append(SectionBinding(section, start, start + len(text), _sha(text.encode())))
        elif _LINE_END.search(text):
            # AI consumers also receive sections. An ambiguous section view
            # without one exact raw span cannot be replaced safely.
            unmapped = True
    for match in matches if not unmapped else ():
        candidates = [row for row in locations if row.start <= match.start() and match.end() <= row.end]
        if len(candidates) != 1:
            unmapped = True
        else:
            binding = candidates[0]
            # The existing Evidence editor cannot review a silently truncated
            # section. Its current limit is 20,000 characters.
            if len(dict(original)[binding.section]) > 20000:
                unmapped = True
            affected[binding.section] = binding
    bindings = tuple(sorted(affected.values(), key=lambda row: row.start))
    if any(left.end > right.start for left, right in zip(bindings, bindings[1:], strict=False)):
        unmapped = True
    source_sha = _sha(source_bytes)
    identity = {"version": VERSION, "owner": user_id, "resume": resume_id,
        "source_format": source_format, "source_sha256": source_sha,
        "raw_sha256": _sha(raw_text.encode()),
        "sections": [(name, _sha(text.encode())) for name, text in original],
        "issues": [(m.start(), m.end()) for m in matches], "issue_count": count, "unmapped": unmapped}
    return Assessment(user_id, resume_id, source_format, source_sha, source_bytes, raw_text, original,
                      bindings, count, unmapped, _fingerprint(identity))


def resolve_transcription(assessment: Assessment, evidence: list[object],
                          reviews: tuple[ExplicitTranscriptionReview, ...] = (), *,
                          expected_context_fingerprint: str | None = None) -> ResolvedTranscription:
    """Resolve complete context only from explicit, current approved reviews.

    Recompute from freshly loaded source/evidence at quote, dispatch and commit;
    an old Assessment and old evidence objects cannot discover database changes.
    The future consumer must load persisted review actions independently. Passing
    a self-issued record is not supported and is not proof of a persisted action.
    """
    def refused(reason: str, *, editable: bool = False) -> ResolvedTranscription:
        if expected_context_fingerprint is not None:
            raise SourceTranscriptionChanged()
        return ResolvedTranscription("editable_source_required" if editable else "needs_review",
            reason, None, (), assessment.fingerprint, None, None, False)

    current = assess_source(user_id=assessment.user_id, resume_id=assessment.resume_id,
        source_bytes=assessment.source_bytes, source_format=assessment.source_format,
        raw_text=assessment.raw_text, sections=dict(assessment.sections))
    if current != assessment:
        return refused("assessment_binding_changed")
    if assessment.editable_source_required:
        return refused("section_cannot_be_reviewed_exactly", editable=True)
    if len(evidence) > 1000 or len(reviews) > 30:
        return refused("review_scope_invalid")
    required = {row.section: row for row in assessment.affected_sections}
    if not required and reviews:
        return refused("review_scope_invalid")
    selected: dict[str, tuple[ExplicitTranscriptionReview, str]] = {}
    record_ids: set[str] = set()
    for review in reviews:
        if (type(review) is not ExplicitTranscriptionReview or type(review.section) is not str
                or review.section not in required or review.section in selected
                or type(review.record_id) is not str or not review.record_id or len(review.record_id) > 160
                or type(review.evidence_id) is not str or not 0 < len(review.evidence_id) <= 64
                or review.record_id in record_ids or type(review.user_id) is not int
                or type(review.resume_id) is not int or review.user_id != assessment.user_id
                or review.resume_id != assessment.resume_id or review.source_sha256 != assessment.source_sha256
                or review.original_section_sha256 != required[review.section].original_sha256
                or type(review.action) is not str
                or review.action not in {"correct_transcription", "confirm_visible_text"}
                or type(review.approved_text_sha256) is not str or not _SHA.fullmatch(review.approved_text_sha256)
                or type(review.evidence_fingerprint) is not str or not _SHA.fullmatch(review.evidence_fingerprint)):
            return refused("review_binding_changed")
        rows = [row for row in evidence if getattr(row, "id", None) == review.evidence_id]
        if len(rows) != 1:
            return refused("review_evidence_unavailable")
        row = rows[0]
        text = getattr(row, "evidence_text", None)
        if (getattr(row, "user_id", None) != assessment.user_id
                or getattr(row, "resume_id", None) != assessment.resume_id
                or getattr(row, "approval_state", None) != "approved"
                or type(text) is not str or not text.strip() or len(text) > 20000
                or _sha(text.encode()) != review.approved_text_sha256):
            return refused("review_evidence_changed")
        try:
            if evidence_snapshot_fingerprint(row) != review.evidence_fingerprint:
                return refused("review_version_changed")
        except (ValueError, TypeError, AttributeError):
            return refused("review_version_changed")
        original = dict(assessment.sections)[review.section]
        if ((review.action == "confirm_visible_text" and text != original)
                or (review.action == "correct_transcription" and (text == original or _LINE_END.search(text)))):
            return refused("review_action_mismatch")
        selected[review.section] = (review, text)
        record_ids.add(review.record_id)
    if set(selected) != set(required):
        return refused("explicit_transcription_review_required")
    raw = assessment.raw_text
    for binding in reversed(assessment.affected_sections):
        raw = raw[:binding.start] + selected[binding.section][1] + raw[binding.end:]
    sections = tuple((name, selected[name][1] if name in selected else text)
                     for name, text in assessment.sections)
    if (len(raw.encode()) > 262144 or sum(len(text.encode()) for _, text in sections) > 262144):
        return refused("reviewed_context_exceeds_scope")
    # A correction must not create a fresh ambiguity across the replacement
    # boundary. Only exact spans explicitly confirmed as visible may retain it.
    confirmed = []
    shift = 0
    for binding in assessment.affected_sections:
        review, text = selected[binding.section]
        if review.action == "confirm_visible_text":
            confirmed.append((binding.start + shift, binding.start + shift + len(text)))
        shift += len(text) - (binding.end - binding.start)
    if any(not any(start <= match.start() and match.end() <= end for start, end in confirmed)
           for match in _LINE_END.finditer(raw)):
        return refused("reviewed_context_contains_new_ambiguity")
    versions = [{"record_id": row.record_id, "evidence_id": row.evidence_id,
        "evidence_fingerprint": row.evidence_fingerprint, "section": row.section,
        "original_sha256": row.original_section_sha256, "approved_sha256": row.approved_text_sha256,
        "action": row.action} for row, _ in (selected[name] for name in sorted(selected))]
    version = _fingerprint({"version": VERSION, "assessment": assessment.fingerprint, "reviews": versions})
    context = _fingerprint({"version": version, "raw_sha256": _sha(raw.encode()),
                            "sections": [(name, _sha(text.encode())) for name, text in sections]})
    if expected_context_fingerprint is not None and context != expected_context_fingerprint:
        raise SourceTranscriptionChanged()
    return ResolvedTranscription("ready", None, raw, sections, assessment.fingerprint,
                                 version, context, assessment.issue_count == 0)
