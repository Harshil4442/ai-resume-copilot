"""Real complete comparison contracts; synthetic model data, not qualification."""
from __future__ import annotations

import copy
import json

import pytest

from app.services import delivery_contracts, llm_client
from app.services import matching_contract as contract


def context():
    return {"resume": {"raw_text": "Morgan River. Developed Python APIs for Harbor Labs.",
        "sections": {"experience": "Developed Python APIs for Harbor Labs."}, "skills": ["Python"], "experience_years": 1},
        "job": {"title": "Backend engineer", "description": "Build reliable Python services and Docker tools."},
        "approved_evidence": [{"id": "fact-python", "text": "Developed Python APIs for Harbor Labs.", "title": "Approved work", "skills": [], "metrics": {}}]}


def comparison_output(value):
    required, _ = contract.extract_skills_from_text(value["job"]["description"])
    rows = []
    for skill in required:
        fact = next((fact for fact in value["approved_evidence"] if contract.source_skill(skill, fact["text"])), None)
        via = next((source for source in value["resume"]["skills"] if contract.canonical_skill(source).casefold() == contract.canonical_skill(skill).casefold()), "")
        supported = bool(fact and via)
        rows.append({"jd_skill": skill, "match_type": "full" if supported else "needs_evidence",
            "coverage": 1.0 if supported else 0.0, "via_skill": via if supported else "",
            "explanation": "Review the cited record for this requirement." if supported else "Review supporting evidence for this requirement; the assessment remains uncertain.",
            "evidence_ids": [fact["id"]] if supported else []})
    fact = value["approved_evidence"][0]
    return {"extracted_jd_skills": sorted(required), "skill_analysis": rows,
        "dimensions": [{"name": "Role Relevance", "score": 60.0, "feedback": "Review the cited approved record against the role.",
            "evidence_ids": [fact["id"]], "evidence_quote": fact["text"]}],
        "unassessed_dimensions": [{"name": name, "reason": "This dimension needs further approved evidence before assessment."}
                                  for name in contract.DIMENSIONS if name != "Role Relevance"],
        "fit_summary": {"text": "This comparison uses the approved record and flags requirements needing evidence.", "evidence_ids": []},
        "improvement_tips": [{"text": "Review each flagged requirement and add an approved example where available.", "evidence_ids": []}]}


def test_complete_contract_preserves_context_and_missing_proof_without_fake_scores():
    value = context()
    value["job"]["description"] += " Include the final job sentinel."
    value["resume"]["sections"]["projects"] = "Complete projects sentinel."
    messages = contract.messages(value)
    sent = json.loads(messages[1]["content"].split("\n", 1)[1])
    assert sent == value and "final job sentinel" in sent["job"]["description"]
    output = contract.validate(comparison_output(value), value)
    assert len(output["dimensions"]) + len(output["unassessed_dimensions"]) == 8
    assert all("score" not in row for row in output["unassessed_dimensions"])
    assert next(r for r in output["skill_analysis"] if r["jd_skill"] == "Docker")["match_type"] == "needs_evidence"


@pytest.mark.parametrize("changed", ["invented_requirement", "omitted_requirement", "duplicate_requirement", "unknown_evidence", "unsupported_skill", "invalid_coverage", "nan_score", "missing_dimension", "unsupported_personal_claim", "invented_quote", "unreviewed_url", "generic_only"])
def test_semantic_and_factual_contract_rejects_incomplete_or_invented_output(changed):
    value, raw = context(), comparison_output(context())
    if changed == "invented_requirement":
        raw["extracted_jd_skills"].append("Kubernetes")
    elif changed == "omitted_requirement":
        raw["extracted_jd_skills"] = ["Python"]
        raw["skill_analysis"] = raw["skill_analysis"][:1]
    elif changed == "duplicate_requirement":
        raw["extracted_jd_skills"].append("python")
    elif changed == "unknown_evidence":
        raw["dimensions"][0]["evidence_ids"] = ["missing"]
    elif changed == "unsupported_skill":
        raw["skill_analysis"][0].update({"match_type": "full", "coverage": 1.0, "via_skill": "Kubernetes", "evidence_ids": ["fact-python"]})
    elif changed == "invalid_coverage":
        raw["skill_analysis"][0]["coverage"] = 0.7
    elif changed == "nan_score":
        raw["dimensions"][0]["score"] = float("nan")
    elif changed == "missing_dimension":
        raw["unassessed_dimensions"].pop()
    elif changed == "unsupported_personal_claim":
        raw["fit_summary"] = {"text": "You led Kubernetes deployments and reduced costs by 42%.", "evidence_ids": ["fact-python"]}
    elif changed == "invented_quote":
        raw["dimensions"][0]["evidence_quote"] = "Led a production Kubernetes team."
    elif changed == "unreviewed_url":
        raw["improvement_tips"][0]["text"] = "Review https://invented.example.test/course"
    else:
        raw["dimensions"] = []
        raw["unassessed_dimensions"] = [{"name": name, "reason": "Further approved evidence is needed."} for name in contract.DIMENSIONS]
        for row in raw["skill_analysis"]:
            row.update({"match_type": "needs_evidence", "coverage": 0.0, "via_skill": "", "evidence_ids": []})
    with pytest.raises(contract.MatchingOutputUnavailable):
        contract.validate(raw, value)


def test_missing_evidence_and_oversized_inputs_decline_before_any_chat(monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda *a: pytest.fail("No model call"))
    value = context()
    value["approved_evidence"] = []
    with pytest.raises(contract.MatchingContextUnavailable, match="Approve at least one"):
        llm_client.analyze_job_match_complete_llm(value)
    value = context()
    value["resume"]["raw_text"] += " never-truncate" * 10000
    with pytest.raises(contract.MatchingContextUnavailable, match="Nothing was shortened"):
        llm_client.analyze_job_match_complete_llm(value)


def test_matching_output_digest_binds_actual_helpers_and_both_operation_names(monkeypatch):
    first = delivery_contracts.current_output_contract("enhanced_job_match")
    assert first and first == delivery_contracts.current_output_contract("job_match")
    monkeypatch.setattr(contract, "MAX_CONTEXT_ESTIMATE", 100)
    assert delivery_contracts.current_output_contract("enhanced_job_match") != first


def test_validation_never_rewrites_or_adds_missing_candidate_claims():
    value, raw = context(), comparison_output(context())
    original = copy.deepcopy(raw)
    result = contract.validate(raw, value)
    assert raw == original
    assert result["fit_summary"] == original["fit_summary"]["text"]


def test_complete_prompt_sensitive_redaction_preserves_json_structure_and_safe_content():
    from app.services.prompt_privacy import REDACTED, redact_messages

    value = context()
    value["resume"]["raw_text"] = "Morgan River\nprivate@example.invalid\nDeveloped Python APIs for Harbor Labs."
    sent = redact_messages(contract.messages(value))
    parsed = json.loads(sent[-1]["content"].split("COMPLETE SOURCE DATA:\n", 1)[1])
    assert "private@example.invalid" not in sent[-1]["content"]
    assert REDACTED in parsed["resume"]["raw_text"]
    assert parsed["approved_evidence"] == value["approved_evidence"]
    assert "Developed Python APIs for Harbor Labs." in parsed["resume"]["raw_text"]


def test_full_mapping_cannot_relabel_a_different_skill_as_equivalent():
    value, raw = context(), comparison_output(context())
    docker = next(row for row in raw["skill_analysis"] if row["jd_skill"] == "Docker")
    docker.update(match_type="full", coverage=1.0, via_skill="Python", evidence_ids=["fact-python"])
    with pytest.raises(contract.MatchingOutputUnavailable):
        contract.validate(raw, value)


def test_missing_proof_is_not_a_categorical_absence_of_experience():
    value, raw = context(), comparison_output(context())
    raw["unassessed_dimensions"][0]["reason"] = "You have no relevant experience with this role."
    with pytest.raises(contract.MatchingOutputUnavailable, match="Missing proof"):
        contract.validate(raw, value)


@pytest.mark.parametrize("claim", ["You have a 95% chance of selection.", "Hiring probability is 80%.", "Your chance of being hired is 75%."])
def test_comparison_cannot_invent_selection_probabilities(claim):
    value, raw = context(), comparison_output(context())
    raw["fit_summary"]["text"] = claim
    with pytest.raises(contract.MatchingOutputUnavailable, match="probability"):
        contract.validate(raw, value)


def test_malformed_or_near_prefix_keeps_existing_sensitive_fallback():
    from app.services.prompt_privacy import REDACTED, redact_prompt_text

    for value in ('COMPLETE SOURCE DATA:\n{"password":"private-do-not-send", "safe":"retain"',
                  'Narrative. Password: private-do-not-send\nEmail: private@example.invalid'):
        result = redact_prompt_text(value)
        assert "private-do-not-send" not in result and "private@example.invalid" not in result
        assert REDACTED in result
