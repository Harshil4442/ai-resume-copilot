"""Additive monetary records; legacy balances and accepted orders remain untouched."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
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


class CreditCampaign(Base):
    __tablename__ = "credit_campaigns"
    __table_args__ = (
        UniqueConstraint("version", name="uq_credit_campaign_version"),
        CheckConstraint("ends_at > starts_at", name="ck_credit_campaign_interval"),
    )
    id = Column(String(64), primary_key=True)
    version = Column(String(64), nullable=False)
    catalog_version = Column(String(64), nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    timezone = Column(String(64), nullable=False, default="Asia/Kolkata")
    reference_price_verified = Column(Boolean, nullable=False, default=False)
    reference_evidence_id = Column(String(160), nullable=True)
    reference_approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class CreditPurchaseQuote(Base):
    __tablename__ = "credit_purchase_quotes"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_credit_purchase_quote_key"),
        UniqueConstraint("accepted_order_id", name="uq_credit_quote_order"),
        CheckConstraint("expires_at > created_at", name="ck_credit_purchase_quote_interval"),
        CheckConstraint("state IN ('quoted','accepted')", name="ck_credit_purchase_quote_state"),
        Index("ix_credit_purchase_quote_owner_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    sku = Column(String(64), nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    state = Column(String(16), nullable=False, default="quoted")
    accepted_order_id = Column(String(64), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)


class CreditAccount(Base):
    __tablename__ = "credit_accounts"
    __table_args__ = (CheckConstraint("debt_credits >= 0", name="ck_credit_account_debt"),)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    debt_credits = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class CreditGrant(Base):
    __tablename__ = "credit_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "source_id", name="uq_credit_grant_source"),
        CheckConstraint("quantity > 0", name="ck_credit_grant_quantity"),
        CheckConstraint(
            "remaining_credits >= 0 AND remaining_credits <= quantity",
            name="ck_credit_grant_remaining",
        ),
        CheckConstraint(
            "expired_credits >= 0 AND expired_credits <= quantity", name="ck_credit_grant_expired"
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > issued_at", name="ck_credit_grant_expiry"
        ),
        Index("ix_credit_grant_owner_expiry", "user_id", "expires_at"),
        Index("ix_credit_grant_origin", "user_id", "origin_type", "origin_id"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source_type = Column(String(40), nullable=False)
    source_id = Column(String(160), nullable=False)
    # Restorations keep the purchased origin so a cash refund cannot mint fresh use.
    origin_type = Column(String(40), nullable=False)
    origin_id = Column(String(160), nullable=False)
    funding_kind = Column(String(24), nullable=False)
    quantity = Column(Integer, nullable=False)
    remaining_credits = Column(Integer, nullable=False)
    expired_credits = Column(Integer, nullable=False, default=0)
    issued_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    terms_snapshot = Column(JSON, nullable=False)
    parent_grant_id = Column(String(64), ForeignKey("credit_grants.id"), nullable=True)


class CreditReservation(Base):
    __tablename__ = "credit_reservations"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_credit_reservation_key"),
        UniqueConstraint("user_id", "operation", "source_id", name="uq_credit_operation_source"),
        CheckConstraint("reserved_credits > 0", name="ck_credit_reservation_positive"),
        CheckConstraint(
            "committed_credits >= 0 AND released_credits >= 0 AND committed_credits + released_credits <= reserved_credits",
            name="ck_credit_reservation_amounts",
        ),
        CheckConstraint("execution_allowance_minor >= 0", name="ck_credit_execution_allowance"),
        CheckConstraint("state IN ('reserved','settled')", name="ck_credit_reservation_state"),
        Index("ix_credit_reservation_owner_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    idempotency_key = Column(String(160), nullable=False)
    operation = Column(String(64), nullable=False)
    source_id = Column(String(64), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    quote_snapshot = Column(JSON, nullable=False)
    execution_allowance_minor = Column(Integer, nullable=False)
    reserved_credits = Column(Integer, nullable=False)
    committed_credits = Column(Integer, nullable=False, default=0)
    released_credits = Column(Integer, nullable=False, default=0)
    state = Column(String(16), nullable=False, default="reserved")
    created_at = Column(DateTime(timezone=True), nullable=False)
    settled_at = Column(DateTime(timezone=True), nullable=True)


class CreditAllocation(Base):
    __tablename__ = "credit_allocations"
    __table_args__ = (
        UniqueConstraint("reservation_id", "ordinal", name="uq_credit_allocation_ordinal"),
        UniqueConstraint("reservation_id", "grant_id", name="uq_credit_allocation_grant"),
        CheckConstraint("quantity > 0", name="ck_credit_allocation_quantity"),
        CheckConstraint(
            "committed_credits >= 0 AND released_credits >= 0 AND committed_credits + released_credits <= quantity",
            name="ck_credit_allocation_amounts",
        ),
    )
    id = Column(String(64), primary_key=True)
    reservation_id = Column(
        String(64), ForeignKey("credit_reservations.id"), nullable=False, index=True
    )
    grant_id = Column(String(64), ForeignKey("credit_grants.id"), nullable=False, index=True)
    ordinal = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    committed_credits = Column(Integer, nullable=False, default=0)
    released_credits = Column(Integer, nullable=False, default=0)


class CreditEvent(Base):
    __tablename__ = "credit_events"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_credit_event_key"),
        Index("ix_credit_event_owner_created", "user_id", "created_at"),
        Index("ix_credit_event_origin", "user_id", "origin_type", "origin_id"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    idempotency_key = Column(String(200), nullable=False)
    event_type = Column(String(24), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    source_type = Column(String(40), nullable=False)
    source_id = Column(String(160), nullable=False)
    origin_type = Column(String(40), nullable=True)
    origin_id = Column(String(160), nullable=True)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)


class CreditServiceQuote(Base):
    """Short-lived owner/input/qualified-route binding; never contains resume text."""

    __tablename__ = "credit_service_quotes"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_credit_service_quote_key"),
        UniqueConstraint("accepted_reservation_id", name="uq_credit_service_quote_reservation"),
        CheckConstraint("expires_at > created_at", name="ck_credit_service_quote_interval"),
        Index("ix_credit_service_quote_owner_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    operation = Column(String(64), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    contract_fingerprint = Column(String(64), nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_reservation_id = Column(
        String(64), ForeignKey("credit_reservations.id"), nullable=True
    )
    accepted_at = Column(DateTime(timezone=True), nullable=True)


class CreditExecutionHold(Base):
    """Detached whole-operation liability, retained independently of customer use."""

    __tablename__ = "credit_execution_holds"
    __table_args__ = (
        UniqueConstraint("reservation_id", name="uq_credit_execution_reservation"),
        UniqueConstraint("financial_group_id", name="uq_credit_execution_group"),
        CheckConstraint(
            "reserved_minor >= 0 AND native_reserved_minor >= 0 AND native_reserved_minor <= reserved_minor",
            name="ck_credit_execution_reserved",
        ),
        CheckConstraint(
            "settled_minor IS NULL OR settled_minor >= 0", name="ck_credit_execution_settled"
        ),
        CheckConstraint(
            "native_settled_minor IS NULL OR native_settled_minor >= 0",
            name="ck_credit_execution_native_settled",
        ),
        Index("ix_credit_execution_state_created", "state", "created_at"),
        Index("ix_credit_execution_owner_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reservation_id = Column(String(64), ForeignKey("credit_reservations.id"), nullable=False)
    financial_group_id = Column(String(64), nullable=False)
    route_id = Column(String(120), nullable=False)
    funding_kind = Column(String(24), nullable=False)
    reserved_minor = Column(BigInteger, nullable=False)
    native_reserved_minor = Column(BigInteger, nullable=False)
    settled_minor = Column(BigInteger, nullable=True)
    native_settled_minor = Column(BigInteger, nullable=True)
    state = Column(String(24), nullable=False, default="reserved")
    delivery_state = Column(String(24), nullable=False, default="pending")
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    settled_at = Column(DateTime(timezone=True), nullable=True)


class CreditExecutionAttempt(Base):
    __tablename__ = "credit_execution_attempts"
    __table_args__ = (
        UniqueConstraint("hold_id", "ordinal", name="uq_credit_execution_attempt"),
        UniqueConstraint("liability_id", name="uq_credit_execution_liability"),
        CheckConstraint("ordinal > 0", name="ck_credit_execution_attempt_ordinal"),
    )
    id = Column(String(64), primary_key=True)
    hold_id = Column(
        String(64), ForeignKey("credit_execution_holds.id"), nullable=False, index=True
    )
    liability_id = Column(String(64), ForeignKey("model_cost_liabilities.id"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    state = Column(String(24), nullable=False, default="planned")
    claim_key = Column(String(64), nullable=True)
    provider_request_id_hash = Column(String(64), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
