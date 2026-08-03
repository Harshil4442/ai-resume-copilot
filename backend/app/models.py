from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from .database import Base


FREE_SIGNUP_ANALYSIS_UNITS = 50


def _utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String, default="")
    tier = Column(String, default="free")
    ai_credits = Column(Integer, default=FREE_SIGNUP_ANALYSIS_UNITS, nullable=False)
    # When premium access expires. NULL while free; NULL on a legacy/lifetime
    # premium grant is treated as still-active.
    premium_until = Column(DateTime, nullable=True)
    terms_accepted_at = Column(DateTime, nullable=True)
    terms_version = Column(String(32), nullable=True)
    privacy_version = Column(String(32), nullable=True)
    age_confirmed_at = Column(DateTime, nullable=True)

    profile = relationship("UserProfile", back_populates="user", uselist=False)
    resumes = relationship("Resume", back_populates="user")
    job_matches = relationship("JobMatch", back_populates="user")

    def is_premium_active(self) -> bool:
        """True when the user has an unexpired premium grant."""
        if self.tier != "premium":
            return False
        if self.premium_until is None:
            return True  # legacy lifetime grant
        expires = self.premium_until
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > datetime.now(timezone.utc)


class PaymentOrder(Base):
    """Provider-neutral, server-priced checkout order.

    ``user_id`` is deliberately nullable. Payment/accounting records are
    retained after account deletion while the customer link is removed.
    Entitlements are recorded separately and are never inferred from a browser
    redirect.
    """
    __tablename__ = "payment_orders"

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    provider = Column(String(32), nullable=False, default="razorpay")
    provider_mode = Column(String(16), nullable=False, default="live")
    # Razorpay key IDs are public checkout identifiers. Persisting the ID lets
    # us abandon an open order safely when keys rotate without storing secrets.
    provider_key_id = Column(String(120), nullable=True)
    provider_order_id = Column(String(120), unique=True, nullable=True, index=True)
    provider_customer_id = Column(String(120), nullable=True)
    provider_subscription_id = Column(String(120), nullable=True)
    sku = Column(String(64), nullable=False)
    catalog_version = Column(String(64), nullable=False)
    billing_type = Column(String(32), nullable=False, default="one_time")
    # Immutable fulfilment snapshot. Delayed webhooks must grant what was
    # purchased, even after the current catalog changes or removes the SKU.
    entitlement_kind = Column(String(40), nullable=False)
    entitlement_quantity = Column(Integer, nullable=False)
    billing_country = Column(String(2), nullable=False, default="IN")
    billing_country_confirmed_at = Column(DateTime, nullable=False)
    gross_amount_minor = Column(Integer, nullable=False)
    # Product/customer tax is not supplied by Razorpay's Payment entity. Keep
    # it separate from Razorpay's own GST-on-processing-fee field.
    customer_tax_amount_minor = Column(Integer, nullable=True)
    provider_fee_amount_minor = Column(Integer, nullable=True)
    provider_fee_tax_minor = Column(Integer, nullable=True)
    # Gross less the provider fee reported at capture; not a settled-bank
    # amount. Actual settlements require separate reconciliation records.
    estimated_net_amount_minor = Column(Integer, nullable=True)
    refunded_amount_minor = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="INR")
    status = Column(String(32), nullable=False, default="initializing")
    # At most one open purchase attempt per user/SKU. It is cleared on every
    # terminal transition. NULL values remain reusable on SQLite/PostgreSQL.
    active_attempt_key = Column(String(160), unique=True, nullable=True, index=True)
    client_confirmed_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    refunded_at = Column(DateTime, nullable=True)
    customer_deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class PaymentTransaction(Base):
    """Normalized provider payment metadata; never stores card/bank secrets."""

    __tablename__ = "payment_transactions"
    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_payment_provider_payment"),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("payment_orders.id"), nullable=False, index=True)
    provider = Column(String(32), nullable=False)
    provider_payment_id = Column(String(120), nullable=False, index=True)
    entity_type = Column(String(32), nullable=False, default="payment")
    status = Column(String(32), nullable=False)
    gross_amount_minor = Column(Integer, nullable=False)
    customer_tax_amount_minor = Column(Integer, nullable=True)
    provider_fee_amount_minor = Column(Integer, nullable=True)
    provider_fee_tax_minor = Column(Integer, nullable=True)
    estimated_net_amount_minor = Column(Integer, nullable=True)
    refunded_amount_minor = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), nullable=False)
    payment_method = Column(String(32), nullable=True)
    instrument_international = Column(Boolean, nullable=True)
    captured_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class PaymentRefund(Base):
    """One row per provider refund, protecting partial-refund accounting."""

    __tablename__ = "payment_refunds"
    __table_args__ = (
        UniqueConstraint("provider", "provider_refund_id", name="uq_payment_provider_refund"),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("payment_orders.id"), nullable=False, index=True)
    transaction_id = Column(Integer, ForeignKey("payment_transactions.id"), nullable=False, index=True)
    provider = Column(String(32), nullable=False)
    provider_refund_id = Column(String(120), nullable=False, index=True)
    amount_minor = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False)
    status = Column(String(32), nullable=False)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class PaymentEvent(Base):
    """Webhook replay ledger. Only a payload digest is retained, not raw JSON."""

    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_payment_provider_event"),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("payment_orders.id"), nullable=True, index=True)
    provider = Column(String(32), nullable=False)
    provider_event_id = Column(String(160), nullable=False, index=True)
    event_type = Column(String(80), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    processing_status = Column(String(32), nullable=False, default="received")
    error_code = Column(String(80), nullable=True)
    provider_created_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, default=_utcnow)
    processed_at = Column(DateTime, nullable=True)


class EntitlementLedger(Base):
    """Authoritative provider-independent record of paid access grants."""

    __tablename__ = "entitlement_ledger"
    __table_args__ = (
        UniqueConstraint("source_order_id", name="uq_entitlement_source_order"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    source_order_id = Column(Integer, ForeignKey("payment_orders.id"), nullable=False, index=True)
    source_provider = Column(String(32), nullable=False)
    sku = Column(String(64), nullable=False)
    entitlement_kind = Column(String(40), nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="active")
    starts_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    granted_at = Column(DateTime, default=_utcnow)
    revoked_at = Column(DateTime, nullable=True)

class UserProfile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    full_name = Column(String, default="")
    headline = Column(String, default="")
    phone = Column(String, default="")
    location = Column(String, default="")
    linkedin = Column(String, default="")
    github = Column(String, default="")
    portfolio = Column(String, default="")
    target_role = Column(String, default="")
    preferred_job_type = Column(String, default="")
    preferred_location = Column(String, default="")
    years_experience = Column(Float, default=0.0)
    bio = Column(Text, default="")
    skills = Column(JSON, default=list)
    education = Column(Text, default="")
    certifications = Column(Text, default="")

    user = relationship("User", back_populates="profile")

class Resume(Base):
    __tablename__ = "resumes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=_utcnow)

    original_filename = Column(String, default="")
    raw_text = Column(Text, default="")

    skills = Column(JSON, default=list)
    experience_years = Column(Float, default=0.0)
    sections = Column(JSON, default=dict)
    contact_info = Column(JSON, default=dict)

    user = relationship("User", back_populates="resumes")

class JobMatch(Base):
    __tablename__ = "job_matches"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    resume_id = Column(Integer, ForeignKey("resumes.id"))
    created_at = Column(DateTime, default=_utcnow)

    job_title        = Column(String, default="")
    company          = Column(String, default="")
    job_description  = Column(Text, default="")

    required_skills  = Column(JSON, default=list)
    full_matches     = Column(JSON, default=list)  # direct + full coverage skills
    partial_matches  = Column(JSON, default=list)  # [{skill, coverage, via}]
    true_gaps        = Column(JSON, default=list)  # no meaningful coverage
    match_score      = Column(Float, default=0.0)
    fit_summary           = Column(Text, default="")
    dimension_scores      = Column(JSON, default=list)  # [{name,score,feedback}]
    skill_verification_rate = Column(Float, default=0.0)
    improvement_tips      = Column(JSON, default=list)

    user   = relationship("User", back_populates="job_matches")
    resume = relationship("Resume")


class SkillCoverage(Base):
    """
    Persistent DB cache for LLM-computed pairwise skill coverage weights.
    Written once per unique (skill_from, skill_to) pair, read on every subsequent request.
    """
    __tablename__ = "skill_coverage"

    skill_from = Column(String(120), primary_key=True)
    skill_to   = Column(String(120), primary_key=True)
    weight     = Column(Float, nullable=False)   # 0.0 to 1.0
    source     = Column(String(20), default="llm")
    created_at = Column(DateTime, default=_utcnow)


class Opportunity(Base):
    """A user's durable workspace for one target role."""

    __tablename__ = "opportunities"
    __table_args__ = (
        Index("ix_opportunities_user_stage", "user_id", "stage"),
        Index("ix_opportunities_user_updated", "user_id", "updated_at"),
    )

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=True, index=True)
    latest_match_id = Column(Integer, ForeignKey("job_matches.id"), nullable=True)
    latest_analysis_run_id = Column(String(64), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    company = Column(String(200), nullable=False, default="")
    location = Column(String(200), nullable=False, default="")
    source = Column(String(80), nullable=False, default="manual")
    source_url = Column(Text, nullable=True)
    job_description = Column(Text, nullable=False, default="")
    job_snapshot = Column(JSON, nullable=False, default=dict)
    stage = Column(String(32), nullable=False, default="saved")
    priority = Column(String(16), nullable=False, default="medium")
    compensation = Column(String(160), nullable=True)
    deadline_at = Column(DateTime, nullable=True)
    next_action = Column(String(240), nullable=True)
    notes = Column(Text, nullable=False, default="")
    outcome = Column(String(32), nullable=True)
    outcome_notes = Column(Text, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    outcome_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class AnalysisRun(Base):
    """Durable and idempotent execution state for expensive operations."""

    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_analysis_run_user_idempotency"),
        Index("ix_analysis_runs_user_status", "user_id", "status"),
        Index("ix_analysis_runs_status_created", "status", "created_at"),
    )

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    opportunity_id = Column(String(64), ForeignKey("opportunities.id"), nullable=True, index=True)
    operation = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="queued")
    idempotency_key = Column(String(160), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    input_payload = Column(JSON, nullable=False, default=dict)
    result_payload = Column(JSON, nullable=True)
    result_artifact_ref = Column(Text, nullable=True)
    estimated_units = Column(Integer, nullable=False, default=0)
    committed_units = Column(Integer, nullable=False, default=0)
    usage_state = Column(String(24), nullable=False, default="pending")
    provider = Column(String(80), nullable=True)
    model = Column(String(120), nullable=True)
    prompt_version = Column(String(64), nullable=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(String(500), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    input_purged_at = Column(DateTime, nullable=True)
    result_purged_at = Column(DateTime, nullable=True)


class UsageEvent(Base):
    """Append-only audit record for analysis-unit mutations."""

    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_usage_event_user_idempotency"),
        Index("ix_usage_events_user_created", "user_id", "created_at"),
    )

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    analysis_run_id = Column(String(64), ForeignKey("analysis_runs.id"), nullable=True, index=True)
    entitlement_id = Column(Integer, ForeignKey("entitlement_ledger.id"), nullable=True)
    event_type = Column(String(24), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    idempotency_key = Column(String(200), nullable=False)
    source_type = Column(String(64), nullable=False, default="analysis_run")
    source_id = Column(String(120), nullable=True)
    actor = Column(String(80), nullable=False, default="system")
    reason = Column(String(240), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (Index("ix_application_events_opportunity_occurred", "opportunity_id", "occurred_at"),)

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    opportunity_id = Column(String(64), ForeignKey("opportunities.id"), nullable=False, index=True)
    event_type = Column(String(48), nullable=False)
    from_stage = Column(String(32), nullable=True)
    to_stage = Column(String(32), nullable=True)
    note = Column(Text, nullable=True)
    source = Column(String(40), nullable=False, default="user")
    resume_version_id = Column(String(64), nullable=True)
    occurred_at = Column(DateTime, nullable=False, default=_utcnow)
    recorded_at = Column(DateTime, nullable=False, default=_utcnow)


class ResumeVersion(Base):
    __tablename__ = "resume_versions"
    __table_args__ = (
        UniqueConstraint("resume_id", "version_number", name="uq_resume_version_number"),
        Index("ix_resume_versions_user_created", "user_id", "created_at"),
    )

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=False, index=True)
    opportunity_id = Column(String(64), ForeignKey("opportunities.id"), nullable=True, index=True)
    version_number = Column(Integer, nullable=False)
    label = Column(String(160), nullable=False, default="Resume version")
    structured_content = Column(JSON, nullable=False, default=dict)
    rendered_artifact_ref = Column(Text, nullable=True)
    evidence_ids = Column(JSON, nullable=False, default=list)
    generation_run_id = Column(String(64), ForeignKey("analysis_runs.id"), nullable=True)
    approval_state = Column(String(24), nullable=False, default="draft")
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (Index("ix_evidence_items_user_category", "user_id", "category"),)

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=True, index=True)
    category = Column(String(48), nullable=False)
    title = Column(String(180), nullable=False)
    evidence_text = Column(Text, nullable=False)
    metrics = Column(JSON, nullable=False, default=dict)
    skills = Column(JSON, nullable=False, default=list)
    provenance = Column(String(40), nullable=False, default="user")
    source_ref = Column(Text, nullable=True)
    approval_state = Column(String(24), nullable=False, default="pending")
    confidence = Column(String(16), nullable=False, default="user_provided")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_user_due", "user_id", "due_at"),)

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    opportunity_id = Column(String(64), ForeignKey("opportunities.id"), nullable=True, index=True)
    reminder_type = Column(String(40), nullable=False, default="follow_up")
    message = Column(String(300), nullable=False)
    due_at = Column(DateTime, nullable=False)
    status = Column(String(24), nullable=False, default="scheduled")
    delivery_channel = Column(String(24), nullable=False, default="in_app")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    sent_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    dismissed_at = Column(DateTime, nullable=True)


class OpportunityContact(Base):
    __tablename__ = "opportunity_contacts"

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    opportunity_id = Column(String(64), ForeignKey("opportunities.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    role = Column(String(160), nullable=True)
    email = Column(String(320), nullable=True)
    profile_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class CareerMemoryEntry(Base):
    __tablename__ = "career_memory_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "memory_key", name="uq_career_memory_user_key"),
        Index("ix_career_memory_user_category", "user_id", "category"),
    )

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(48), nullable=False)
    memory_key = Column(String(120), nullable=False)
    value = Column(JSON, nullable=False)
    provenance = Column(String(40), nullable=False, default="user")
    source_ref = Column(Text, nullable=True)
    approval_state = Column(String(24), nullable=False, default="approved")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class ModelCallEvent(Base):
    __tablename__ = "model_call_events"
    __table_args__ = (Index("ix_model_calls_run_created", "analysis_run_id", "created_at"),)

    id = Column(String(64), primary_key=True)
    analysis_run_id = Column(String(64), ForeignKey("analysis_runs.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String(80), nullable=False)
    model = Column(String(120), nullable=False)
    prompt_version = Column(String(64), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    latency_ms = Column(Integer, nullable=False, default=0)
    estimated_cost_micros = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False)
    error_code = Column(String(80), nullable=True)
    cache_status = Column(String(24), nullable=True)
    fallback_from = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("operation", "version", name="uq_prompt_operation_version"),
    )

    id = Column(String(64), primary_key=True)
    operation = Column(String(64), nullable=False, index=True)
    version = Column(String(64), nullable=False)
    template_checksum = Column(String(64), nullable=False)
    output_schema = Column(JSON, nullable=False, default=dict)
    model_config = Column(JSON, nullable=False, default=dict)
    evaluation_result = Column(JSON, nullable=False, default=dict)
    release_status = Column(String(24), nullable=False, default="draft")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    activated_at = Column(DateTime, nullable=True)


class NotificationOutbox(Base):
    """Durable, idempotent lifecycle-message delivery queue."""

    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_notification_outbox_idempotency"),
        Index("ix_notification_outbox_status_available", "status", "available_at"),
    )

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    notification_type = Column(String(64), nullable=False)
    channel = Column(String(24), nullable=False, default="email")
    recipient = Column(String(320), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    idempotency_key = Column(String(200), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime, nullable=False, default=_utcnow)
    claimed_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class AdminAuditEvent(Base):
    """Append-only record for privileged support and accounting actions."""

    __tablename__ = "admin_audit_events"
    __table_args__ = (Index("ix_admin_audit_created", "created_at"),)

    id = Column(String(64), primary_key=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    actor_email = Column(String(320), nullable=False)
    action = Column(String(80), nullable=False)
    target_type = Column(String(64), nullable=False)
    target_id = Column(String(160), nullable=False)
    reason = Column(String(500), nullable=False)
    before_state = Column(JSON, nullable=False, default=dict)
    after_state = Column(JSON, nullable=False, default=dict)
    correlation_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
