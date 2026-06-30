from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from ... import models
from ..matching import build_skill_confidence_map
from .project_recommender import recommend_projects
from .providers.base import JobPosting, JobSearchParams
from .providers.registry import search_all
from .skill_extractor import categorize_skills, extract_skills_from_text
from .skill_taxonomy import canonical_skill, skill_category


MAX_RESULTS = 100


def importance_from_percentage(percentage: float) -> str:
    if percentage >= 60:
        return "critical"
    if percentage >= 40:
        return "high"
    if percentage >= 20:
        return "medium"
    return "low"


def _priority_for_gap(importance: str, status: str) -> str:
    if importance == "critical" and status == "missing":
        return "critical"
    if importance in {"critical", "high"} and status in {"missing", "claimed"}:
        return "high"
    if importance == "medium" and status in {"missing", "claimed"}:
        return "medium"
    return "low"


def _resume_status(skill: str, resume: Optional[models.Resume], confidence_map: Dict[str, float]) -> str:
    if resume is None:
        return "missing"
    canonical = canonical_skill(skill).lower()
    resume_skills = {canonical_skill(s).lower() for s in (resume.skills or []) if str(s).strip()}
    sections = resume.sections or {}
    evidence_text = f"{sections.get('experience', '')}\n{sections.get('projects', '')}".lower()
    if canonical in evidence_text:
        return "proven"
    if canonical in resume_skills:
        conf = confidence_map.get(canonical, 0.3)
        return "proven" if conf >= 0.7 else "claimed"
    return "missing"


def _reason(skill: str, percentage: float, status: str) -> str:
    if status == "proven":
        return f"{skill} appears in {percentage:.0f}% of sampled postings and is evidenced in your resume."
    if status == "claimed":
        return f"{skill} appears in {percentage:.0f}% of sampled postings, but your resume does not strongly evidence it in experience or projects."
    return f"{skill} appears in {percentage:.0f}% of sampled postings and was not found in your resume."


def _job_quality_warning(jobs: List[JobPosting]) -> List[str]:
    warnings = []
    if len(jobs) < 10:
        warnings.append("Sample size is small. Broaden the role, location, or date range for stronger market confidence.")
    very_short = sum(1 for job in jobs if len((job.description or "").split()) < 35)
    if very_short:
        warnings.append(f"{very_short} postings had short descriptions, so skill extraction may be incomplete.")
    return warnings


def _build_skill_tables(skill_counts: Counter, jd_count: int) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    top_skills = []
    for skill, count in skill_counts.most_common(30):
        percentage = round(count / max(jd_count, 1) * 100, 1)
        top_skills.append({
            "skill": skill,
            "count": count,
            "percentage": percentage,
            "category": skill_category(skill),
            "importance": importance_from_percentage(percentage),
        })

    by_category: dict[str, list[dict]] = defaultdict(list)
    for item in top_skills:
        by_category[item["category"]].append({
            "skill": item["skill"],
            "count": item["count"],
            "percentage": item["percentage"],
        })
    skill_categories = [
        {"category": category, "skills": skills}
        for category, skills in sorted(by_category.items())
    ]
    return top_skills, skill_categories


def analyze_market(
    *,
    target_role: str,
    location: str,
    country_code: str,
    experience_level: str,
    remote: Optional[bool],
    max_results: int,
    posted_within_days: int,
    resume: Optional[models.Resume],
) -> Dict[str, Any]:
    params = JobSearchParams(
        target_role=target_role,
        location=location or "",
        country_code=(country_code or "").upper(),
        experience_level=experience_level or "",
        remote=remote,
        max_results=max(5, min(max_results or 50, MAX_RESULTS)),
        posted_within_days=max(1, min(posted_within_days or 30, 365)),
    )
    provider_result = search_all(params)
    jobs = provider_result.jobs
    warnings = list(provider_result.warnings)
    warnings.extend(_job_quality_warning(jobs))
    if resume is None:
        warnings.append("No resume selected, so resume-vs-market gap analysis is limited.")

    jd_skill_sets = []
    security_warnings = []
    for job in jobs:
        skills, skill_warnings = extract_skills_from_text(job.description)
        jd_skill_sets.append(skills)
        security_warnings.extend(skill_warnings)
    warnings.extend(sorted(set(security_warnings)))

    skill_counts: Counter = Counter()
    for skills in jd_skill_sets:
        skill_counts.update(skills)
    if jobs and not skill_counts:
        warnings.append("No strong skills were detected from the sampled postings.")

    top_skills, skill_categories = _build_skill_tables(skill_counts, len(jobs))

    confidence_map: Dict[str, float] = {}
    if resume is not None:
        confidence_map = build_skill_confidence_map(
            [canonical_skill(s).lower() for s in (resume.skills or [])],
            resume.sections or {},
        )

    gap_analysis = []
    for item in top_skills[:20]:
        status = _resume_status(item["skill"], resume, confidence_map)
        priority = _priority_for_gap(item["importance"], status)
        gap_analysis.append({
            "skill": item["skill"],
            "market_demand_percentage": item["percentage"],
            "resume_status": status,
            "priority": priority,
            "reason": _reason(item["skill"], item["percentage"], status),
        })

    learning_priorities = [
        {
            "skill": item["skill"],
            "priority": item["priority"],
            "why": item["reason"],
        }
        for item in gap_analysis
        if item["resume_status"] != "proven" and item["priority"] in {"critical", "high", "medium"}
    ][:8]

    projects = recommend_projects(gap_analysis)
    top_names = [item["skill"] for item in top_skills[:5]]
    missing_names = [item["skill"] for item in gap_analysis if item["resume_status"] == "missing"][:4]
    if top_names:
        summary = (
            f"Across {len(jobs)} sampled {target_role} postings, the most repeated skills were "
            f"{', '.join(top_names)}."
        )
        if resume is not None and missing_names:
            summary += f" Your strongest resume gaps are {', '.join(missing_names)}."
    else:
        summary = (
            f"No reliable skill trend could be extracted for {target_role}. Try broadening the role, "
            "location, or provider date range."
        )

    return {
        "target_role": target_role,
        "location": location,
        "country_code": country_code.upper() if country_code else "",
        "experience_level": experience_level,
        "remote": remote,
        "source_provider": provider_result.provider,
        "from_cache": provider_result.from_cache,
        "sample_size": len(jobs),
        "confidence": "high" if len(jobs) >= 40 else "medium" if len(jobs) >= 10 else "low",
        "top_skills": top_skills,
        "skill_categories": skill_categories,
        "resume_gap_analysis": gap_analysis,
        "recommended_projects": projects,
        "learning_priorities": learning_priorities,
        "summary": summary,
        "warnings": sorted(set(warnings)),
        "sample_jobs": [
            {
                "title": j.title,
                "company": j.company,
                "location": j.location,
                "posted_at": j.posted_at,
                "url": j.url,
                "source": j.source,
            }
            for j in jobs[:10]
        ],
    }

