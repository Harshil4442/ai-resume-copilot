import json
from types import SimpleNamespace

import pytest
from backend.app.domains.analysis import tasks
from backend.app.services import llm_client
from google import genai


def _tailoring_input():
    original = "Developed Python services and reduced response time by 20%."
    return {
        "job_title": "Platform Engineer",
        "jd_text": "Build reliable Python services with measurable performance.",
        "approved_evidence": [{"id": "evd_python", "title": "API work", "text": original}],
        "source_units": [{"unit_id": "source-body", "text": original, "max_chars": len(original)}],
    }


def _tailoring_edit():
    return {
        "unit_id": "source-body",
        "original_text": _tailoring_input()["source_units"][0]["text"],
        "replacement_text": "Built Python services and reduced response time by 20%.",
        "evidence_ids": ["evd_python"],
        "reason": "Emphasizes implementation and performance relevant to the role.",
    }


def test_tailoring_returns_source_replacements_without_rebuilding_sections(monkeypatch):
    calls = []

    def response(messages):
        calls.append(messages)
        return f"```json\n{json.dumps({'source_edits': [_tailoring_edit()], 'evidence_needed': []})}\n```"

    monkeypatch.setattr(llm_client, "_chat", response)
    result = llm_client.tailor_resume_from_evidence(**_tailoring_input(), repair_note="Use a shorter replacement")
    assert result["source_edits"] == [_tailoring_edit()]
    assert "bullets" not in result and "summary_items" not in result and "skills" not in result
    assert result["evidence_policy"] == "approved_only"
    assert len(calls) == 1
    assert "Use a shorter replacement" in calls[0][1]["content"]
    assert "source-body" in calls[0][1]["content"]


@pytest.mark.parametrize(
    "leading,trailing,provider_leading,provider_trailing",
    [(" ", "", "", ""), ("", " ", "", ""), ("  ", "   ", " ", " "), ("", "", " ", " ")],
)
def test_tailoring_retains_exact_source_fragment_boundary_spaces(
    monkeypatch, leading, trailing, provider_leading, provider_trailing
):
    inputs = _tailoring_input()
    original = leading + inputs["source_units"][0]["text"] + trailing
    replacement = _tailoring_edit()["replacement_text"]
    source_unit = {
        "unit_id": "source-body",
        "text": original,
        "max_chars": len(original),
        "line_context": f"Earlier fragment {original} later fragment",
        "allowed_characters": "".join(sorted(set(original + replacement))),
    }
    inputs["source_units"] = [source_unit]
    edit = {
        **_tailoring_edit(),
        "original_text": original,
        "replacement_text": provider_leading + replacement + provider_trailing,
    }
    calls = []

    def chat(messages):
        calls.append(messages)
        return json.dumps({"source_edits": [edit]})

    monkeypatch.setattr(llm_client, "_chat", chat)
    result = llm_client.tailor_resume_from_evidence(**inputs)

    assert result["source_edits"] == [{**edit, "replacement_text": leading + replacement + trailing}]
    assert len(calls) == 1
    supplied_units = json.loads(calls[0][1]["content"].split("EDITABLE SOURCE UNITS:\n", 1)[1])
    assert supplied_units == [{
        **source_unit,
        "max_body_chars": len(original.strip()),
        "target_body_chars": len(original.strip()),
    }]


def test_tailoring_counts_source_boundary_spaces_against_the_fragment_length(monkeypatch):
    inputs = _tailoring_input()
    original = "  Built reliable Python services for customers.  "
    replacement = "Created reliable Python services for customers."
    inputs["source_units"][0].update(text=original, max_chars=len(original))
    inputs["approved_evidence"][0]["text"] = original
    edit = {**_tailoring_edit(), "original_text": original, "replacement_text": replacement}
    assert len(replacement) <= len(original)
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))

    with pytest.raises(llm_client.TailoringOutputError, match="including boundary spacing") as error:
        llm_client.tailor_resume_from_evidence(**inputs)

    assert error.value.unit_id == "source-body"


@pytest.mark.parametrize(
    "replacement,unavailable_character",
    [
        ("Built Python services and reduced response time by 20%.", "B"),
        ("Built Python services; reduced response time by 20%.", ";"),
    ],
)
def test_tailoring_rejects_grounded_text_with_unavailable_subset_font_characters(
    monkeypatch, replacement, unavailable_character
):
    inputs = _tailoring_input()
    original = inputs["source_units"][0]["text"]
    inputs["source_units"][0]["allowed_characters"] = "".join(
        sorted(set(original + replacement) - {unavailable_character})
    )
    assert unavailable_character not in original
    edit = {**_tailoring_edit(), "replacement_text": replacement}
    calls = []

    def chat(messages):
        calls.append(messages)
        return json.dumps({"source_edits": [edit]})

    monkeypatch.setattr(llm_client, "_chat", chat)
    with pytest.raises(llm_client.TailoringOutputError, match="characters available in the source font") as error:
        llm_client.tailor_resume_from_evidence(**inputs)

    assert error.value.unit_id == "source-body"
    assert len(calls) == 1


def test_tailoring_line_context_does_not_authorize_new_facts_in_a_fragment(monkeypatch):
    inputs = _tailoring_input()
    inputs["source_units"][0]["line_context"] = "Developed Python services alongside a Java platform."
    edit = {**_tailoring_edit(), "replacement_text": "Built Java services and reduced response time by 20%."}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))

    with pytest.raises(llm_client.TailoringOutputError, match="factual skill or named term") as error:
        llm_client.tailor_resume_from_evidence(**inputs)

    assert error.value.unit_id == "source-body"


@pytest.mark.parametrize(
    "updates",
    [
        {"unit_id": "foreign-source"},
        {"original_text": "A different resume passage"},
        {"replacement_text": ""},
        {"replacement_text": "Invented a huge platform and reduced latency by 99%."},
        {"replacement_text": "Built Python services and reduced response time by 2%."},
        {"replacement_text": "Built Python services and reduced response time by 20%.\n"},
        {"replacement_text": _tailoring_input()["source_units"][0]["text"]},
        {"evidence_ids": ["evd_foreign"]},
        {"evidence_ids": []},
        {"reason": ""},
    ],
)
def test_tailoring_rejects_unsafe_or_unsupported_replacements(monkeypatch, updates):
    edit = {**_tailoring_edit(), **updates}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    with pytest.raises(llm_client.TailoringOutputError):
        llm_client.tailor_resume_from_evidence(**_tailoring_input())


@pytest.mark.parametrize("output", ["Unreadable output", '{"source_edits": []}', "null", "[]", None])
def test_tailoring_rejects_missing_edit_output_without_a_placeholder(monkeypatch, output):
    monkeypatch.setattr(llm_client, "_chat", lambda messages: output)
    with pytest.raises(llm_client.TailoringOutputError):
        llm_client.tailor_resume_from_evidence(**_tailoring_input())


def test_tailoring_rejects_duplicate_locations(monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [_tailoring_edit()] * 2}))
    with pytest.raises(llm_client.TailoringOutputError):
        llm_client.tailor_resume_from_evidence(**_tailoring_input())


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"unit_id": "untrusted-model-identity"}, "invalid_source_location"),
        ({"original_text": "private model source mismatch"}, "source_text_mismatch"),
        ({"replacement_text": None}, "invalid_replacement_text"),
        ({"replacement_text": ""}, "invalid_replacement_text"),
        (
            {"replacement_text": "private model replacement that is much too long " * 4},
            "invalid_replacement_length",
        ),
        (
            {"replacement_text": _tailoring_input()["source_units"][0]["text"]},
            "unchanged_source_text",
        ),
        (
            {"replacement_text": "Built Python services and reduced response time by 2%."},
            "changed_source_numbers",
        ),
        (
            {"replacement_text": "Built Python services and reduced response time by 20%.\n"},
            "invalid_replacement_text",
        ),
        (
            {"replacement_text": "Built Python services and reduced response time by 20%.\x7f"},
            "invalid_replacement_text",
        ),
        (
            {"replacement_text": "Built Java services and reduced response time by 20%."},
            "unsupported_factual_term",
        ),
        ({"evidence_ids": ["untrusted-evidence-id"]}, "unsupported_evidence"),
        ({"evidence_ids": []}, "unsupported_evidence"),
        ({"reason": ""}, "missing_edit_reason"),
    ],
)
@pytest.mark.parametrize("invalid_first", [False, True])
def test_tailoring_keeps_safe_replacement_when_another_proposal_is_invalid(
    monkeypatch, updates, reason, invalid_first
):
    inputs = _tailoring_input()
    inputs["source_units"].append({**inputs["source_units"][0], "unit_id": "other-body"})
    valid = _tailoring_edit()
    invalid = {**valid, "unit_id": "other-body", **updates}
    proposals = [invalid, valid] if invalid_first else [valid, invalid]
    monkeypatch.setattr(
        llm_client, "_chat", lambda messages: json.dumps({"source_edits": proposals})
    )

    result = llm_client.tailor_resume_from_evidence(**inputs)

    assert result["source_edits"] == [valid]
    rejected = result["rejected_source_edits"]
    assert len(rejected) == 1
    assert rejected[0]["unit_id"] == (None if reason == "invalid_source_location" else "other-body")
    assert rejected[0]["reason"] == reason
    assert rejected[0]["repair_hint"] and len(rejected[0]["repair_hint"]) <= 350
    serialized = json.dumps(rejected)
    assert "private model" not in serialized
    assert "untrusted-model-identity" not in serialized
    assert "untrusted-evidence-id" not in serialized
    assert valid["original_text"] not in serialized
    assert valid["replacement_text"] not in serialized


@pytest.mark.parametrize("invalid", [None, "not an object"])
def test_tailoring_keeps_safe_replacement_beside_nonobject_proposal(monkeypatch, invalid):
    valid = _tailoring_edit()
    monkeypatch.setattr(
        llm_client, "_chat", lambda messages: json.dumps({"source_edits": [invalid, valid]})
    )
    result = llm_client.tailor_resume_from_evidence(**_tailoring_input())
    assert result["source_edits"] == [valid]
    assert result["rejected_source_edits"][0]["reason"] == "invalid_edit_item"
    assert result["rejected_source_edits"][0]["unit_id"] is None


def test_tailoring_keeps_safe_replacement_beside_missing_subset_glyph(monkeypatch):
    inputs = _tailoring_input()
    original = inputs["source_units"][0]["text"]
    valid = _tailoring_edit()
    inputs["source_units"].append(
        {
            "unit_id": "other-body",
            "text": original,
            "allowed_characters": "".join(
                sorted(set(original + valid["replacement_text"]) - {"B"})
            ),
        }
    )
    unavailable = {**valid, "unit_id": "other-body"}
    monkeypatch.setattr(
        llm_client, "_chat", lambda messages: json.dumps({"source_edits": [unavailable, valid]})
    )
    result = llm_client.tailor_resume_from_evidence(**inputs)
    assert result["source_edits"] == [valid]
    assert result["rejected_source_edits"][0]["reason"] == "missing_pdf_glyph"


def test_tailoring_drops_ambiguous_duplicate_unit_but_keeps_unrelated_safe_edit(monkeypatch):
    inputs = _tailoring_input()
    inputs["source_units"].append({**inputs["source_units"][0], "unit_id": "other-body"})
    duplicate = _tailoring_edit()
    independent = {**duplicate, "unit_id": "other-body"}
    monkeypatch.setattr(
        llm_client,
        "_chat",
        lambda messages: json.dumps({"source_edits": [duplicate, independent, duplicate]}),
    )
    result = llm_client.tailor_resume_from_evidence(**inputs)
    assert result["source_edits"] == [independent]
    assert [item["reason"] for item in result["rejected_source_edits"]] == [
        "duplicate_source_location"
    ] * 2


def test_tailoring_normalizes_excess_provider_outer_spaces_before_length(monkeypatch):
    inputs = _tailoring_input()
    original = " " + inputs["source_units"][0]["text"] + "  "
    inputs["source_units"][0]["text"] = original
    valid = {**_tailoring_edit(), "original_text": original}
    proposal = {**valid, "replacement_text": " " * 20 + valid["replacement_text"] + " " * 20}
    assert len(proposal["replacement_text"]) > len(original)
    monkeypatch.setattr(
        llm_client, "_chat", lambda messages: json.dumps({"source_edits": [proposal]})
    )
    result = llm_client.tailor_resume_from_evidence(**inputs)
    assert result["source_edits"] == [
        {**valid, "replacement_text": " " + valid["replacement_text"] + "  "}
    ]
    assert result["rejected_source_edits"] == []


def test_tailoring_all_invalid_prefers_actionable_length_error_over_noops(monkeypatch):
    inputs = _tailoring_input()
    original = inputs["source_units"][0]["text"]
    inputs["source_units"].extend(
        [
            {"unit_id": "other-body", "text": original},
            {"unit_id": "long-body", "text": original},
        ]
    )
    unchanged = {**_tailoring_edit(), "replacement_text": original}
    overlength = {**_tailoring_edit(), "unit_id": "long-body", "replacement_text": original + "!"}
    proposals = [unchanged, {**unchanged, "unit_id": "other-body"}, overlength]
    monkeypatch.setattr(
        llm_client, "_chat", lambda messages: json.dumps({"source_edits": proposals})
    )
    with pytest.raises(llm_client.TailoringOutputError) as error:
        llm_client.tailor_resume_from_evidence(**inputs)
    assert error.value.unit_id == "long-body"
    assert error.value.reason_code == "invalid_replacement_length"
    assert f"Original final length is {len(original)}" in error.value.repair_hint
    assert f"proposed final length is {len(original) + 1}" in error.value.repair_hint
    assert "Shorten by at least 1 character" in error.value.repair_hint
    assert original not in error.value.repair_hint


@pytest.mark.parametrize("outer_spaces", [False, True])
def test_tailoring_all_noops_never_returns_an_unchanged_version(monkeypatch, outer_spaces):
    inputs = _tailoring_input()
    original = inputs["source_units"][0]["text"]
    replacement = " " + original + " " if outer_spaces else original
    noop = {**_tailoring_edit(), "replacement_text": replacement}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [noop]}))
    with pytest.raises(llm_client.TailoringOutputError) as error:
        llm_client.tailor_resume_from_evidence(**inputs)
    assert error.value.reason_code == "unchanged_source_text"
    assert error.value.unit_id == "source-body"
    assert "Choose a different useful passage" in error.value.repair_hint


@pytest.mark.parametrize("count", [0, 13])
def test_tailoring_keeps_strict_raw_proposal_count(monkeypatch, count):
    monkeypatch.setattr(
        llm_client,
        "_chat",
        lambda messages: json.dumps({"source_edits": [_tailoring_edit()] * count}),
    )
    with pytest.raises(llm_client.TailoringOutputError) as error:
        llm_client.tailor_resume_from_evidence(**_tailoring_input())
    assert error.value.reason_code == "invalid_edit_count"
    assert "actual changed" in error.value.repair_hint


def _fragment_tailoring_input():
    original = "for ARM architecture validation, enabling "
    inputs = _tailoring_input()
    inputs["source_units"] = [{
        "unit_id": "source-fragment",
        "text": original,
        "required_prefix": "for ARM",
        "required_suffix": "validation, enabling",
        "line_context": original + "100+ engineers to test reliably.",
    }]
    inputs["approved_evidence"][0]["text"] = original + "100+ engineers to test reliably."
    edit = {**_tailoring_edit(), "unit_id": "source-fragment", "original_text": original}
    return inputs, edit


@pytest.mark.parametrize(
    "replacement",
    [
        "for ARM validation, enabling reliability ",
        "to ARM architecture validation, enabling ",
        "For ARM architecture validation, enabling ",
        "for ARM architecture validation enabling ",
        "for ARMament validation, enabling ",
    ],
)
def test_tailoring_rejects_changes_to_readonly_fragment_transitions(monkeypatch, replacement):
    inputs, edit = _fragment_tailoring_input()
    assert len(replacement) <= len(edit["original_text"])
    proposal = {**edit, "replacement_text": replacement}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [proposal]}))
    with pytest.raises(llm_client.TailoringOutputError) as error:
        llm_client.tailor_resume_from_evidence(**inputs)
    assert error.value.reason_code == "changed_fragment_boundary"
    assert error.value.unit_id == "source-fragment"
    assert "required_prefix and required_suffix" in error.value.repair_hint
    assert "for ARM" not in error.value.repair_hint
    assert "validation, enabling" not in error.value.repair_hint


@pytest.mark.parametrize("provider_text", ["for ARM validation, enabling", "  for  ARM validation, enabling   "])
def test_tailoring_keeps_interior_fragment_edits_and_exact_source_outer_spaces(monkeypatch, provider_text):
    inputs, edit = _fragment_tailoring_input()
    calls = []

    def chat(messages):
        calls.append(messages)
        return json.dumps({"source_edits": [{**edit, "replacement_text": provider_text}]})

    monkeypatch.setattr(llm_client, "_chat", chat)
    result = llm_client.tailor_resume_from_evidence(**inputs)
    assert result["source_edits"] == [{**edit, "replacement_text": provider_text.strip() + " "}]
    assert result["rejected_source_edits"] == []
    assert "required_prefix or required_suffix" in calls[0][0]["content"]


def test_tailoring_retains_unrelated_safe_edit_when_fragment_transition_is_unsafe(monkeypatch):
    fragment_inputs, fragment = _fragment_tailoring_input()
    inputs = _tailoring_input()
    inputs["source_units"].extend(fragment_inputs["source_units"])
    inputs["approved_evidence"][0]["text"] += " " + fragment_inputs["approved_evidence"][0]["text"]
    invalid = {**fragment, "replacement_text": "for ARM validation, enabling reliability "}
    valid = _tailoring_edit()
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [invalid, valid]}))
    result = llm_client.tailor_resume_from_evidence(**inputs)
    assert result["source_edits"] == [valid]
    assert result["rejected_source_edits"] == [{
        "unit_id": "source-fragment",
        "reason": "changed_fragment_boundary",
        "repair_hint": "Keep required_prefix and required_suffix token sequences unchanged at their respective boundaries. Edit only interior wording or choose another passage.",
    }]


def test_tailoring_complete_summary_without_anchors_can_change_boundary_words(monkeypatch):
    inputs = _tailoring_input()
    inputs["source_units"][0]["section"] = "summary"
    inputs["source_units"][0]["required_prefix"] = ""
    inputs["source_units"][0]["required_suffix"] = ""
    valid = {**_tailoring_edit(), "replacement_text": "Built Python services and cut response time by 20%."}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [valid]}))
    assert llm_client.tailor_resume_from_evidence(**inputs)["source_edits"] == [valid]


@pytest.mark.parametrize("kind", ["text_object", "paragraph", None])
def test_tailoring_supplies_pdf_width_margin_without_restricting_docx_or_changing_hard_limit(
    monkeypatch, kind
):
    inputs = _tailoring_input()
    original = " " + inputs["source_units"][0]["text"] + "  "
    source_unit = {"unit_id": "source-body", "text": original, "max_chars": len(original)}
    if kind is not None:
        source_unit["kind"] = kind
    original_unit = dict(source_unit)
    inputs["source_units"] = [source_unit]
    valid = {**_tailoring_edit(), "original_text": original}
    calls = []

    def chat(messages):
        calls.append(messages)
        return json.dumps({"source_edits": [valid]})

    monkeypatch.setattr(llm_client, "_chat", chat)
    result = llm_client.tailor_resume_from_evidence(**inputs)
    body_chars = len(original.strip())
    target_chars = int(body_chars * 0.85) if kind == "text_object" else body_chars
    units = json.loads(calls[0][1]["content"].split("EDITABLE SOURCE UNITS:\n", 1)[1])
    assert units == [{**source_unit, "max_body_chars": body_chars, "target_body_chars": target_chars}]
    assert inputs["source_units"] == [original_unit]
    # This valid proposal exceeds the PDF soft target; only the original hard
    # length and downstream native font/layout proof may reject it.
    assert len(valid["replacement_text"]) > int(body_chars * 0.85)
    assert result["source_edits"] == [{**valid, "replacement_text": " " + valid["replacement_text"] + "  "}]
    system = calls[0][0]["content"]
    assert "soft length target" in system
    assert "at least 15% shorter" in system
    assert "nearly the same character count can be wider" in system
    assert "three to six independent useful changes" in system


@pytest.mark.parametrize("invented_term", ["C++", "Java", "java", "Kubernetes", "CloudNova", "Acme", "CISSP", "ZPX", "PhD"])
def test_tailoring_rejects_factual_terms_found_only_in_the_job(monkeypatch, invented_term):
    inputs = _tailoring_input()
    inputs["jd_text"] = f"The employer requests {invented_term} experience."
    edit = {**_tailoring_edit(), "replacement_text": f"Built {invented_term} services and reduced response time by 20%."}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    with pytest.raises(llm_client.TailoringOutputError, match="factual skill or named term"):
        llm_client.tailor_resume_from_evidence(**inputs)


@pytest.mark.parametrize("support_location", ["unreferenced-evidence", "other-source-unit"])
def test_tailoring_does_not_borrow_factual_support_from_unrelated_context(monkeypatch, support_location):
    inputs = _tailoring_input()
    if support_location == "unreferenced-evidence":
        inputs["approved_evidence"].append({"id": "evd_java", "text": "Built Java services.", "skills": ["Java"]})
    else:
        inputs["source_units"].append({"unit_id": "other-project", "text": "Built Java services."})
    edit = {**_tailoring_edit(), "replacement_text": "Built Java services and reduced response time by 20%."}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    with pytest.raises(llm_client.TailoringOutputError, match="factual skill or named term"):
        llm_client.tailor_resume_from_evidence(**inputs)


@pytest.mark.parametrize("support_field", ["text", "skills"])
def test_tailoring_accepts_new_factual_terms_from_the_cited_evidence(monkeypatch, support_field):
    inputs = _tailoring_input()
    evidence = {"id": "evd_java", "text": "Developed customer services."}
    evidence[support_field] = "Developed Java services." if support_field == "text" else ["Java"]
    inputs["approved_evidence"].append(evidence)
    edit = {**_tailoring_edit(), "replacement_text": "Built Java services and reduced response time by 20%.", "evidence_ids": ["evd_python", "evd_java"]}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    result = llm_client.tailor_resume_from_evidence(**inputs)
    assert result["source_edits"] == [edit]


@pytest.mark.parametrize("source_term,replacement_term", [("JavaScript", "Java"), ("C", "C++"), ("C++", "C#"), ("MySQL", "SQL")])
def test_tailoring_factual_skill_support_uses_complete_technical_tokens(monkeypatch, source_term, replacement_term):
    inputs = _tailoring_input()
    original = inputs["source_units"][0]["text"].replace("Python", source_term)
    inputs["source_units"][0]["text"] = original
    inputs["approved_evidence"][0]["text"] = original
    edit = {**_tailoring_edit(), "original_text": original, "replacement_text": f"Built {replacement_term} services and reduced response time by 20%."}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    with pytest.raises(llm_client.TailoringOutputError, match="factual skill or named term"):
        llm_client.tailor_resume_from_evidence(**inputs)


def test_tailoring_accepts_grounded_aliases_and_normal_action_verb_paraphrases(monkeypatch):
    inputs = _tailoring_input()
    original = "Developed reliable postgres services for global customers and improved response time."
    inputs["source_units"][0]["text"] = original
    inputs["approved_evidence"][0]["text"] = original
    edit = {**_tailoring_edit(), "original_text": original, "replacement_text": "Built PostgreSQL services for customers and improved response time."}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    result = llm_client.tailor_resume_from_evidence(**inputs)
    assert result["source_edits"] == [edit]


@pytest.mark.parametrize(
    "source_term,replacement_term",
    [("APIs", "APIs"), ("APIs", "API"), ("API", "APIs"), ("SOPs", "SOPs"),
     ("SDKs", "SDK"), ("JWTs", "JWT"), ("JWT", "JWTs"), ("SQLs", "SQL"),
     ("CloudNovaSDKs", "CloudNovaSDKs")],
)
def test_tailoring_accepts_grounded_complete_acronym_and_original_brand_tokens(
    monkeypatch, source_term, replacement_term
):
    original = f"Developed reliable {source_term} for customers and improved system performance."
    replacement = f"Built reliable {replacement_term} for customers and improved system performance."
    inputs = _tailoring_input()
    inputs["source_units"][0]["text"] = original
    inputs["approved_evidence"][0]["text"] = original
    edit = {**_tailoring_edit(), "original_text": original, "replacement_text": replacement}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    assert llm_client.tailor_resume_from_evidence(**inputs)["source_edits"] == [edit]


@pytest.mark.parametrize("support_field", ["text", "skills"])
def test_tailoring_accepts_acronym_plural_support_from_only_the_cited_evidence(monkeypatch, support_field):
    inputs = _tailoring_input()
    if support_field == "text":
        inputs["approved_evidence"][0]["text"] += " Built reliable APIs."
    else:
        inputs["approved_evidence"][0]["skills"] = ["APIs"]
    edit = {**_tailoring_edit(), "replacement_text": "Built API services and reduced response time by 20%."}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    assert llm_client.tailor_resume_from_evidence(**inputs)["source_edits"] == [edit]


@pytest.mark.parametrize("source_term,replacement_term", [("AWS", "AW"), ("APIsExtra", "API"), ("MySQL", "SQLs"), ("API", "ZPXs")])
def test_tailoring_acronym_plural_support_does_not_match_other_complete_tokens(monkeypatch, source_term, replacement_term):
    original = f"Developed reliable {source_term} services for customers and improved performance."
    replacement = f"Built reliable {replacement_term} services for customers and improved performance."
    inputs = _tailoring_input()
    inputs["source_units"][0]["text"] = original
    inputs["approved_evidence"][0]["text"] = original
    edit = {**_tailoring_edit(), "original_text": original, "replacement_text": replacement}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    with pytest.raises(llm_client.TailoringOutputError, match="factual skill or named term"):
        llm_client.tailor_resume_from_evidence(**inputs)


def test_tailoring_plural_acronym_does_not_borrow_unreferenced_evidence(monkeypatch):
    inputs = _tailoring_input()
    inputs["approved_evidence"].append({"id": "evd_apis", "text": "Built APIs.", "skills": ["API"]})
    edit = {**_tailoring_edit(), "replacement_text": "Built APIs and reduced response time by 20%."}
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps({"source_edits": [edit]}))
    with pytest.raises(llm_client.TailoringOutputError, match="factual skill or named term"):
        llm_client.tailor_resume_from_evidence(**inputs)


def test_gemini_transient_failure_uses_next_stable_model(monkeypatch):
    attempts = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            attempts.append(model)
            assert config.temperature is None
            if model == "gemini-3.5-flash":
                raise RuntimeError("503 UNAVAILABLE: model is experiencing high demand")
            return SimpleNamespace(text="completed")

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.5-flash")
    monkeypatch.setattr(genai, "Client", lambda **kwargs: SimpleNamespace(models=FakeModels()))

    result = llm_client._chat([{"role": "user", "content": "Analyze this role"}])

    assert result == "completed"
    assert attempts == ["gemini-3.5-flash", "gemini-3.6-flash"]


def test_exhausted_transient_models_remain_retryable(monkeypatch):
    class UnavailableModels:
        def generate_content(self, *, model, contents, config):
            raise RuntimeError("503 UNAVAILABLE: temporary provider capacity issue")

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(
        genai,
        "Client",
        lambda **kwargs: SimpleNamespace(models=UnavailableModels()),
    )

    with pytest.raises(llm_client.LLMProviderError) as exc_info:
        llm_client._chat([{"role": "user", "content": "Analyze this role"}])

    assert exc_info.value.retryable is True
    assert tasks._retryable(exc_info.value) is True


def test_retryable_detection_inspects_wrapped_provider_error():
    provider_error = RuntimeError("503 UNAVAILABLE")
    wrapped_error = RuntimeError("LLM provider request failed")
    wrapped_error.__cause__ = provider_error

    assert tasks._retryable(wrapped_error) is True


def _question_set():
    questions = [
        "How would you design a distributed search index?",
        "How do you investigate high query latency?",
        "How do you keep search results fresh?",
        "How would you measure search quality?",
        "How do you handle a failed indexing worker?",
        "How do you test a ranking change?",
        "How do you resolve competing engineering priorities?",
        "Which project demonstrates your approach to reliability?",
    ]
    return [
        {
            "question": question,
            "coaching_angle": "Explain the context, your action, and the verified result.",
            "evidence_ids": [],
        }
        for question in questions
    ]


@pytest.mark.parametrize("format_name", ["array", "fenced", "object", "fenced_object", "prose"])
def test_interview_accepts_common_json_formats_without_a_fallback(monkeypatch, format_name):
    data = _question_set()
    content = json.dumps({"questions": data} if "object" in format_name else data)
    if "fenced" in format_name:
        content = f"```json\n{content}\n```"
    elif format_name == "prose":
        content = f"Here are the practice questions:\n{content}\nReview the evidence before practicing."
    calls = []

    def chat(messages):
        calls.append(messages)
        return content

    monkeypatch.setattr(llm_client, "_chat", chat)
    result = llm_client.generate_interview_questions("Search Engineer", "Build reliable search.")

    assert len(result) == 8
    assert [item["question"] for item in result] == [item["question"] for item in data]
    assert all(item["answer_state"] == "evidence_needed" for item in result)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "bad_output", ["malformed", "short", "duplicate", "duplicate_spacing", "invalid_fields", "empty", "missing_content"]
)
def test_interview_repairs_an_unusable_first_response(monkeypatch, bad_output):
    data = _question_set()
    invalid = {
        "malformed": "Here are some interview questions, without JSON.",
        "short": json.dumps(data[:1]),
        "duplicate": json.dumps([data[0]] * 8),
        "duplicate_spacing": json.dumps([
            variant
            for item in data[:4]
            for variant in (item, {**item, "question": item["question"].replace("?", " ?")})
        ]),
        "invalid_fields": json.dumps([*data[:7], {"question": 123, "evidence_ids": []}]),
        "empty": "",
        "missing_content": None,
    }[bad_output]
    responses = iter([invalid, json.dumps(data)])
    calls = []

    def chat(messages):
        calls.append(list(messages))
        return next(responses)

    monkeypatch.setattr(llm_client, "_chat", chat)
    result = llm_client.generate_interview_questions("Search Engineer", "Build reliable search.")

    assert len(result) == 8
    assert len(calls) == 2
    assert "exactly 8 distinct questions" in calls[1][-1]["content"]


def test_interview_exhausted_repair_fails_without_becoming_a_retryable_run(monkeypatch):
    calls = []

    def chat(messages):
        calls.append(list(messages))
        return "Invalid JSON; provider prose mentions 429, 503 and timeout."

    monkeypatch.setattr(llm_client, "_chat", chat)
    with pytest.raises(llm_client.InterviewOutputError, match="complete set of 8") as error:
        llm_client.generate_interview_questions("Search Engineer", "Build reliable search.")

    assert len(calls) == 2
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert tasks._retryable(error.value) is False


def test_interview_filters_unknown_evidence_and_keeps_approved_facts(monkeypatch):
    data = _question_set()
    data[0]["evidence_ids"] = ["approved-1", "invented", "approved-1"]
    data[1]["evidence_ids"] = ["invented"]
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps(data))

    result = llm_client.generate_interview_questions(
        "Search Engineer",
        "Build reliable search.",
        approved_evidence=[{"id": "approved-1", "title": "API project", "text": "Reduced latency by 20%."}],
    )

    assert len(result) == 8
    assert result[0]["evidence_ids"] == ["approved-1"]
    assert result[0]["answer_state"] == "evidence_backed"
    assert "Reduced latency by 20%." in result[0]["answer"]
    assert result[1]["evidence_ids"] == []
    assert result[1]["answer_state"] == "evidence_needed"


def test_interview_caps_an_oversized_response_to_the_requested_count(monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps(_question_set()))
    assert len(llm_client.generate_interview_questions("Search Engineer", "Build search.", 3)) == 3


def test_interview_provider_errors_are_not_hidden_by_output_repair(monkeypatch):
    def chat(messages):
        raise llm_client.LLMProviderError("Provider unavailable", retryable=True)

    monkeypatch.setattr(llm_client, "_chat", chat)
    with pytest.raises(llm_client.LLMProviderError) as error:
        llm_client.generate_interview_questions("Search Engineer", "Build search.")
    assert error.value.retryable is True
