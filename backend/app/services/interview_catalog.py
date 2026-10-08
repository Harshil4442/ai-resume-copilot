"""Curated practice questions with candidate evidence references, no model call."""
from __future__ import annotations

from typing import Any

from .market.skill_extractor import extract_skills_from_text

CATALOG_VERSION = "role-practice-v1"
_ROLE_TOPICS = (
    ({"software", "engineer", "developer", "backend", "frontend", "platform"}, (
        ("How would you investigate a production incident and decide what to fix first?", "Discuss signals, hypotheses, a safe fix, and how you would verify recovery."),
        ("How would you design a service for this role to stay reliable as usage grows?", "Explain requirements, failure cases, trade-offs, and measurable reliability."),
        ("How would you test and release a change without disrupting users?", "Discuss representative tests, gradual rollout, observability, and rollback."),
    )),
    ({"data", "analyst", "analytics", "scientist", "machine learning"}, (
        ("How would you check whether a dataset is suitable for a business decision?", "Discuss quality, missing data, sampling, and limitations before choosing an approach."),
        ("How would you explain an uncertain analysis result to a nontechnical stakeholder?", "Separate observations, assumptions, confidence, and the decision that the evidence supports."),
        ("How would you evaluate whether a data or model change improves results?", "Choose useful baselines, independent validation, and measures tied to the task."),
    )),
    ({"design", "designer", "ux", "product"}, (
        ("How would you identify the user problem before proposing a solution?", "Explain research questions, evidence gathering, and how you would prioritize needs."),
        ("How would you evaluate whether a product change improves the user experience?", "Describe accessibility, usability checks, success measures, and unintended effects."),
        ("How would you balance user needs against delivery constraints?", "Discuss options, trade-offs, stakeholder alignment, and a testable next step."),
    )),
    ({"sales", "account", "customer", "marketing", "business"}, (
        ("How would you understand a customer need before recommending a solution?", "Discuss listening, qualifying the need, and making an accurate recommendation."),
        ("How would you measure the success of work in this role?", "Connect useful metrics to business goals and explain what could distort those metrics."),
        ("How would you handle a difficult stakeholder or customer conversation?", "Explain preparation, clear communication, boundaries, and follow-through."),
    )),
)


def curated_interview_questions(
    job_title: str,
    jd_text: str,
    num_questions: int = 8,
    approved_evidence: list[dict] | None = None,
) -> list[dict[str, Any]]:
    evidence = approved_evidence or []
    title_words = job_title.casefold()
    topics = next((items for words, items in _ROLE_TOPICS if any(word in title_words for word in words)), ())
    skills, _ = extract_skills_from_text(jd_text)
    entries = list(topics)
    for skill in sorted(skills, key=str.casefold)[:3]:
        entries.append((
            f"How would you use {skill} to solve a problem in this {job_title or 'target'} role?",
            f"Explain the problem, why {skill} is suitable, its limitations, and how you would test the result.",
        ))
    entries.extend([
        (f"Which experience best demonstrates your fit for this {job_title or 'target'} role?", "Choose a relevant approved example and explain the context, your action, and the verified result."),
        ("Tell me about a time you worked with others to solve a difficult problem.", "Explain your own contribution, how you communicated, and a verified outcome."),
        ("How do you prioritize when several important tasks compete for your time?", "Discuss impact, urgency, dependencies, and how you communicate trade-offs."),
        ("Describe how you learn an unfamiliar skill needed for a project.", "Explain a concrete learning approach and how you check that you can apply the skill."),
        ("What would you clarify before starting your first project in this role?", "Ask about goals, users, constraints, responsibilities, and success measures."),
        ("How would you respond when your initial approach does not work?", "Describe how you investigate, seek feedback, adjust, and verify the next approach."),
        ("How do you ensure the quality and accuracy of your work?", "Discuss review, checks, error handling, and how you learn from mistakes."),
        ("What questions would you ask the hiring team about this role?", "Ask about expectations, collaboration, growth, and the problems the team needs to solve."),
        ("How would you handle a requirement that is unclear or changes during delivery?", "Explain clarification, written assumptions, impact assessment, and communication."),
        ("How would you communicate progress and risks to your team?", "Use clear updates, evidence, next actions, and explicit requests for help where needed."),
        ("How would you decide whether a process should be improved?", "Understand the present outcome, gather evidence, test an improvement, and measure its effect."),
        ("How do you approach constructive feedback on your work?", "Explain how you clarify feedback, apply what is useful, and check the result."),
    ])
    output = []
    for question, coaching in entries[:max(3, min(int(num_questions), 12))]:
        q_skills, _ = extract_skills_from_text(question)
        cited = [item for item in evidence if q_skills and q_skills.intersection(
            extract_skills_from_text(str(item.get("text", "")))[0]
        )][:2]
        ids = [str(item["id"]) for item in cited if item.get("id")]
        if ids:
            facts = "; ".join(str(item.get("text", "")) for item in cited)[:1600]
            guidance = f"Approved facts to review: {facts}\n\nCoaching focus: {coaching}"
        else:
            guidance = f"Practice the approach generally. Add or approve a relevant fact before drafting a personal experience answer. Coaching focus: {coaching}"
        output.append({
            "question": question, "answer": guidance, "evidence_ids": ids,
            "answer_state": "evidence_backed" if ids else "evidence_needed",
            "provenance": "curated", "catalog_version": CATALOG_VERSION,
        })
    return output
