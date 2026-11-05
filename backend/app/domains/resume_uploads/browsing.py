"""Shared metadata-only resume browsing; transport and native auth stay separate."""
import base64
import hashlib
import hmac
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ... import models, schemas
from ...security import JWT_SECRET

MAX_LEGACY_RESUMES = 500
MAX_RESUME_PAGE = 50
_CURSOR_PURPOSE = b"hirewiz:resume-page:v1\0"


def _resume_metadata_query(db: Session, user_id: int):
    # Never hydrate extracted text, personal JSON or the deferred original bytes
    # merely to populate a selector. source_available remains a SQL expression.
    return db.query(models.Resume.id, models.Resume.original_filename, models.Resume.created_at,
                    models.Resume.source_available, models.Resume.source_format).filter(models.Resume.user_id == user_id)


def _resume_metadata(row) -> schemas.ResumeListItem:
    return schemas.ResumeListItem(id=row.id, filename=row.original_filename or f"Resume #{row.id}",
        created_at=row.created_at, source_available=bool(row.source_available), source_format=row.source_format)


def _resume_cursor(user_id: int, row) -> str:
    raw = json.dumps([1, user_id, row.created_at.isoformat(), row.id], separators=(",", ":")).encode()
    signature = hmac.new(JWT_SECRET.encode(), _CURSOR_PURPOSE + raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")


def _read_resume_cursor(value: str, user_id: int) -> tuple[datetime, int]:
    try:
        if not value or len(value) > 256 or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in value):
            raise ValueError
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        raw, signature = decoded[:-32], decoded[-32:]
        if not hmac.compare_digest(signature, hmac.new(JWT_SECRET.encode(), _CURSOR_PURPOSE + raw, hashlib.sha256).digest()):
            raise ValueError
        data = json.loads(raw)
        if (type(data) is not list or len(data) != 4 or type(data[0]) is not int or data[0] != 1
                or type(data[1]) is not int or data[1] != user_id or type(data[2]) is not str
                or type(data[3]) is not int or not 0 < data[3] < 2**63):
            raise ValueError
        stamp = datetime.fromisoformat(data[2])
        if stamp.tzinfo is not None or stamp.isoformat() != data[2] or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
            raise ValueError
        return stamp, data[3]
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(400, detail={"code": "invalid_resume_cursor", "message": "Refresh the resume list and try again."}) from None


def page(db: Session, user_id: int, *, limit: int = 20, cursor: str | None = None, current_id: int | None = None) -> schemas.ResumePageResponse:
    if type(limit) is not int or not 1 <= limit <= MAX_RESUME_PAGE:
        raise HTTPException(422, "Resume page limit must be between 1 and 50")
    query = _resume_metadata_query(db, user_id)
    if cursor is not None:
        stamp, identity = _read_resume_cursor(cursor, user_id)
        query = query.filter(or_(models.Resume.created_at < stamp,
                                 and_(models.Resume.created_at == stamp, models.Resume.id < identity)))
    rows = query.order_by(models.Resume.created_at.desc(), models.Resume.id.desc()).limit(limit + 1).all()
    items = rows[:limit]
    selected = None
    if current_id is not None:
        selected = next((row for row in items if row.id == current_id), None)
        if selected is None:
            selected = _resume_metadata_query(db, user_id).filter(models.Resume.id == current_id).one_or_none()
        if selected is None:
            raise HTTPException(404, detail="Selected resume is unavailable")
    return schemas.ResumePageResponse(resumes=[_resume_metadata(row) for row in items],
        next_cursor=_resume_cursor(user_id, items[-1]) if len(rows) > limit else None,
        selected_resume=_resume_metadata(selected) if selected is not None else None)
