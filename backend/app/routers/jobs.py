import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from ..database import get_db
from .. import models, schemas
from ..security import get_current_user
from ..services.guardrails import billable_operation

router = APIRouter(prefix="/jobs", tags=["jobs"])

@router.post("/match", response_model=schemas.JobMatchResponse)
def match_job(
    payload: schemas.JobMatchRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == payload.resume_id,
                models.Resume.user_id == current_user.id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found for this user")
    jd_text = payload.job_description if isinstance(payload.job_description, str) else str(payload.job_description)
    if len(jd_text.strip()) < 20:
        raise HTTPException(status_code=422, detail="Job description is too short")

    from ..domains.analysis.operations import execute_job_match

    try:
        with billable_operation(
            user_id=current_user.id,
            db=db,
            operation="job_match_legacy",
            amount=1,
            input_payload={"resume_id": resume.id, "job_title": payload.job_title},
        ):
            result = execute_job_match(
                db,
                user_id=current_user.id,
                payload={
                    "resume_id": resume.id,
                    "job_title": payload.job_title,
                    "company": payload.company or "",
                    "job_description": jd_text,
                },
            )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Job match failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Job analysis provider failed. Reserved units were released.") from exc

    return schemas.JobMatchResponse(
        match_id=result["match_id"],
        match_score=result["match_score"],
        grade=result["grade"],
        required_skills=result["required_skills"],
        full_matches=result["full_matches"],
        partial_matches=[schemas.PartialMatch(**item) for item in result["partial_matches"]],
        true_gaps=result["true_gaps"],
        skill_verification_rate=result["skill_verification_rate"],
        dimensions=[schemas.DimensionScore(**item) for item in result["dimensions"]],
        fit_summary=result["fit_summary"],
        improvement_tips=result["improvement_tips"],
    )

@router.get("/matches", response_model=schemas.JobMatchHistoryResponse)
def get_match_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    matches = (
        db.query(models.JobMatch)
        .filter(models.JobMatch.user_id == current_user.id)
        .order_by(models.JobMatch.created_at.desc())
        .limit(50)
        .all()
    )
    return schemas.JobMatchHistoryResponse(
        matches=[
            schemas.JobMatchHistoryItem(
                match_id   = m.id,
                job_title  = m.job_title,
                company    = m.company,
                match_score= m.match_score,
                created_at = m.created_at,
            ) for m in matches
        ]
    )

@router.post("/match/{match_id}/tailor", response_model=schemas.ResumeTailorResponse)
def tailor_resume(
    match_id: int,
    payload: schemas.ResumeTailorRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    match = db.query(models.JobMatch).filter(
        models.JobMatch.id == match_id,
        models.JobMatch.user_id == current_user.id
    ).first()
    
    if not match:
        raise HTTPException(status_code=404, detail="Job match not found")
        
    resume = match.resume
    if not resume:
        raise HTTPException(status_code=404, detail="Associated resume not found")

    approved_evidence = (
        db.query(models.EvidenceItem)
        .filter(
            models.EvidenceItem.user_id == current_user.id,
            models.EvidenceItem.resume_id == resume.id,
            models.EvidenceItem.approval_state == "approved",
        )
        .order_by(models.EvidenceItem.created_at.asc())
        .all()
    )
    evidence_payload = [
        {
            "id": item.id,
            "title": item.title,
            "text": item.evidence_text,
            "metrics": item.metrics or {},
            "skills": item.skills or [],
        }
        for item in approved_evidence
    ]

    try:
        with billable_operation(
            user_id=current_user.id,
            db=db,
            operation="resume_tailor_legacy",
            amount=10,
            input_payload={
                "match_id": match.id,
                "resume_id": resume.id,
                "template_type": payload.template_type,
                "evidence_ids": [item["id"] for item in evidence_payload],
            },
        ):
            from ..services.llm_client import tailor_resume_mega_llm

            tailored_latex = tailor_resume_mega_llm(
                resume_text=resume.raw_text,
                jd_text=match.job_description,
                template_type=payload.template_type,
                true_gaps=match.true_gaps or [],
                partial_matches=match.partial_matches or [],
                approved_evidence=evidence_payload,
            )

            pdf_b64 = None
            import base64
            import os
            import subprocess
            import tempfile

            with tempfile.TemporaryDirectory() as tempdir:
                tex_path = os.path.join(tempdir, "resume.tex")
                with open(tex_path, "w", encoding="utf-8") as file:
                    file.write(tailored_latex)

                try:
                    subprocess.run(
                        ["pdflatex", "-interaction=nonstopmode", "resume.tex"],
                        cwd=tempdir,
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                    )
                    pdf_path = os.path.join(tempdir, "resume.pdf")
                    if os.path.exists(pdf_path):
                        with open(pdf_path, "rb") as pdf_file:
                            pdf_b64 = base64.b64encode(pdf_file.read()).decode("utf-8")
                except Exception as latex_error:
                    log.warning("Failed to compile LaTeX: %s", latex_error)

            return schemas.ResumeTailorResponse(
                tailored_resume_markdown=tailored_latex,
                pdf_base64=pdf_b64,
            )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Tailoring failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Resume tailoring failed. Reserved units were released.",
        ) from exc
