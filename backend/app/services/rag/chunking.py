from dataclasses import dataclass
from typing import Any, List

from ... import models


@dataclass
class EvidenceChunk:
    id: str
    source: str
    title: str
    text: str
    priority: float = 0.0


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = []
        for key, val in value.items():
            parts.append(f"{key}: {_to_text(val)}")
        return "\n".join(p for p in parts if p.strip())
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_to_text(v) for v in value if _to_text(v))
    return str(value).strip()


def _add(chunks: List[EvidenceChunk], source: str, title: str, text: Any, priority: float) -> None:
    clean = _to_text(text)
    if not clean:
        return
    chunks.append(EvidenceChunk(
        id=f"{source}-{len(chunks) + 1}",
        source=source,
        title=title,
        text=clean[:5000],
        priority=priority,
    ))


def build_match_chunks(resume: models.Resume, match: models.JobMatch) -> List[EvidenceChunk]:
    sections = resume.sections or {}
    chunks: List[EvidenceChunk] = []

    contact = resume.contact_info or {}
    if isinstance(contact, dict):
        visible_contact = {k: v for k, v in contact.items() if v}
        _add(chunks, "resume_summary", "Resume contact/profile signals", visible_contact, 0.35)

    _add(chunks, "resume_skills", "Resume skills", resume.skills or [], 0.75)
    _add(chunks, "resume_experience", "Resume experience section", sections.get("experience", ""), 0.85)
    _add(chunks, "resume_projects", "Resume projects section", sections.get("projects", ""), 0.8)
    _add(chunks, "resume_education", "Resume education section", sections.get("education", ""), 0.45)
    _add(chunks, "resume_sections", "Other resume sections", {
        k: v for k, v in sections.items()
        if k not in {"experience", "projects", "education", "skills"} and v
    }, 0.25)

    _add(chunks, "job_description", "Job description", match.job_description, 0.9)
    _add(chunks, "required_skills", "Skills extracted from job description", match.required_skills or [], 0.95)
    _add(chunks, "full_matches", "Full skill matches", match.full_matches or [], 0.8)
    _add(chunks, "partial_matches", "Partial skill coverage", match.partial_matches or [], 0.85)
    _add(chunks, "true_gaps", "True missing skill gaps", match.true_gaps or [], 0.95)
    _add(chunks, "match_score", "Overall match score", {
        "score": float(match.match_score or 0.0),
        "skill_verification_rate": float(match.skill_verification_rate or 0.0),
    }, 0.85)
    _add(chunks, "dimension_scores", "Recruiter dimension scores", match.dimension_scores or [], 0.9)
    _add(chunks, "fit_summary", "Fit summary", match.fit_summary or "", 0.9)
    _add(chunks, "improvement_tips", "Improvement tips", match.improvement_tips or [], 0.75)

    return chunks

