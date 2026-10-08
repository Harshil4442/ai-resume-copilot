"""Employer domain persistence, separate from career-analysis accounting.

Service credits are prepaid money-backed units. Promotional analysis units and
Premium access never authorize a free employer-service operation.
"""
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import deferred

from ...database import Base
from ..common import utcnow


class EmployerSource(Base):
    __tablename__ = "employer_sources"
    __table_args__ = (
        UniqueConstraint("platform", "region", "board_token", name="uq_employer_source_tenant"),
        Index("ix_employer_sources_due", "enabled", "next_refresh_at"),
    )
    id = Column(String(64), primary_key=True)
    employer = Column(String(200), nullable=False)
    # Operator-verified identity may group multiple ATS tenants; never inferred by AI.
    employer_key = Column(String(120), nullable=True, index=True)
    admission_policy = Column(JSON, nullable=True)
    platform = Column(String(40), nullable=False)
    board_token = Column(String(120), nullable=False)
    region = Column(String(16), nullable=False, default="global")
    careers_url = Column(Text, nullable=False)
    allowed_hosts = Column(JSON, nullable=False, default=list)
    verification_url = Column(Text, nullable=False)
    verification_note = Column(String(1000), nullable=False)
    verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    enabled = Column(Boolean, nullable=False, default=True)
    # Public read access is separate from a contracted, scoped write grant.
    submission_enabled = Column(Boolean, nullable=False, default=False)
    submission_grant = Column(String(500), nullable=True)
    credential_env = Column(String(120), nullable=True)
    form_parity_verified = Column(Boolean, nullable=False, default=False)
    receipt_contract = Column(JSON, nullable=True)
    status = Column(String(24), nullable=False, default="pending")
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(80), nullable=True)
    next_refresh_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    scan_token = Column(String(64), nullable=True)
    scan_started_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class EmployerPosting(Base):
    __tablename__ = "employer_postings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_employer_source_posting"),
        Index("ix_employer_postings_open_checked", "is_open", "last_checked_at"),
        Index("ix_employer_postings_location", "location"),
    )
    id = Column(String(64), primary_key=True)
    source_id = Column(String(64), ForeignKey("employer_sources.id"), nullable=False, index=True)
    external_id = Column(String(160), nullable=False)
    requisition_id = Column(String(160), nullable=True)
    opening_key = Column(String(64), nullable=True, index=True)
    title = Column(String(300), nullable=False, index=True)
    employer = Column(String(200), nullable=False)
    location = Column(String(400), nullable=False, default="")
    country = Column(String(2), nullable=True)
    remote = Column(Boolean, nullable=False, default=False)
    description = Column(Text, nullable=False)
    skills = Column(JSON, nullable=False, default=list)
    canonical_url = Column(Text, nullable=False)
    apply_url = Column(Text, nullable=False)
    language = Column(String(20), nullable=False, default="en")
    publication_at = Column(DateTime(timezone=True), nullable=True)
    source_updated_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_checked_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    content_sha256 = Column(String(64), nullable=False)
    is_open = Column(Boolean, nullable=False, default=True)


class ServiceCreditEvent(Base):
    __tablename__ = "service_credit_events"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_service_credit_user_key"),
        Index("ix_service_credit_user_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    event_type = Column(String(24), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    idempotency_key = Column(String(200), nullable=False)
    source_type = Column(String(48), nullable=False)
    source_id = Column(String(120), nullable=False)
    reason = Column(String(240), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ServiceCreditReservation(Base):
    __tablename__ = "service_credit_reservations"
    __table_args__ = (UniqueConstraint("operation", "source_id", name="uq_service_reservation_source"),)
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    operation = Column(String(40), nullable=False)
    source_id = Column(String(64), nullable=False)
    unit_price = Column(Integer, nullable=False)
    requested_count = Column(Integer, nullable=False)
    reserved_amount = Column(Integer, nullable=False)
    committed_amount = Column(Integer, nullable=False, default=0)
    released_amount = Column(Integer, nullable=False, default=0)
    state = Column(String(24), nullable=False, default="reserved")
    pricing_version = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    settled_at = Column(DateTime(timezone=True), nullable=True)


class EmployerSearch(Base):
    __tablename__ = "employer_searches"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_employer_search_user_key"),
        Index("ix_employer_search_user_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    query = Column(JSON, nullable=False)
    status = Column(String(24), nullable=False, default="completed")
    desired_count = Column(Integer, nullable=False)
    delivered_count = Column(Integer, nullable=False, default=0)
    reserved_credits = Column(Integer, nullable=False, default=0)
    charged_credits = Column(Integer, nullable=False, default=0)
    refunded_credits = Column(Integer, nullable=False, default=0)
    items = Column(JSON, nullable=False, default=list)
    scope = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class EmployerJobDelivery(Base):
    __tablename__ = "employer_job_deliveries"
    __table_args__ = (
        UniqueConstraint("user_id", "posting_id", name="uq_employer_delivery_user_posting"),
        UniqueConstraint("user_id", "opening_key", name="uq_employer_delivery_user_opening"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    posting_id = Column(String(64), ForeignKey("employer_postings.id"), nullable=False)
    opening_key = Column(String(64), nullable=True, index=True)
    search_id = Column(String(64), ForeignKey("employer_searches.id"), nullable=False)
    charged_credits = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class SealedApplicationArtifact(Base):
    __tablename__ = "sealed_application_artifacts"
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=False)
    resume_version_id = Column(String(64), ForeignKey("resume_versions.id"), nullable=True)
    sha256 = Column(String(64), nullable=False)
    filename = Column(String(240), nullable=False)
    media_type = Column(String(160), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    # Inline bytes only in development/tests. Production uses private immutable GCS generations.
    content = deferred(Column(LargeBinary, nullable=True))
    gcs_object = Column(Text, nullable=True)
    gcs_generation = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class EmployerApplication(Base):
    __tablename__ = "employer_applications"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_employer_application_user_key"),
        Index("ix_employer_application_user_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    posting_id = Column(String(64), ForeignKey("employer_postings.id"), nullable=False, index=True)
    opening_key = Column(String(64), nullable=True, index=True)
    employer_key = Column(String(64), nullable=True, index=True)
    admission_snapshot = Column(JSON, nullable=True)
    pricing_snapshot = Column(JSON, nullable=True)
    batch_id = Column(String(64), ForeignKey("employer_application_batches.id"), nullable=True, index=True)
    idempotency_key = Column(String(160), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    # Cleared on safely cancelled/failed intents; retained for confirmed/unknown sends.
    active_key = Column(String(160), unique=True, nullable=True, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=False)
    resume_choice = Column(String(16), nullable=False)
    resume_version_id = Column(String(64), ForeignKey("resume_versions.id"), nullable=True)
    artifact_id = Column(String(64), ForeignKey("sealed_application_artifacts.id"), nullable=False)
    form = Column(JSON, nullable=False)
    answers = Column(JSON, nullable=False, default=dict)
    consents = Column(JSON, nullable=False, default=dict)
    package_digest = Column(String(64), nullable=False)
    job_content_sha256 = Column(String(64), nullable=False)
    application_mode = Column(String(24), nullable=False, default="manual")
    status = Column(String(32), nullable=False, default="needs_action")
    allowed_actions = Column(JSON, nullable=False, default=list)
    approved_digest = Column(String(64), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approval_expires_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    credit_cost = Column(Integer, nullable=False)
    charged_credits = Column(Integer, nullable=False, default=0)
    reservation_id = Column(String(64), ForeignKey("service_credit_reservations.id"), nullable=True)
    receipt = Column(JSON, nullable=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class EmployerApplicationBatch(Base):
    __tablename__ = "employer_application_batches"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_employer_batch_user_key"),)
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    idempotency_key = Column(String(160), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    package_digest = Column(String(64), nullable=False)
    # Immutable enumerated packages, actions, prices and admission configuration.
    items = Column(JSON, nullable=False)
    admission_snapshot = Column(JSON, nullable=False)
    quoted_credits = Column(Integer, nullable=False)
    max_total_credits = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default="quoted")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)


class EmployerAdmission(Base):
    """A pending slot or possible-send budget; uncertainty never expires itself."""
    __tablename__ = "employer_admissions"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_employer_admission_application"),
        Index("ix_employer_admission_user_window", "user_id", "state", "possible_send_at"),
        Index("ix_employer_admission_employer_window", "user_id", "employer_key", "state", "possible_send_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    application_id = Column(String(64), ForeignKey("employer_applications.id", ondelete="SET NULL"), nullable=True)
    employer_key = Column(String(64), nullable=False)
    opening_key = Column(String(64), nullable=False)
    active_key = Column(String(160), unique=True, nullable=True)
    credit_cost = Column(Integer, nullable=False)
    policy_snapshot = Column(JSON, nullable=False)
    state = Column(String(24), nullable=False, default="reserved")
    admitted_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    possible_send_at = Column(DateTime(timezone=True), nullable=True)
    settled_at = Column(DateTime(timezone=True), nullable=True)
    release_reason = Column(String(80), nullable=True)


class EmployerApplicationAttempt(Base):
    __tablename__ = "employer_application_attempts"
    id = Column(String(64), primary_key=True)
    application_id = Column(String(64), ForeignKey("employer_applications.id"), nullable=False, index=True)
    launch_token = Column(String(64), unique=True, nullable=False)
    package_digest = Column(String(64), nullable=False)
    state = Column(String(24), nullable=False, default="submitting")
    response_status = Column(Integer, nullable=True)
    receipt = Column(JSON, nullable=True)
    error_code = Column(String(80), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class EmployerApplicationApproval(Base):
    __tablename__ = "employer_application_approvals"
    id = Column(String(64), primary_key=True)
    application_id = Column(String(64), ForeignKey("employer_applications.id"), nullable=False, index=True)
    package_digest = Column(String(64), nullable=False)
    review_snapshot = Column(JSON, nullable=False)
    allowed_actions = Column(JSON, nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class EmployerArtifactDeletion(Base):
    __tablename__ = "employer_artifact_deletions"
    id = Column(String(64), primary_key=True)
    gcs_object = Column(Text, nullable=False)
    gcs_generation = Column(String(80), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    attempt_count = Column(Integer, nullable=False, default=0)
    last_error_code = Column(String(80), nullable=True)
    absence_confirmed_at = Column(DateTime(timezone=True), nullable=True)


class EmployerArtifactUpload(Base):
    """A cleanup guard committed before an object can be uploaded to GCS."""
    __tablename__ = "employer_artifact_uploads"
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    gcs_object = Column(Text, nullable=False)
    gcs_generation = Column(String(80), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
