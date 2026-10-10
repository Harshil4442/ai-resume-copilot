from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from ...database import Base
from ..common import utcnow


class ResumeUpload(Base):
    __tablename__ = "resume_uploads"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_sha256", name="uq_resume_upload_owner_key"),
        CheckConstraint("size_bytes BETWEEN 1 AND 5242880", name="ck_resume_upload_size"),
        CheckConstraint("source_format IN ('pdf','docx')", name="ck_resume_upload_format"),
        CheckConstraint("state IN ('awaiting_upload','queued','inspecting','released','rejected','failed','cancelled','expired')", name="ck_resume_upload_state"),
        CheckConstraint("attempt_count BETWEEN 0 AND 3", name="ck_resume_upload_attempts"),
        Index("ix_resume_upload_due", "state", "next_attempt_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    idempotency_sha256 = Column(String(64), nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    original_filename = Column(String(255), nullable=True)
    source_format = Column(String(8), nullable=False)
    media_type = Column(String(160), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    quarantine_bucket = Column(String(63), nullable=False)
    quarantine_name = Column(String(180), nullable=False)
    clean_bucket = Column(String(63), nullable=False)
    clean_name = Column(String(180), nullable=False)
    quarantine_generation = Column(String(20), nullable=True)
    clean_generation = Column(String(20), nullable=True)
    scan_receipt = Column(JSON, nullable=True)
    enrich_skills = Column(Boolean, nullable=False, default=False)
    state = Column(String(24), nullable=False, default="awaiting_upload")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    upload_grant_expires_at = Column(DateTime(timezone=True), nullable=False)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    attempt_count = Column(Integer, nullable=False, default=0)
    last_error_code = Column(String(64), nullable=True)
    result_resume_id = Column(Integer, ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class ResumeUploadCleanup(Base):
    """Detached cleanup intent survives customer and resume deletion."""
    __tablename__ = "resume_upload_cleanups"
    id = Column(String(64), primary_key=True)
    upload_id = Column(String(64), nullable=False, unique=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    attempt_count = Column(Integer, nullable=False, default=0)
    last_error_code = Column(String(64), nullable=True)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    quarantine_closed_generation = Column(String(20), nullable=True)
    clean_closed_generation = Column(String(20), nullable=True)
    payload_retired_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
