import hashlib
import io
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import models, schemas
from ..database import get_db
from ..rate_limiter import limiter
from ..security import get_current_user
from ..services.parsing import enrich_resume_skills, parse_resume_file

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
                source_available=bool(r.source_available),
                source_format=r.source_format,
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
        source_available=bool(resume.source_available),
        source_format=resume.source_format,
    )


@router.get(
    "/{resume_id}/source",
    response_class=Response,
    responses={
        200: {
            "description": "Original uploaded resume",
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                    "schema": {"type": "string", "format": "binary"}
                },
            },
        }
    },
)
def download_resume_source(
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
    if not resume.source_available:
        raise HTTPException(
            status_code=409,
            detail="The original file is unavailable. Upload this resume again to preserve its formatting.",
        )

    filename = resume.original_filename or f"resume-{resume.id}.{resume.source_format}"
    safe_filename = Path(filename.replace("\\", "/")).name
    ascii_filename = "".join(
        character if 32 <= ord(character) < 127 and character not in {'"', "\\"} else "_"
        for character in safe_filename
    )
    media_type = (
        "application/pdf"
        if resume.source_format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return Response(
        content=resume.source_document,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{quote(safe_filename, safe='')}"
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
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
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid DOCX document.") from exc
    return safe_name, "docx"


@router.post("/parse", response_model=schemas.ResumeParseResponse)
@limiter.limit("5/minute")
async def parse_resume(
    request: Request,
    file: UploadFile = File(...),
    enrich_skills: bool = Form(False),
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
    filename, source_format = _validated_resume_upload(filename, content_type, file_bytes)
    raw_text, sections, skills, exp_years, contact_info = await run_in_threadpool(
        parse_resume_file, file_bytes, filename=filename, use_llm=False
    )

    resume = models.Resume(
        user_id=current_user.id,
        original_filename=filename,
        raw_text=raw_text,
        source_document=file_bytes,
        source_format=source_format,
        skills=skills,
        experience_years=exp_years,
        sections=sections,
        contact_info=contact_info,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    enrichment_state = "not_requested"
    enrichment_units = 0
    warnings: list[str] = []
    if enrich_skills:
        from ..services.guardrails import billable_operation

        class NoUsefulEnrichment(ValueError):
            pass

        try:
            with billable_operation(
                user_id=current_user.id, db=db, operation="resume_enrichment", amount=1,
                input_payload={"resume_id": resume.id, "source_sha256": hashlib.sha256(file_bytes).hexdigest(), "mode": "enhanced"},
            ) as enrichment_run:
                enriched = await run_in_threadpool(enrich_resume_skills, raw_text, skills)
                if enriched == skills:
                    raise NoUsefulEnrichment("No additional supported skills were found")
                resume.skills = enriched
                db.flush()
            skills = enriched
            enrichment_state = "completed"
            enrichment_units = enrichment_run.committed_units
        except NoUsefulEnrichment:
            enrichment_state = "unchanged"
            warnings.append("Enrichment found no additional source-supported skills. No analysis units were charged.")
        except HTTPException as exc:
            if exc.status_code != 402:
                raise
            enrichment_state = "insufficient_units"
            warnings.append("Optional enrichment requires 1 analysis unit. Your original resume was parsed without AI.")
        except Exception:
            enrichment_state = "failed"
            warnings.append("Optional enrichment was unavailable. Your original resume was parsed without AI and no enrichment units were charged.")

    return schemas.ResumeParseResponse(
        resume_id=resume.id,
        skills=skills,
        experience_years=exp_years,
        sections=sections,
        contact_info=schemas.ContactInfo(**contact_info),
        source_available=bool(resume.source_available),
        source_format=resume.source_format,
        extraction_mode="enriched" if enrichment_state == "completed" else "deterministic",
        enrichment_state=enrichment_state,
        enrichment_units=enrichment_units,
        warnings=warnings,
    )
