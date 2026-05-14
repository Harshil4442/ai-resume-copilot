from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..security import get_current_user
from ..services.recommender import (
    build_fallback_learning_strategy,
    get_skill_gaps_and_courses,
    resources_for_skills,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

@router.post("/gaps")
async def skill_gaps(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    target_role = (payload.get("target_role") or "").strip()
    if not target_role:
        raise HTTPException(status_code=400, detail="target_role is required")

    current_skills = payload.get("current_skills")
    if not current_skills:
        latest_resume = (
            db.query(models.Resume)
            .filter(models.Resume.user_id == current_user.id)
            .order_by(models.Resume.created_at.desc())
            .first()
        )
        current_skills = (latest_resume.skills if latest_resume and latest_resume.skills else [])

    gaps, courses = get_skill_gaps_and_courses(current_skills, target_role)
    return {"skill_gaps": gaps, "recommended_courses": courses, "current_skills": current_skills}


def _as_list(value):
    return value if isinstance(value, list) else []


def _attach_resources(strategy: dict, skills_hint: list[str]) -> dict:
    priority_skills = [
        (p.get("skill") or "").strip().lower()
        for p in _as_list(strategy.get("learning_priorities"))
        if (p.get("skill") or "").strip()
    ]
    resource_skills = priority_skills or skills_hint
    resource_map = resources_for_skills(resource_skills)

    for priority in _as_list(strategy.get("learning_priorities")):
        skill = (priority.get("skill") or "").strip().lower()
        priority["resources"] = resource_map.get(skill, [])

    return strategy


def _clean_text(value, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _normalize_strategy(strategy: dict) -> dict:
    signals = []
    for item in _as_list(strategy.get("missing_hiring_signals")):
        if not isinstance(item, dict):
            continue
        signal = _clean_text(item.get("signal"))
        if not signal:
            continue
        signals.append({
            "signal": signal,
            "why_it_matters": _clean_text(item.get("why_it_matters"), "This matters because it affects how strongly the resume maps to the selected job."),
            "severity": _clean_text(item.get("severity"), "medium").lower(),
        })

    priorities = []
    for item in _as_list(strategy.get("learning_priorities")):
        if not isinstance(item, dict):
            continue
        skill = _clean_text(item.get("skill")).lower()
        if not skill:
            continue
        priorities.append({
            "skill": skill,
            "priority": _clean_text(item.get("priority"), "medium").lower(),
            "current_status": _clean_text(item.get("current_status"), "gap"),
            "reason": _clean_text(item.get("reason"), "This skill or signal would improve the selected job match."),
            "expected_outcome": _clean_text(item.get("expected_outcome"), f"Gain practical evidence for {skill}."),
            "resources": _as_list(item.get("resources")),
        })

    projects = []
    for item in _as_list(strategy.get("project_recommendations")):
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"), "Job-readiness project")
        projects.append({
            "title": title,
            "covers_gaps": [_clean_text(x).lower() for x in _as_list(item.get("covers_gaps")) if _clean_text(x)],
            "description": _clean_text(item.get("description"), "Build a practical project that demonstrates the most important missing hiring signals."),
            "implementation_steps": [_clean_text(x) for x in _as_list(item.get("implementation_steps")) if _clean_text(x)],
            "resume_bullets": [_clean_text(x) for x in _as_list(item.get("resume_bullets")) if _clean_text(x)],
            "interview_talking_points": [_clean_text(x) for x in _as_list(item.get("interview_talking_points")) if _clean_text(x)],
        })

    timeline = []
    for item in _as_list(strategy.get("timeline")):
        if not isinstance(item, dict):
            continue
        timeline.append({
            "phase": _clean_text(item.get("phase"), "Phase"),
            "focus": _clean_text(item.get("focus"), "Focused learning"),
            "deliverable": _clean_text(item.get("deliverable"), "A demonstrable project artifact"),
        })

    return {
        **strategy,
        "readiness_summary": _clean_text(strategy.get("readiness_summary"), "Focus on the highest-value gaps for this selected match and convert them into project evidence."),
        "missing_hiring_signals": signals,
        "learning_priorities": priorities,
        "project_recommendations": projects,
        "timeline": timeline,
    }


@router.post("/match_strategy", response_model=schemas.LearningStrategyResponse)
async def match_learning_strategy(
    payload: schemas.LearningStrategyRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    match = (
        db.query(models.JobMatch)
        .filter(models.JobMatch.id == payload.match_id, models.JobMatch.user_id == current_user.id)
        .first()
    )
    if not match:
        raise HTTPException(status_code=404, detail="Job match not found for this user")

    resume = None
    if match.resume_id:
        resume = (
            db.query(models.Resume)
            .filter(models.Resume.id == match.resume_id, models.Resume.user_id == current_user.id)
            .first()
        )

    resume_skills = resume.skills if resume and resume.skills else []
    experience_years = float(resume.experience_years or 0.0) if resume else 0.0
    true_gaps = _as_list(match.true_gaps)
    partial_matches = _as_list(match.partial_matches)
    required_skills = _as_list(match.required_skills)
    improvement_tips = _as_list(match.improvement_tips)
    dimension_scores = _as_list(match.dimension_scores)

    generated_by = "llm"
    try:
        from ..services.llm_client import generate_learning_strategy_llm

        strategy = generate_learning_strategy_llm(
            job_title=match.job_title,
            company=match.company,
            jd_text=match.job_description or "",
            resume_skills=resume_skills,
            experience_years=experience_years,
            true_gaps=true_gaps,
            partial_matches=partial_matches,
            required_skills=required_skills,
            match_score=float(match.match_score or 0.0),
            fit_summary=match.fit_summary or "",
            dimension_scores=dimension_scores,
            improvement_tips=improvement_tips,
        )
    except Exception:
        generated_by = "fallback"
        strategy = build_fallback_learning_strategy(
            job_title=match.job_title,
            company=match.company,
            match_score=float(match.match_score or 0.0),
            true_gaps=true_gaps,
            partial_matches=partial_matches,
            improvement_tips=improvement_tips,
        )

    skills_hint = strategy.pop("_resource_skills", None) or [*true_gaps, *required_skills[:3]]
    strategy = _normalize_strategy(strategy)
    strategy = _attach_resources(strategy, skills_hint)

    return {
        "match_id": match.id,
        "job_title": match.job_title,
        "company": match.company or "",
        "current_score": float(match.match_score or 0.0),
        "readiness_summary": strategy.get("readiness_summary", ""),
        "missing_hiring_signals": _as_list(strategy.get("missing_hiring_signals")),
        "learning_priorities": _as_list(strategy.get("learning_priorities")),
        "project_recommendations": _as_list(strategy.get("project_recommendations")),
        "timeline": _as_list(strategy.get("timeline")),
        "generated_by": strategy.get("generated_by") or generated_by,
    }
