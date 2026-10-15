import json
from pathlib import Path
from typing import Any

from .market.skill_taxonomy import canonical_skill

ROOT = Path(__file__).resolve().parents[2]
COURSES_PATH = ROOT / "resources" / "courses.json"

def _load_courses() -> list[dict]:
    try:
        return json.loads(COURSES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

COURSES = _load_courses()

ROLE_SKILLS = {
    "Software Engineer": ["python", "javascript", "git", "data structures", "system design", "sql", "testing"],
    "Backend Engineer": ["python", "fastapi", "django", "sql", "postgresql", "redis", "docker", "oauth", "jwt", "testing"],
    "Frontend Engineer": ["javascript", "typescript", "react", "next.js", "html", "css", "tailwind", "testing"],
    "DevOps Engineer": ["linux", "docker", "kubernetes", "ci/cd", "terraform", "observability", "cloud run", "aws", "gcp"],
    "Data Scientist": ["python", "numpy", "pandas", "sql", "machine learning", "statistics", "visualization"],
    "ML Engineer": ["python", "machine learning", "pytorch", "tensorflow", "nlp", "docker", "kubernetes", "gcp"],
}

def _norm(s: str) -> str:
    return canonical_skill(s or "").casefold()

def resources_for_skills(skills: list[str], limit_per_skill: int = 3) -> dict[str, list[dict]]:
    """Return a small curated resource set for each skill, keyed by normalized skill."""
    requested = [_norm(s) for s in skills if _norm(s)]
    out: dict[str, list[dict]] = {s: [] for s in requested}

    for skill in requested:
        seen = set()
        for c in COURSES:
            c_skills = [_norm(x) for x in (c.get("skills") or []) if _norm(x)]
            c_skill = _norm(c.get("skill", ""))
            if skill not in c_skills and skill != c_skill:
                continue
            url = c.get("url") or ""
            key = url or c.get("title", "")
            if key in seen:
                continue
            seen.add(key)
            out[skill].append({
                "title": c.get("title", ""),
                "platform": c.get("platform", ""),
                "url": url,
                "skill": skill,
                "level": c.get("level"),
            })
            if len(out[skill]) >= limit_per_skill:
                break

    return out

def _priority_from_index(index: int) -> str:
    if index < 2:
        return "high"
    if index < 5:
        return "medium"
    return "low"

def build_fallback_learning_strategy(
    *,
    job_title: str,
    company: str,
    match_score: float,
    true_gaps: list[str],
    partial_matches: list[dict[str, Any]],
    improvement_tips: list[str],
) -> dict[str, Any]:
    """
    Curated default based on saved missing-evidence signals.
    Projects are suggestions for future work, never candidate accomplishments.
    """
    partial_skills = [
        str(p.get("skill", "")).strip().lower()
        for p in partial_matches
        if str(p.get("skill", "")).strip()
    ]
    priority_skills = []
    seen = set()
    for skill in [*true_gaps, *partial_skills]:
        s = _norm(skill)
        if s and s not in seen:
            seen.add(s)
            priority_skills.append(s)

    top = priority_skills[:6]
    priorities = []
    for idx, skill in enumerate(top):
        is_gap = skill in {_norm(s) for s in true_gaps}
        priorities.append({
            "skill": skill,
            "priority": _priority_from_index(idx),
            "current_status": "true_gap" if is_gap else "partial_coverage",
            "reason": (
                "The saved analysis has no resume evidence for this skill; confirm the role requirement before prioritizing it."
                if is_gap else
                "You have related experience, but the match analysis found only partial coverage."
            ),
            "expected_outcome": f"Build enough practical evidence to discuss {skill} confidently in screening and interviews.",
        })

    covers = top[:4] or ["job-relevant implementation", "testing", "documentation"]
    project_title = f"{job_title or 'Target Role'} readiness project"
    if company:
        project_title += f" for {company}"

    return {
        "readiness_summary": (
            f"Your current match score is {match_score:.1f}/100. The highest-value learning work is to turn "
            "the most important gaps into visible project evidence that maps directly to this job."
        ),
        "missing_hiring_signals": [
            {
                "signal": "Evidence for job-critical missing skills",
                "why_it_matters": "Hiring teams trust demonstrated project or work evidence more than a standalone skills list.",
                "severity": "high" if true_gaps else "medium",
            },
            {
                "signal": "Clear interview story for weak areas",
                "why_it_matters": "A focused project gives you concrete tradeoffs, implementation details, and outcomes to discuss.",
                "severity": "medium",
            },
        ],
        "learning_priorities": priorities,
        "project_recommendations": [
            {
                "title": project_title,
                "covers_gaps": covers,
                "description": (
                    "Build a compact, production-style project that combines the top missing skills from this match "
                    "into one demonstrable artifact with a README, architecture notes, and deployment or demo evidence."
                ),
                "implementation_steps": [
                    "Pick a realistic use case similar to the target job's domain.",
                    f"Implement the core workflow using: {', '.join(covers)}.",
                    "Add tests, configuration, and clear setup instructions.",
                    "Document architecture decisions and tradeoffs in the README.",
                    "Create 2-3 resume bullets that describe the project in hiring-manager language.",
                ],
                "resume_bullets": [],
                "interview_talking_points": [
                    "Why these technologies were chosen for the target job requirements.",
                    "What tradeoffs you made while implementing the project.",
                    "How you validated correctness, reliability, or performance.",
                ],
            }
        ],
        "timeline": [
            {"phase": "Phase 1", "focus": "Close the highest-priority gap", "deliverable": "Working local implementation"},
            {"phase": "Phase 2", "focus": "Combine skills into a realistic workflow", "deliverable": "End-to-end project flow"},
            {"phase": "Phase 3", "focus": "Package the hiring evidence", "deliverable": "README, resume bullets, and interview notes"},
        ],
        "generated_by": "curated",
        "_resource_skills": top,
    }

def get_skill_gaps_and_courses(current_skills: list[str], target_role: str) -> tuple[list[str], list[dict]]:
    target = ROLE_SKILLS.get(target_role, [])
    cur = {_norm(x) for x in (current_skills or []) if _norm(x)}
    gaps = [s for s in target if _norm(s) not in cur]

    gap_set = {_norm(g) for g in gaps}
    recommended = []
    seen = set()

    for c in COURSES:
        c_skills = [_norm(x) for x in (c.get("skills") or []) if _norm(x)]
        hit = next((s for s in c_skills if s in gap_set), None)
        if not hit:
            continue
        # de-dup by url
        url = c.get("url") or ""
        if url in seen:
            continue
        seen.add(url)
        recommended.append({
            "title": c.get("title", ""),
            "platform": c.get("platform", ""),
            "url": url,
            "skill": hit,
        })

    # Sort: show courses for the first few gaps earlier
    gap_rank = {_norm(g): i for i, g in enumerate(gaps)}
    recommended.sort(key=lambda x: gap_rank.get(_norm(x.get("skill","")), 999))

    return gaps, recommended[:30]
