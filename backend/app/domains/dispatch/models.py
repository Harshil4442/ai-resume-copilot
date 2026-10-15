from sqlalchemy import JSON, Column, DateTime, Index, Integer, String, Text

from ...database import Base
from ..common import utcnow


class DispatchOutbox(Base):
    __tablename__ = "dispatch_outbox"
    __table_args__ = (Index("ix_dispatch_due", "status", "available_at"),)

    id = Column(String(64), primary_key=True)
    topic = Column(String(64), nullable=False)
    aggregate_id = Column(String(64), nullable=False, index=True)
    idempotency_key = Column(String(180), nullable=False, unique=True)
    # Keep candidate files, answers, credentials and resume text out of tasks.
    payload = Column(JSON, nullable=False, default=dict)
    status = Column(String(24), nullable=False, default="pending")
    available_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    dispatch_attempts = Column(Integer, nullable=False, default=0)
    execution_attempts = Column(Integer, nullable=False, default=0)
    task_name = Column(String(512), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
