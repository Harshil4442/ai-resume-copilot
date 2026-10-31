"""Pure review/context contracts; synthetic records are not persisted consent."""
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest
from backend.app.domains.career.schemas import evidence_snapshot_fingerprint
from backend.app.services import source_transcription as transcription
from reportlab.pdfgen.canvas import Canvas

ORIGINAL = "Developed clear docu-\nmented Python service records."
CORRECTED = "Developed clear documented Python service records."
PREFIX = "Morgan River\nEXPERIENCE\n"
SUFFIX = "\nPROJECTS\nBuilt seven independent API checks.\nEDUCATION\nNorth College degree."


def assessment(*, body=ORIGINAL, source=b"immutable synthetic source", kind="pdf"):
    return transcription.assess_source(user_id=1, resume_id=3, source_bytes=source,
        source_format=kind, raw_text=PREFIX + body + SUFFIX,
        sections={"other": "Morgan River", "experience": body,
            "projects": "Built seven independent API checks.", "education": "North College degree."})


def evidence(text=CORRECTED, *, section="experience", identifier="evd-reviewed"):
    return SimpleNamespace(id=identifier, user_id=1, resume_id=3, category=section,
        title=section.title(), evidence_text=text, metrics={}, skills=["Python"],
        provenance="resume_extraction", source_ref="resume:3:section:" + section,
        approval_state="approved", confidence="extracted",
        created_at=datetime(2026, 10, 30), updated_at=datetime(2026, 10, 30, 1))


def synthetic_record(source, row, *, section="experience", action="correct_transcription"):
    """Emulate a future trusted persisted-record loader; no persistence is made."""
    return transcription.ExplicitTranscriptionReview(record_id="review:" + row.id,
        user_id=1, resume_id=3, source_sha256=source.source_sha256, section=section,
        original_section_sha256=sha256(dict(source.sections)[section].encode()).hexdigest(),
        approved_text_sha256=sha256(row.evidence_text.encode()).hexdigest(),
        evidence_id=row.id, evidence_fingerprint=evidence_snapshot_fingerprint(row), action=action)


def test_real_synthetic_native_pdf_extraction_requires_review_without_guessing_a_word():
    from backend.app.services.parsing import parse_resume_file

    output = BytesIO()
    canvas = Canvas(output, invariant=True, pageCompression=0)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(48, 720, "EXPERIENCE")
    canvas.setFont("Helvetica", 11)
    canvas.drawString(48, 690, "Developed clear docu-")
    canvas.drawString(48, 670, "mented Python service records.")
    canvas.save()
    source = output.getvalue()
    assert b"ActualText" not in source
    raw, sections, *_ = parse_resume_file(source, "synthetic.pdf", use_llm=False)
    value = transcription.assess_source(user_id=1, resume_id=3, source_bytes=source,
        source_format="pdf", raw_text=raw, sections=sections)
    assert value.issue_count == 1 and "docu-\nmented" in value.raw_text
    result = transcription.resolve_transcription(value, [])
    assert result.status == "needs_review" and result.raw_text is None
    assert not result.physical_edit_compatible


@pytest.mark.parametrize("text", [ORIGINAL, CORRECTED])
def test_legacy_approved_evidence_does_not_create_a_new_readability_attestation(text):
    result = transcription.resolve_transcription(assessment(), [evidence(text)])
    assert result.status == "needs_review" and result.reason == "explicit_transcription_review_required"
    assert result.context_fingerprint is None and result.raw_text is None


def test_explicit_corrected_approved_section_preserves_every_other_byte_and_stales_old_quotes():
    source, row = assessment(), evidence()
    original_bytes = source.source_bytes
    record = synthetic_record(source, row)
    result = transcription.resolve_transcription(source, [row], (record,))
    assert result.status == "ready" and result.raw_text == PREFIX + CORRECTED + SUFFIX
    assert dict(result.sections) == {**dict(source.sections), "experience": CORRECTED}
    assert source.source_bytes == original_bytes and source.raw_text == PREFIX + ORIGINAL + SUFFIX
    assert result.review_version_fingerprint and result.context_fingerprint
    assert not result.physical_edit_compatible  # Logical review never maps physical PDF edit units.
    assert transcription.resolve_transcription(source, [row], (record,),
        expected_context_fingerprint=result.context_fingerprint) == result
    row.updated_at += timedelta(microseconds=1)
    assert transcription.resolve_transcription(source, [row], (record,)).status == "needs_review"
    with pytest.raises(transcription.SourceTranscriptionChanged):
        transcription.resolve_transcription(source, [row], (record,),
            expected_context_fingerprint=result.context_fingerprint)


def test_explicit_literal_confirmation_does_not_join_or_authorize_native_tailoring():
    source, row = assessment(), evidence(ORIGINAL)
    record = synthetic_record(source, row, action="confirm_visible_text")
    result = transcription.resolve_transcription(source, [row], (record,))
    assert result.status == "ready" and result.raw_text == source.raw_text
    assert "docu-\nmented" in result.raw_text and not result.physical_edit_compatible


@pytest.mark.parametrize("change", ["approval", "text", "timestamp", "skills", "source_ref", "owner", "resume"])
def test_changed_evidence_cannot_reuse_an_explicit_review(change):
    source, row = assessment(), evidence()
    record = synthetic_record(source, row)
    updates = {"approval": ("approval_state", "pending"), "text": ("evidence_text", "A different fact."),
        "timestamp": ("updated_at", row.updated_at + timedelta(microseconds=1)),
        "skills": ("skills", ["SQL"]), "source_ref": ("source_ref", "different-source"),
        "owner": ("user_id", 2), "resume": ("resume_id", 4)}
    setattr(row, *updates[change])
    result = transcription.resolve_transcription(source, [row], (record,))
    assert result.status == "needs_review" and result.raw_text is None


@pytest.mark.parametrize("field,value", [("source_sha256", "0" * 64),
    ("original_section_sha256", "0" * 64), ("approved_text_sha256", "0" * 64),
    ("evidence_fingerprint", "0" * 64), ("user_id", 2), ("resume_id", 4),
    ("section", "projects"), ("evidence_id", "missing-row"), ("record_id", ""),
    ("action", "implicit_legacy_approval"), ("action", [])])
def test_wrong_explicit_review_bindings_cannot_release_context(field, value):
    source, row = assessment(), evidence()
    record = replace(synthetic_record(source, row), **{field: value})
    result = transcription.resolve_transcription(source, [row], (record,))
    assert result.status == "needs_review" and result.context_fingerprint is None


def test_changed_source_bytes_or_section_text_stops_a_previously_quoted_context():
    source, row = assessment(), evidence()
    record = synthetic_record(source, row)
    result = transcription.resolve_transcription(source, [row], (record,))
    for changed in (assessment(source=b"different immutable source"), assessment(body=ORIGINAL + " ")):
        assert transcription.resolve_transcription(changed, [row], (record,)).status == "needs_review"
        with pytest.raises(transcription.SourceTranscriptionChanged):
            transcription.resolve_transcription(changed, [row], (record,),
                expected_context_fingerprint=result.context_fingerprint)


def test_assessment_fields_cannot_be_changed_to_erase_ambiguity():
    source = assessment()
    forged = replace(source, affected_sections=(), issue_count=0)
    result = transcription.resolve_transcription(forged, [])
    assert result.status == "needs_review" and result.reason == "assessment_binding_changed"


@pytest.mark.parametrize("mode", ["duplicate_review", "duplicate_evidence", "partial_review"])
def test_review_selection_never_chooses_arbitrarily_or_omits_an_affected_section(mode):
    source, row = assessment(), evidence()
    record = synthetic_record(source, row)
    rows, records = [row], (record,)
    if mode == "duplicate_review":
        records += (replace(record, record_id="second-record"),)
    elif mode == "duplicate_evidence":
        rows.append(row)
    else:
        source = transcription.assess_source(user_id=1, resume_id=3, source_bytes=b"source",
            source_format="pdf", raw_text="EXPERIENCE\n" + ORIGINAL + "\nPROJECTS\nBuilt inde-\npendent checks.",
            sections={"experience": ORIGINAL, "projects": "Built inde-\npendent checks."})
        record = synthetic_record(source, row)
        records = (record,)
    result = transcription.resolve_transcription(source, rows, records)
    assert result.status == "needs_review" and result.raw_text is None


@pytest.mark.parametrize("mode", ["duplicate_locations", "unmapped_section", "too_many_issues", "oversized_section"])
def test_unreviewable_source_requires_existing_editable_source_path_without_truncation(mode):
    source = assessment()
    if mode == "duplicate_locations":
        raw, sections = ORIGINAL + "\n" + ORIGINAL, {"experience": ORIGINAL}
    elif mode == "unmapped_section":
        raw, sections = ORIGINAL, {"experience": "Different extracted view"}
    elif mode == "too_many_issues":
        raw = "\n".join([ORIGINAL] * 257)
        sections = {"experience": raw}
    else:
        raw = "X" * 20001 + "\n" + ORIGINAL
        sections = {"experience": raw}
    source = transcription.assess_source(user_id=1, resume_id=3, source_bytes=source.source_bytes,
        source_format="pdf", raw_text=raw, sections=sections)
    result = transcription.resolve_transcription(source, [])
    assert result.status == "editable_source_required" and result.raw_text is None
    assert source.raw_text == raw and dict(source.sections) == sections
    if mode == "too_many_issues":
        assert source.issue_count == 257


@pytest.mark.parametrize("kind", ["pdf", "docx", "tex", "texzip"])
def test_no_format_only_implicit_repair_or_review_bypass(kind):
    source = assessment(kind=kind)
    assert source.issue_count == 1 and transcription.resolve_transcription(source, []).status == "needs_review"


@pytest.mark.parametrize("text", ["Built event-driven API-14 tools.", "Employment: 2020 -\n2022.",
    "Kept ordinary source text.\nRetained the next line.", "X" * 20000])
def test_unambiguous_text_and_same_line_hyphens_are_exactly_preserved(text):
    source = assessment(body=text)
    result = transcription.resolve_transcription(source, [])
    assert source.issue_count == 0 and result.status == "ready"
    assert result.raw_text == source.raw_text and result.sections == source.sections
    assert result.physical_edit_compatible  # Existing independent native validators remain required.


def test_review_identity_changes_context_even_when_the_approved_text_is_identical():
    source, row = assessment(), evidence()
    record = synthetic_record(source, row)
    first = transcription.resolve_transcription(source, [row], (record,))
    second = transcription.resolve_transcription(source, [row], (replace(record, record_id="new-explicit-action"),))
    assert first.raw_text == second.raw_text and first.context_fingerprint != second.context_fingerprint
    assert first.review_version_fingerprint != second.review_version_fingerprint


def test_review_action_cannot_hide_a_new_unresolved_word_fragment():
    source, row = assessment(), evidence(ORIGINAL + " Still ambig-\nuous.")
    record = synthetic_record(source, row)
    result = transcription.resolve_transcription(source, [row], (record,))
    assert result.status == "needs_review" and result.reason == "review_action_mismatch"


def test_ambiguous_section_view_missing_from_raw_cannot_release_to_ai():
    source = transcription.assess_source(user_id=1, resume_id=3, source_bytes=b"source",
        source_format="pdf", raw_text="Native raw text differs from the section view.",
        sections={"experience": ORIGINAL})
    result = transcription.resolve_transcription(source, [])
    assert result.status == "editable_source_required" and result.raw_text is None


@pytest.mark.parametrize("text", ["Used ISO9001docu-\nmentation records.", "Handled docu-\nmented2 cases."])
def test_adjacent_digits_cannot_hide_an_alphabetic_line_end_fragment(text):
    source = assessment(body=text)
    assert source.issue_count == 1
    assert transcription.resolve_transcription(source, []).status == "needs_review"


def test_correction_cannot_create_unreviewed_line_end_ambiguity_at_section_boundary():
    source, row = assessment(), evidence("Approved a replacement ending in a fragment-")
    record = synthetic_record(source, row)
    result = transcription.resolve_transcription(source, [row], (record,))
    assert result.status == "needs_review" and result.raw_text is None
    assert result.reason == "reviewed_context_contains_new_ambiguity"


def test_full_context_overflow_refuses_without_truncating_approved_or_original_text():
    original_raw = "P" * 250000 + "\n" + ORIGINAL
    source = transcription.assess_source(user_id=1, resume_id=3, source_bytes=b"source",
        source_format="pdf", raw_text=original_raw, sections={"experience": ORIGINAL})
    row = evidence("A complete explicit correction. " + "X" * 19000)
    record = synthetic_record(source, row)
    result = transcription.resolve_transcription(source, [row], (record,))
    assert result.status == "needs_review" and result.raw_text is None
    assert result.reason == "reviewed_context_exceeds_scope"
    assert source.raw_text == original_raw and len(row.evidence_text) > 19000
