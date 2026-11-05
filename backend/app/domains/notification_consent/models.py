"""Disposable scheduling projection of native digest consent; never authority."""

from sqlalchemy import (
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


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("subject_uuid", name="uq_notification_pref_subject"),
        UniqueConstraint("account_binding_id", name="uq_notification_pref_binding"),
        UniqueConstraint("operation_id", name="uq_notification_pref_operation"),
        CheckConstraint(
            "native_generation BETWEEN 1 AND 9007199254740991",
            name="ck_notification_pref_generation",
        ),
        CheckConstraint(
            "length(subject_uuid) = 36 AND length(account_binding_id) = 36 AND length(operation_id) = 36",
            name="ck_notification_pref_uuid_length",
        ),
        CheckConstraint(
            "length(principal_sha256) = 64 AND length(native_head_sha256) = 64 AND length(recipient_sha256) = 64",
            name="ck_notification_pref_hash_length",
        ),
        CheckConstraint(
            "length(policy_version) BETWEEN 1 AND 80", name="ck_notification_pref_policy"
        ),
        CheckConstraint(
            "(enabled AND next_due_at IS NOT NULL) OR (NOT enabled AND next_due_at IS NULL)",
            name="ck_notification_pref_schedule",
        ),
        Index("ix_notification_preferences_due", "enabled", "next_due_at", "user_id"),
    )

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE", name="fk_notification_pref_owner"),
        primary_key=True,
    )
    subject_uuid = Column(String(36), nullable=False)
    account_binding_id = Column(String(36), nullable=False)
    principal_sha256 = Column(String(64), nullable=False)
    native_generation = Column(BigInteger, nullable=False)
    native_head_sha256 = Column(String(64), nullable=False)
    recipient_sha256 = Column(String(64), nullable=False)
    policy_version = Column(String(80), nullable=False)
    enabled = Column(Boolean, nullable=False)
    next_due_at = Column(DateTime(timezone=True), nullable=True)
    operation_id = Column(String(36), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
