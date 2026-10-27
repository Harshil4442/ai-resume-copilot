from __future__ import annotations

from sqlalchemy import (
    BigInteger,
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


class FreeCoreReservation(Base):
    """Detached infrastructure liability; refund/deletion never removes expense."""
    __tablename__ = "free_core_reservations"
    __table_args__ = (
        CheckConstraint("reserved_minor > 0", name="ck_free_core_reserved"),
        CheckConstraint("settled_minor IS NULL OR settled_minor >= 0", name="ck_free_core_settled"),
        CheckConstraint("state IN ('reserved','settled')", name="ck_free_core_state"),
        Index("ix_free_core_owner_created", "user_id", "created_at"),
        Index("ix_free_core_state_created", "state", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    operation = Column(String(64), nullable=False)
    route_id = Column(String(120), nullable=False)
    policy_version = Column(String(120), nullable=False)
    funding_period_id = Column(String(120), nullable=False)
    reserved_minor = Column(BigInteger, nullable=False)
    settled_minor = Column(BigInteger, nullable=True)
    state = Column(String(16), nullable=False, default="reserved")
    created_at = Column(DateTime(timezone=True), nullable=False)
    settled_at = Column(DateTime(timezone=True), nullable=True)
    settlement_receipt_sha256 = Column(String(64), nullable=True)


class FreeCoreRequest(Base):
    """Private idempotency/result binding; removed with its account."""
    __tablename__ = "free_core_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_free_core_request_key"),
        UniqueConstraint("user_id", "source_type", "source_id", name="uq_free_core_request_source"),
        UniqueConstraint("reservation_id", name="uq_free_core_request_reservation"),
        CheckConstraint("state IN ('admitted','completed')", name="ck_free_core_request_state"),
        Index("ix_free_core_request_fingerprint", "user_id", "input_fingerprint"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_type = Column(String(40), nullable=False)
    source_id = Column(String(64), nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    reservation_id = Column(String(64), ForeignKey("free_core_reservations.id"), nullable=False)
    result_id = Column(String(64), nullable=True)
    state = Column(String(16), nullable=False, default="admitted")
