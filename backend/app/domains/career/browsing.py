"""Bounded opportunity metadata browsing; cursors never authorize access.

Counts and rows are advisory reads, not a transaction-wide snapshot. Existing
fresh authentication, ownership, action admission and full exports stay separate.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, true
from sqlalchemy.orm import Session

from ...models import Opportunity
from ...security import JWT_SECRET
from . import schemas
from .service import VALID_STAGES

DEFAULT_PAGE = 20
MAX_PAGE = 50
MAX_CURSOR = 1024
_PURPOSE = b"hirewiz:opportunity-page:v1\0"
_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _normalized(stage: str | None, search: str | None):
    if stage is not None and (type(stage) is not str or len(stage) > 32):
        raise HTTPException(422, "Unsupported opportunity stage")
    if search is not None and (type(search) is not str or len(search) > 200):
        raise HTTPException(422, "Opportunity search must have at most 200 characters")
    stage = stage.strip() or None if stage is not None else None
    if stage is not None and stage not in VALID_STAGES:
        raise HTTPException(422, "Unsupported opportunity stage")
    return stage, search.strip() if search is not None else ""


def _context(
    user_id: int, stage: str | None, search: str, include_archived: bool, limit: int
) -> str:
    # Bind the complete normalized filter while keeping even a 200-character
    # Unicode search out of the token and within its fixed transport bound.
    raw = json.dumps(
        [user_id, stage, search, include_archived, limit], separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _cursor(user_id: int, context: str, stamp: datetime, identity: str) -> str:
    stamp = stamp.astimezone(UTC).replace(tzinfo=None) if stamp.tzinfo is not None else stamp
    raw = json.dumps(
        [1, user_id, context, stamp.isoformat(), identity], separators=(",", ":")
    ).encode()
    signature = hmac.new(JWT_SECRET.encode(), _PURPOSE + raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")


def _read_cursor(value: str, user_id: int, context: str) -> tuple[datetime, str]:
    try:
        if (
            type(value) is not str
            or not value
            or len(value) > MAX_CURSOR
            or any(c not in _ALPHABET for c in value)
        ):
            raise ValueError
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        raw, signature = decoded[:-32], decoded[-32:]
        expected = hmac.new(JWT_SECRET.encode(), _PURPOSE + raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(raw)
        if (
            type(data) is not list
            or len(data) != 5
            or type(data[0]) is not int
            or data[0] != 1
            or type(data[1]) is not int
            or data[1] != user_id
            or data[2] != context
            or type(data[3]) is not str
            or type(data[4]) is not str
            or not 1 <= len(data[4]) <= 64
        ):
            raise ValueError
        stamp = datetime.fromisoformat(data[3])
        if (
            stamp.tzinfo is not None
            or stamp.isoformat() != data[3]
            or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value
        ):
            raise ValueError
        return stamp, data[4]
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(
            400,
            detail={
                "code": "invalid_opportunity_cursor",
                "message": "Refresh the opportunity list and try again.",
            },
        ) from None


def page(
    db: Session,
    user_id: int,
    *,
    stage: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
    limit: int = DEFAULT_PAGE,
    cursor: str | None = None,
) -> schemas.OpportunityPageResponse:
    if type(user_id) is not int or user_id <= 0:
        raise HTTPException(401, "Not authenticated")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE or type(include_archived) is not bool:
        raise HTTPException(422, "Opportunity page limit must be between 1 and 50")
    stage, search = _normalized(stage, search)
    context = _context(user_id, stage, search, include_archived, limit)
    anchor = _read_cursor(cursor, user_id, context) if cursor is not None else None
    filters = []
    if stage is not None:
        filters.append(Opportunity.stage == stage)
    elif not include_archived:
        filters.append(Opportunity.stage != "archived")
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + escaped + "%"
        filters.append(
            or_(
                *(
                    column.ilike(pattern, escape="\\")
                    for column in (Opportunity.title, Opportunity.company, Opportunity.location)
                )
            )
        )
    filtered = and_(*filters) if filters else true()
    with db.no_autoflush:
        counts = (
            db.query(
                func.count(Opportunity.id).label("total_owned"),
                func.coalesce(func.sum(case((filtered, 1), else_=0)), 0).label("total"),
                func.coalesce(
                    func.sum(
                        case(
                            (Opportunity.stage.notin_(("rejected", "withdrawn", "archived")), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("active_count"),
                func.coalesce(
                    func.sum(case((Opportunity.stage == "interviewing", 1), else_=0)), 0
                ).label("interview_count"),
            )
            .filter(Opportunity.user_id == user_id)
            .one()
        )
        query = db.query(
            *(getattr(Opportunity, name) for name in schemas.OpportunityPageItem.model_fields)
        ).filter(Opportunity.user_id == user_id, *filters)
        if anchor is not None:
            stamp, identity = anchor
            query = query.filter(
                or_(
                    Opportunity.updated_at < stamp,
                    and_(Opportunity.updated_at == stamp, Opportunity.id < identity),
                )
            )
        rows = (
            query.order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
            .limit(limit + 1)
            .all()
        )
        items = [
            schemas.OpportunityPageItem.model_validate(dict(row._mapping)) for row in rows[:limit]
        ]
        next_cursor = (
            _cursor(user_id, context, rows[limit - 1].updated_at, rows[limit - 1].id)
            if len(rows) > limit
            else None
        )
        return schemas.OpportunityPageResponse(
            items=items, next_cursor=next_cursor, **dict(counts._mapping)
        )
