"""Analysis admission aliases keep all coalesced client keys durable."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from ...database import Base
from ..common import utcnow


class AnalysisRequestKey(Base):
    __tablename__ = "analysis_request_keys"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    idempotency_key = Column(String(160), primary_key=True)
    analysis_run_id = Column(String(64), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    input_fingerprint = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
