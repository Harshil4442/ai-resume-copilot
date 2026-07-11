import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..services.parsing import parse_resume_file
from ..security import get_current_user
from ..rate_limiter import limiter

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
MAX_DOCX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_DOCX_ENTRIES = 500


def _validated_resume_upload(filename: str, content_type: str, data: bytes) -> tuple[str, str]:
    safe_name = Path(filename.replace("\\", "/")).name.strip()[:255]
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".pdf", ".docx"} or content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Only PDF and DOCX files are supported.")

    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF.")
        return safe_name, "pdf"

    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid DOCX document.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if len(entries) > MAX_DOCX_ENTRIES or not {
                "[Content_Types].xml",
                "word/document.xml",
            }.issubset(names):
                raise HTTPException(status_code=400, detail="The DOCX structure is not supported.")
            total_size = 0
            for entry in entries:
                path = entry.filename.replace("\\", "/")
                if path.startswith("/") or ".." in path.split("/") or entry.flag_bits & 0x1:
                    raise HTTPException(status_code=400, detail="The DOCX archive is not supported.")
                total_size += entry.file_size
                if total_size > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise HTTPException(status_code=413, detail="The DOCX expands beyond the safe limit.")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid DOCX document.")
    return safe_name, "docx"


@router.post("/parse", response_model=schemas.ResumeParseResponse)
@limiter.limit("5/minute")
async def parse_resume(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    filename = file.filename or ""
    content_type = file.content_type or ""

    # Read one byte beyond the cap so oversized uploads are rejected without
    # buffering an unbounded request body in application memory.
    file_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit.",
        )
    filename, _kind = _validated_resume_upload(filename, content_type, file_bytes)
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
