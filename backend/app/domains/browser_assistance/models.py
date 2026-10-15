"""Durable device, review and field outcomes; no application-credit settlement."""
from sqlalchemy import (
    JSON,
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


class BrowserAssistanceDevice(Base):
    __tablename__ = "browser_assistance_devices"
    __table_args__ = (
        CheckConstraint("state IN ('pending','active','revoked')", name="ck_browser_device_state"),
        Index("ix_browser_device_owner_state", "user_id", "state"),
        Index("ix_browser_device_key_created", "key_sha256", "created_at"),
    )
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    extension_id = Column(String(32), nullable=False)
    public_key = Column(JSON, nullable=False)
    key_sha256 = Column(String(64), nullable=False, unique=True)
    state = Column(String(16), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    pairing_expires_at = Column(DateTime(timezone=True), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class BrowserAssistanceReview(Base):
    __tablename__ = "browser_assistance_reviews"
    __table_args__ = (
        CheckConstraint("state IN ('draft','approved','running','filled','unknown','cancelled','expired')", name="ck_browser_review_state"),
        CheckConstraint("next_index BETWEEN 0 AND 12", name="ck_browser_review_index"),
        CheckConstraint("review_protocol_version BETWEEN 1 AND 3", name="ck_browser_review_protocol"),
        Index("ix_browser_review_owner_application", "user_id", "application_id", "created_at"),
        Index("ix_browser_review_expiry", "expires_at"),
    )
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_id = Column(String(36), ForeignKey("browser_assistance_devices.id", ondelete="CASCADE"), nullable=False)
    application_id = Column(String(64), ForeignKey("employer_applications.id", ondelete="CASCADE"), nullable=False)
    package_digest = Column(String(64), nullable=False)
    artifact_sha256 = Column(String(64), nullable=False)
    context_digest = Column(String(64), nullable=False)
    review_digest = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    state = Column(String(16), nullable=False, default="draft")
    next_index = Column(Integer, nullable=False, default=0)
    review_protocol_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)


class BrowserAssistanceRequest(Base):
    __tablename__ = "browser_assistance_requests"
    __table_args__ = (
        UniqueConstraint("device_id", "request_id", name="uq_browser_device_request"),
        UniqueConstraint("review_id", "field_index", name="uq_browser_review_field"),
        CheckConstraint("state IN ('completed','begun','filled','blocked','unknown')", name="ck_browser_request_state"),
        Index("ix_browser_request_created", "created_at"),
    )
    id = Column(String(36), primary_key=True)
    device_id = Column(String(36), ForeignKey("browser_assistance_devices.id", ondelete="CASCADE"), nullable=False)
    request_id = Column(String(36), nullable=False)
    operation = Column(String(24), nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    review_id = Column(String(36), ForeignKey("browser_assistance_reviews.id", ondelete="CASCADE"), nullable=True)
    field_index = Column(Integer, nullable=True)
    state = Column(String(16), nullable=False, default="completed")
    response = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
