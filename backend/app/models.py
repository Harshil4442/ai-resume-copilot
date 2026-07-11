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
)
from sqlalchemy.orm import relationship
from .database import Base


def _utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String, default="")
    tier = Column(String, default="free")
    ai_credits = Column(Integer, default=20, nullable=False)
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
