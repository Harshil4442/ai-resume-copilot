"""Finite factuality regressions; not a semantic qualification benchmark."""
import copy
import json

import pytest
from backend.app.services import interview_contract, llm_client, synchronous_contracts
from backend.app.services.personal_claims import UnsupportedPersonalClaim, require_supported_claims


def facts(*texts):
    return [{"id": f"fact-{index}", "text": text, "skills": []}
            for index, text in enumerate(texts)]


@pytest.mark.parametrize("source,replacement,evidence", [
    ("Helped with Python platform.", "Led Python platform.", []),
    ("Helped with Python platform.", "Led Python platform.", facts("Led Docker platform.")),
    ("Helped with Python platform.", "Led Python platform.", facts("Led Docker platform. Helped Python platform.")),
    ("Helped lead Python platform.", "Led Python platform.", []),
    ("Planned to build Python platform.", "Built Python platform.", []),
    ("Build Python platform.", "Built Python platform.", []),
    ("Helped with Python platform.", "Led Python platform.", facts("Did not lead Python platform.")),
    ("Helped with Python platform.", "Led Python platform.", facts("If I led Python platform, I would test it.")),
    ("Helped with Python platform.", "Led Python platform.", facts("Planned to lead Python platform.")),
    ("Helped with Python platform.", "Led Python platform.", facts("My manager led Python platform.")),
    ("Helped with Python platform.", "Led Python platform.", facts("We led Python platform.")),
    ("Designed Python platform.", "Deployed Python platform.", []),
    ("Mentored Python team.", "Managed Python team.", []),
])
def test_rewrite_cannot_create_responsibility_or_completed_work(source, replacement, evidence):
    with pytest.raises(UnsupportedPersonalClaim):
        require_supported_claims(replacement, evidence, original=source)


@pytest.mark.parametrize("source,replacement,evidence", [
    ("Built Python APIs.", "Developed Python APIs.", []),
    ("Led Python platform.", "Headed Python platform.", []),
    ("Helped with Python platform.", "Led Python platform.", facts("Led Python platform.")),
    ("Helped build Python platform.", "Helped develop Python platform.", []),
    ("Developed reliable Python services for customers and improved system performance.",
     "Developed reliable Python services for customers and enhanced system performance.", []),
    ("Designed and deployed the Python platform.", "Designed the Python platform.", []),
    ("Designed and deployed the Python platform.", "Deployed the Python platform.", []),
])
def test_genuine_supported_paraphrases_remain_available(source, replacement, evidence):
    require_supported_claims(replacement, evidence, original=source)


@pytest.mark.parametrize("claim", [
    "You have 10 years of Java experience.",
    "You earned a degree at Stanford University.",
    "You are certified in AWS.",
    "You are a Senior Software Engineer at Acme.",
    "You are employed at Acme.",
    "How do you apply your 10 years of Java experience?",
    "Led the Python platform.",
    "10 years of Java experience.", "Stanford University, B.Tech degree.", "Employment: Google.",
    "Master's degree at Stanford University.",
])
def test_explicit_unsupported_candidate_facts_are_refused(claim):
    with pytest.raises(UnsupportedPersonalClaim):
        require_supported_claims(claim, facts("Built Python APIs."))


@pytest.mark.parametrize("claim,evidence", [
    ("You led Python platform.", facts("Led Python platform.")),
    ("You have 10 years of Java experience.", facts("10 years of Java experience.")),
    ("You earned a degree at Stanford University.", facts("Earned a degree at Stanford University.")),
    ("You are certified in AWS.", facts("AWS certification.")),
    ("You are a Software Engineer at Acme.", facts("Currently Software Engineer at Acme.")),
    ("Your Python experience fits this role.", facts("Built Python services.")),
])
def test_grounded_personalized_guidance_is_not_disabled(claim, evidence):
    require_supported_claims(claim, evidence)


def test_past_employment_does_not_support_a_current_employer_assertion():
    with pytest.raises(UnsupportedPersonalClaim):
        require_supported_claims("You are employed at Acme.", facts("Previously worked at Acme."))


def test_every_coordinated_personal_predicate_needs_its_own_support():
    with pytest.raises(UnsupportedPersonalClaim):
        require_supported_claims("You led the Python platform and worked at Google.",
                                 facts("Led the Python platform."))
    require_supported_claims("You led the Python platform and worked at Google.",
                             facts("Led the Python platform.", "Worked at Google."))
    require_supported_claims("You led Python platform and the service deployed Docker.",
                             facts("Led Python platform."))
    with pytest.raises(UnsupportedPersonalClaim):
        require_supported_claims("Designed Java API.",
                                 facts("Designed Python UI and deployed Java API."))
    with pytest.raises(UnsupportedPersonalClaim):
        require_supported_claims("Designed Python platform.",
                                 facts("Designed or deployed Python platform."))
    with pytest.raises(UnsupportedPersonalClaim):
        require_supported_claims("Deployed Python platform.",
                                 facts("Designed or deployed Python platform."))


@pytest.mark.parametrize("value", [
    "This role requires 10 years of Java experience.",
    "The job requires a B.Tech degree.",
    "Compare degree requirements for this role.",
    "AWS certification preparation plan",
])
def test_attributed_role_requirements_and_neutral_qualification_advice_are_not_candidate_facts(value):
    require_supported_claims(value, [])


@pytest.mark.parametrize("value", [
    "Compare 10-year data retention policies.",
    "Describe how you would build a Python service.",
    "If you led a Python platform, explain the verified trade-offs.",
    "Do not assume you have a degree at Stanford University.",
    "You could earn a relevant certification as future work.",
])
def test_neutral_future_and_conditional_guidance_remains_available(value):
    require_supported_claims(value, [])
    assert not interview_contract.unsupported_positive_history(value)


@pytest.mark.parametrize("field", ["answer", "suggested_followups"])
def test_job_citation_does_not_support_personal_facts_in_any_qa_field(field):
    response = {"answer": "Compare practical approaches.", "confidence": "high",
                "sources": ["job"], "suggested_followups": []}
    claim = "You have 10 years of Java experience and earned a degree at Stanford University."
    response[field] = claim if field == "answer" else [claim]
    with pytest.raises(synchronous_contracts.SynchronousOutputUnavailable):
        synchronous_contracts.validate("enhanced_qa", response, {"approved_evidence": []})


def test_cited_facts_support_useful_personalized_qa_and_not_unrelated_history():
    context = {"approved_evidence": facts("Led Python platform.", "Built Docker services.")}
    response = {"answer": "You led Python platform. Explain the trade-offs you considered.",
                "confidence": "medium", "sources": ["fact-0"], "suggested_followups": []}
    assert synchronous_contracts.validate("enhanced_qa", response, context)["answer"] == response["answer"]
    response["answer"] = "You led Docker services."
    with pytest.raises(synchronous_contracts.SynchronousOutputUnavailable):
        synchronous_contracts.validate("enhanced_qa", response, context)


@pytest.mark.parametrize("claim", [
    "You have 10 years of Java experience.", "You earned a degree at Stanford University.",
    "You are certified in AWS.", "You are a Senior Software Engineer at Acme.",
])
def test_interview_set_refuses_new_personal_assertion_families_without_reducing_count(claim):
    questions = [{"question": f"How would you test component {index}?",
                  "preparation_steps": ["Compare relevant testing methods.", "Explain failure checks."],
                  "evidence_ids": []} for index in range(8)]
    questions[0]["preparation_steps"][0] = claim
    with pytest.raises(llm_client.InterviewPersonalClaimError):
        llm_client._parse_interview_questions(json.dumps(questions), 8, set())


@pytest.mark.parametrize("path", [
    ("readiness_summary",), ("missing_hiring_signals", 0, "signal"),
    ("missing_hiring_signals", 0, "why_it_matters"),
    ("learning_priorities", 0, "reason"), ("learning_priorities", 0, "expected_outcome"),
    ("project_recommendations", 0, "title"), ("project_recommendations", 0, "description"),
    ("project_recommendations", 0, "implementation_steps", 0),
    ("project_recommendations", 0, "interview_talking_points", 0),
    ("timeline", 0, "phase"), ("timeline", 0, "focus"), ("timeline", 0, "deliverable"),
])
def test_every_learning_prose_field_refuses_unsupported_personal_claims(path):
    from backend.tests.test_unified_synchronous import output

    raw = copy.deepcopy(output("learning_strategy"))
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = "You led Python platform."
    with pytest.raises(synchronous_contracts.SynchronousOutputUnavailable, match="personal claims"):
        synchronous_contracts.validate("learning_strategy", raw,
                                      {"approved_evidence": facts("Built Python APIs.")})
