import json
from pathlib import Path
from typing import List, Tuple, Dict, Any

ROOT = Path(__file__).resolve().parents[2]
COURSES_PATH = ROOT / "resources" / "courses.json"

def _load_courses() -> List[Dict]:
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
    return (s or "").strip().lower()

def resources_for_skills(skills: List[str], limit_per_skill: int = 3) -> Dict[str, List[Dict]]:
    """Return a small curated resource set for each skill, keyed by normalized skill."""
    requested = [_norm(s) for s in skills if _norm(s)]
    out: Dict[str, List[Dict]] = {s: [] for s in requested}

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
    true_gaps: List[str],
    partial_matches: List[Dict[str, Any]],
    improvement_tips: List[str],
) -> Dict[str, Any]:
    """
    Deterministic strategy used when the LLM is unavailable or returns invalid JSON.
    Keeps the feature useful during local dev and API outages.
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
                "This appears as a missing requirement for the selected match."
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
                "resume_bullets": [
                    f"Built a job-aligned project demonstrating {', '.join(covers[:3])} for a {job_title or 'target'} role.",
                    "Documented architecture tradeoffs, implementation steps, and testing evidence to support interview discussions.",
                ],
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
        "generated_by": "fallback",
        "_resource_skills": top,
    }

def get_skill_gaps_and_courses(current_skills: List[str], target_role: str) -> Tuple[List[str], List[Dict]]:
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
    gap_rank = {g: i for i, g in enumerate(gap_set)}
    recommended.sort(key=lambda x: gap_rank.get(_norm(x.get("skill","")), 999))

    return gaps, recommended[:30]
