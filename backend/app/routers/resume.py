from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..services.parsing import parse_resume_file
from ..security import get_current_user

router = APIRouter(prefix="/resume", tags=["resume"])

ALLOWED_TYPES = {
    "application/pdf",
    "application/octet-stream",                                          # generic binary (some browsers)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",                                                # .doc (we'll reject gracefully)
}


@router.post("/parse", response_model=schemas.ResumeParseResponse)
async def parse_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
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
