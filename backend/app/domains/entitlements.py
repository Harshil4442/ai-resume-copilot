"""Refresh authoritative balances and access after acquiring the owner lock."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import User


def lock_entitlement_owner(db: Session, user_id: int) -> User:
    with db.no_autoflush:
        user = db.query(User).filter(User.id == user_id).with_for_update().one()
        # FOR UPDATE does not replace a cached auth identity. Preserve earlier
        # changes made under this transaction's owner lock (e.g. batch debits),
        # without autoflushing unrelated application/run state before its own locks.
        db.flush([user])
        db.refresh(user, attribute_names=["ai_credits", "job_service_credits", "tier", "premium_until"])
    return user
