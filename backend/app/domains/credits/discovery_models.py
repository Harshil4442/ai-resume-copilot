"""Owner-bound batch delivery proofs; no fabricated per-job monetary charges."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from ...database import Base
from ..common import utcnow


class CreditDiscoveryBatch(Base):
    __tablename__ = "credit_discovery_batches"
    __table_args__ = (
        UniqueConstraint("search_id", "user_id", name="uq_credit_discovery_batch_owner"),
        UniqueConstraint("hold_id", name="uq_credit_discovery_batch_hold"),
        UniqueConstraint("quote_id", name="uq_credit_discovery_batch_quote"),
        ForeignKeyConstraint(
            ["search_id", "user_id"],
            ["employer_searches.id", "employer_searches.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64", name="ck_credit_discovery_request_hash"
        ),
        Index("ix_credit_discovery_batch_owner_created", "user_id", "created_at"),
    )
    search_id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    hold_id = Column(String(64), ForeignKey("credit_execution_holds.id"), nullable=False)
    quote_id = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class CreditDiscoveryDelivery(Base):
    __tablename__ = "credit_discovery_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["search_id", "user_id"],
            ["credit_discovery_batches.search_id", "credit_discovery_batches.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("user_id", "posting_id", name="uq_credit_discovery_posting"),
        UniqueConstraint("search_id", "ordinal", name="uq_credit_discovery_ordinal"),
        CheckConstraint(
            "ordinal BETWEEN 0 AND 4 AND length(opening_key) = 64 AND length(item_digest) = 64",
            name="ck_credit_discovery_delivery_bounds",
        ),
        CheckConstraint(
            "length(CAST(identity_proof AS TEXT)) <= 16384",
            name="ck_credit_discovery_identity_size",
        ),
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    opening_key = Column(String(64), primary_key=True)
    search_id = Column(String(64), nullable=False)
    posting_id = Column(String(64), ForeignKey("employer_postings.id"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    item_digest = Column(String(64), nullable=False)
    identity_proof = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
