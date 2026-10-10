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
from ..services.document_ingestion import DocumentInspectionError, inspect_resume_document
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
                "application/x-tex": {"schema": {"type": "string", "format": "binary"}},
                "application/zip": {"schema": {"type": "string", "format": "binary"}},
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
    native_media = {"tex": "application/x-tex", "texzip": "application/zip"}
    media_type = native_media.get(resume.source_format) or (
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
    "application/x-tex", "text/x-tex", "text/plain", "application/zip", "application/x-zip-compressed",
    "application/octet-stream",                                          # generic binary (some browsers)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
}

# Upload size cap (bytes). Resumes >5MB are almost always images-as-PDF
# and would blow up DB storage + LLM context.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_DOCX_ENTRIES = 500


def _resume_upload_kind(filename: str, content_type: str) -> tuple[str, str]:
    safe_name = Path(filename.replace("\\", "/")).name.strip()[:255]
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".tex", ".zip"} or content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Upload PDF, DOCX, a UTF-8 TeX file or a supported multi-file TeX ZIP project.")
    return safe_name, {".pdf": "pdf", ".docx": "docx", ".tex": "tex", ".zip": "texzip"}[suffix]


def _validated_resume_upload(filename: str, content_type: str, data: bytes) -> tuple[str, str]:
    safe_name, kind = _resume_upload_kind(filename, content_type)
    suffix = Path(safe_name).suffix.lower()

    if suffix in {".tex", ".zip"}:
        from ..services.native_tex import read_project
        from ..services.resume_layout import ResumeLayoutError
        kind = "tex" if suffix == ".tex" else "texzip"
        try:
            read_project(data, kind)
        except ResumeLayoutError as exc:
            raise HTTPException(422, str(exc)) from exc
        return safe_name, kind

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
    owner_id = current_user.id
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
    # Determine only metadata in the API process. ZIP/XML/PDF inspection and
    # deterministic extraction for PDF/DOCX belong to the private scan worker.
    filename, source_format = _resume_upload_kind(filename, content_type)
    from ..services.resume_layout import ResumeLayoutError
    try:
        if source_format in {"pdf", "docx"}:
            raw_text, sections, skills, exp_years, contact_info = await run_in_threadpool(
                inspect_resume_document, file_bytes, source_format=source_format
            )
        else:
            # Native projects retain their separate compiler path. Their full
            # quarantine/scan and post-compile isolation gate remains open.
            _validated_resume_upload(filename, content_type, file_bytes)
            raw_text, sections, skills, exp_years, contact_info = await run_in_threadpool(
                parse_resume_file, file_bytes, filename=filename, use_llm=False
            )
    except DocumentInspectionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except ResumeLayoutError as exc:
        raise HTTPException(422, str(exc)) from exc

    resume = models.Resume(
        user_id=owner_id,
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
    parsed_resume_id = resume.id
    parsed_source_available = bool(resume.source_available)
    parsed_source_format = resume.source_format

    enrichment_state = "not_requested"
    enrichment_units = 0
    warnings: list[str] = []
    if not raw_text.strip():
        warnings.append("No readable text was found. A scanned or complex file may not support source-preserving tailoring. Keep the unchanged original/custom file, or upload an editable DOCX or TeX source; no ATS parsing guarantee is made.")
    from ..services.generation_gate import generation_enabled

    if enrich_skills and not generation_enabled():
        enrichment_state = "unavailable"
        warnings.append("Optional AI enrichment is temporarily paused. Your original resume was parsed without AI and no enrichment units were charged.")
    elif enrich_skills:
        from ..services.guardrails import OptionalGenerationUnavailable, billable_operation
        from ..services.result_commit import begin_result_commit

        class NoUsefulEnrichment(ValueError):
            pass

        try:
            with billable_operation(
                user_id=owner_id, db=db, operation="resume_enrichment", amount=1,
                input_payload={"resume_id": parsed_resume_id, "source_sha256": hashlib.sha256(file_bytes).hexdigest(), "mode": "enhanced"},
            ) as enrichment_run:
                enrichment_run_id = enrichment_run.id
                enriched = await run_in_threadpool(enrich_resume_skills, raw_text, skills)
                begin_result_commit(db, owner_id, enrichment_run_id)
                resume = db.query(models.Resume).filter_by(id=parsed_resume_id, user_id=owner_id).populate_existing().with_for_update().one_or_none()
                if resume is None:
                    raise HTTPException(status_code=404, detail="Resume not found")
                if enriched == skills:
                    raise NoUsefulEnrichment("No additional supported skills were found")
                resume.skills = enriched
                db.flush()
                # Snapshot the charge while the result/run locks are held;
                # commit expires ORM objects and erasure may win immediately
                # afterward. No post-commit customer reload is needed.
                enrichment_units = 0 if enrichment_run.usage_state == "waived" else enrichment_run.estimated_units
            skills = enriched
            enrichment_state = "completed"
        except NoUsefulEnrichment:
            enrichment_state = "unchanged"
            warnings.append("Enrichment found no additional source-supported skills. No analysis units were charged.")
        except OptionalGenerationUnavailable:
            enrichment_state = "unavailable"
            warnings.append("Optional AI enrichment is unavailable because its reviewed model-cost authorization is not ready. Your original resume was parsed without AI; no enrichment units were charged.")
        except HTTPException as exc:
            if exc.status_code != 402:
                raise
            enrichment_state = "insufficient_units"
            warnings.append("Optional enrichment requires 1 analysis unit. Your original resume was parsed without AI.")
        except Exception:
            enrichment_state = "failed"
            warnings.append("Optional enrichment was unavailable. Your original resume was parsed without AI and no enrichment units were charged.")

    return schemas.ResumeParseResponse(
        resume_id=parsed_resume_id,
        skills=skills,
        experience_years=exp_years,
        sections=sections,
        contact_info=schemas.ContactInfo(**contact_info),
        source_available=parsed_source_available,
        source_format=parsed_source_format,
        extraction_mode="enriched" if enrichment_state == "completed" else "deterministic",
        enrichment_state=enrichment_state,
        enrichment_units=enrichment_units,
        warnings=warnings,
    )
