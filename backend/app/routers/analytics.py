import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..security import get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


PROFILE_FIELDS = [
    ("full_name", "Full name"),
    ("headline", "Professional headline"),
    ("location", "Location"),
    ("linkedin", "LinkedIn"),
    ("github", "GitHub"),
    ("portfolio", "Portfolio"),
    ("target_role", "Target role"),
    ("preferred_job_type", "Preferred job type"),
    ("bio", "Short bio"),
]


def _is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return any(str(v).strip() for v in value)
    if isinstance(value, (int, float)):
        return value > 0
    return bool(str(value).strip())


def _latest_datetime(items: List[Any]):
    dates = [getattr(item, "created_at", None) for item in items if getattr(item, "created_at", None)]
    return max(dates) if dates else None


def _match_card(match: models.JobMatch | None) -> Dict[str, Any] | None:
    if not match:
        return None
    return {
        "match_id": match.id,
        "job_title": match.job_title,
        "company": match.company or "",
        "match_score": float(match.match_score or 0.0),
        "created_at": match.created_at.isoformat() if match.created_at else None,
    }


def _count_quantified_achievements(text: str) -> int:
    patterns = [
        r"\b\d+%",
        r"\$\s?\d+",
        r"\b\d+\+?\s*(users|customers|clients|requests|transactions|projects|teams|engineers|apis|services)\b",
        r"\b(reduced|increased|improved|decreased|optimized|automated|saved|launched|delivered)\b",
    ]
    return sum(len(re.findall(pattern, text or "", flags=re.IGNORECASE)) for pattern in patterns)


def _resume_quality(resumes: List[models.Resume]) -> Dict[str, Any]:
    latest = max(resumes, key=lambda r: r.created_at or datetime.min) if resumes else None
    if not latest:
        return {
            "latest_resume_id": None,
            "latest_resume_filename": "",
            "latest_resume_date": None,
            "total_unique_skills": 0,
            "evidenced_skills": 0,
            "claimed_only_skills": 0,
            "verification_rate": 0,
            "quantified_achievements": 0,
            "missing_sections": ["experience", "projects", "education", "skills"],
        }

    all_skills = sorted({str(s).strip().lower() for r in resumes for s in (r.skills or []) if str(s).strip()})
    sections = latest.sections or {}
    experience_text = (sections.get("experience", "") or "").lower()
    projects_text = (sections.get("projects", "") or "").lower()
    evidence_text = f"{experience_text}\n{projects_text}"
    latest_skills = [str(s).strip().lower() for s in (latest.skills or []) if str(s).strip()]
    evidenced = [s for s in latest_skills if s in evidence_text]
    total_latest = max(len(latest_skills), 1)

    important_sections = ["experience", "projects", "education", "skills"]
    missing_sections = [s for s in important_sections if not (sections.get(s) or "").strip()]

    return {
        "latest_resume_id": latest.id,
        "latest_resume_filename": latest.original_filename or f"Resume #{latest.id}",
        "latest_resume_date": latest.created_at.isoformat() if latest.created_at else None,
        "total_unique_skills": len(all_skills),
        "evidenced_skills": len(set(evidenced)),
        "claimed_only_skills": max(len(set(latest_skills)) - len(set(evidenced)), 0),
        "verification_rate": round(len(set(evidenced)) / total_latest * 100),
        "quantified_achievements": _count_quantified_achievements(latest.raw_text or ""),
        "missing_sections": missing_sections,
    }


def _profile_completeness(profile: models.UserProfile | None, resumes: List[models.Resume], matches: List[models.JobMatch]) -> Dict[str, Any]:
    checks = []
    if profile:
        for field, label in PROFILE_FIELDS:
            checks.append((label, _is_filled(getattr(profile, field, None))))
    else:
        checks.extend((label, False) for _, label in PROFILE_FIELDS)

    latest_resume = max(resumes, key=lambda r: r.created_at or datetime.min) if resumes else None
    sections = latest_resume.sections if latest_resume and latest_resume.sections else {}
    contact = latest_resume.contact_info if latest_resume and latest_resume.contact_info else {}

    checks.extend([
        ("Resume uploaded", bool(resumes)),
        ("Skills extracted", any(r.skills for r in resumes)),
        ("Experience section", bool((sections.get("experience") or "").strip())),
        ("Projects section", bool((sections.get("projects") or "").strip())),
        ("Education section", bool((sections.get("education") or "").strip())),
        ("Contact info extracted", any(contact.values()) if isinstance(contact, dict) else False),
        ("At least one job match", bool(matches)),
    ])

    complete = sum(1 for _, ok in checks if ok)
    missing = [label for label, ok in checks if not ok]
    return {
        "score": round(complete / len(checks) * 100) if checks else 0,
        "missing_items": missing[:8],
    }


@router.get("/summary")
def analytics_summary(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    resumes = (
        db.query(models.Resume)
        .filter(models.Resume.user_id == current_user.id)
        .order_by(models.Resume.created_at.asc())
        .all()
    )
    job_matches = (
        db.query(models.JobMatch)
        .filter(models.JobMatch.user_id == current_user.id)
        .order_by(models.JobMatch.created_at.asc())
        .all()
    )
    profile = (
        db.query(models.UserProfile)
        .filter(models.UserProfile.user_id == current_user.id)
        .first()
    )

    avg_match = (
        sum(float(jm.match_score or 0.0) for jm in job_matches) / len(job_matches)
        if job_matches else 0.0
    )

    history = [
        {
            "timestamp": jm.created_at.isoformat(),
            "match_score": float(jm.match_score or 0.0),
            "job_title": jm.job_title,
            "company": jm.company or "",
            "match_id": jm.id,
        }
        for jm in job_matches
    ]

    best_match = max(job_matches, key=lambda jm: float(jm.match_score or 0.0), default=None)
    weakest_match = min(job_matches, key=lambda jm: float(jm.match_score or 0.0), default=None)
    latest_match = job_matches[-1] if job_matches else None

    gap_counter = Counter(
        str(gap).strip().lower()
        for match in job_matches
        for gap in (match.true_gaps or [])
        if str(gap).strip()
    )
    recurring_gaps = [
        {"skill": skill, "count": count}
        for skill, count in gap_counter.most_common(5)
    ]

    last_resume_date = _latest_datetime(resumes)
    last_match_date = _latest_datetime(job_matches)
    last_activity = max([d for d in [last_resume_date, last_match_date] if d], default=None)

    profile_health = _profile_completeness(profile, resumes, job_matches)
    resume_quality = _resume_quality(resumes)

    return {
        # Backward-compatible fields
        "profile_completeness": profile_health["score"],
        "average_match_score": float(avg_match),
        "applications_count": len(job_matches),
        "resume_count": len(resumes),
        "match_history": history,

        # Dashboard V2 fields
        "profile_health": profile_health,
        "resume_quality": resume_quality,
        "match_overview": {
            "best_match": _match_card(best_match),
            "weakest_match": _match_card(weakest_match),
            "latest_match": _match_card(latest_match),
        },
        "activity_summary": {
            "resumes_parsed": len(resumes),
            "job_matches_run": len(job_matches),
            "last_activity_at": last_activity.isoformat() if last_activity else None,
        },
        "recurring_gaps": recurring_gaps,
    }
