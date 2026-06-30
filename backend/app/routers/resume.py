from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..services.parsing import parse_resume_file
from ..security import get_current_user

router = APIRouter(prefix="/resume", tags=["resume"])


@router.get("/list", response_model=schemas.ResumeListResponse)
def list_resumes(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return all parsed resumes for the current user (used for dropdown selection)."""
    resumes = (
        db.query(models.Resume)
        .filter(models.Resume.user_id == current_user.id)
        .order_by(models.Resume.created_at.desc())
        .all()
    )
    return schemas.ResumeListResponse(
        resumes=[
            schemas.ResumeListItem(
                id=r.id,
                filename=r.original_filename or f"Resume #{r.id}",
                created_at=r.created_at,
            )
            for r in resumes
        ]
    )


@router.get("/{resume_id}", response_model=schemas.ResumeParseResponse)
def get_resume(
    resume_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == resume_id, models.Resume.user_id == current_user.id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
        
    contact = resume.contact_info or {}
    return schemas.ResumeParseResponse(
        resume_id=resume.id,
        skills=resume.skills or [],
        experience_years=resume.experience_years or 0.0,
        sections=resume.sections or {},
        contact_info=schemas.ContactInfo(
            name=contact.get("name"),
            email=contact.get("email"),
            phone=contact.get("phone"),
            linkedin=contact.get("linkedin"),
            github=contact.get("github"),
        ),
    )

ALLOWED_TYPES = {
    "application/pdf",
    "application/octet-stream",                                          # generic binary (some browsers)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
}

# Upload size cap (bytes). Resumes >5MB are almost always images-as-PDF
# and would blow up DB storage + LLM context.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.post("/parse", response_model=schemas.ResumeParseResponse)
async def parse_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from ..services.guardrails import verify_and_deduct_credit
    verify_and_deduct_credit(current_user.id, db)

    filename = file.filename or ""
    content_type = file.content_type or ""

    # Validate file type
    is_pdf = filename.lower().endswith(".pdf") or "pdf" in content_type
    is_docx = filename.lower().endswith(".docx") or "wordprocessingml" in content_type

    if not (is_pdf or is_docx):
        raise HTTPException(
            status_code=400,
            detail="Only PDF and DOCX files are supported.",
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit.",
        )
    raw_text, sections, skills, exp_years, contact_info = parse_resume_file(
        file_bytes, filename=filename, use_llm=True
    )

    resume = models.Resume(
        user_id=current_user.id,
        original_filename=filename,
        raw_text=raw_text,
        skills=skills,
        experience_years=exp_years,
        sections=sections,
        contact_info=contact_info,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    return schemas.ResumeParseResponse(
        resume_id=resume.id,
        skills=skills,
        experience_years=exp_years,
        sections=sections,
        contact_info=schemas.ContactInfo(**contact_info),
    )
