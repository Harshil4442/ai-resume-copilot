"""Finite owned advisory reads; every HTTP request retains native authorization."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import inspect
from sqlalchemy.orm import Session, object_session

from ...feature_flags import decisions_for_user
from ...models import CandidatePasswordAccount, User
from ...schemas import UserProfileResponse
from ..credits.availability import availability_view
from ..credits.schemas import CreditAvailabilityView, CreditWalletView
from ..credits.service import wallet
from ..recovery.store import GuardDenied
from .profile_view import profile_view

COMPONENTS = ("profile", "features", "credit_availability", "credit_wallet")
Component = Literal["profile", "features", "credit_availability", "credit_wallet"]
MAX_COMPONENT_BYTES = {"profile": 65536, "features": 16384,
    "credit_availability": 65536, "credit_wallet": 1048576}


class FeatureView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    enabled: bool
    variant: Literal["on", "off"]
    rollout_percent: int = Field(ge=0, le=100)
    bucket: int = Field(ge=0, lt=100)
    reason: Literal["kill_switch", "internal", "invited", "percentage"]

    @model_validator(mode="after")
    def _variant(self) -> FeatureView:
        if self.variant != ("on" if self.enabled else "off"):
            raise ValueError("Feature variant differs from decision")
        return self


class OwnedBootstrapView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["owned-bootstrap-v1"] = "owned-bootstrap-v1"
    profile: UserProfileResponse | None
    features: dict[str, FeatureView] | None
    credit_availability: CreditAvailabilityView | None
    credit_wallet: CreditWalletView | None
    unavailable: list[Component]


def _subject(db: Session, user_id: int) -> tuple:
    row = (db.query(User.id, User.credential_epoch, CandidatePasswordAccount.state,
        CandidatePasswordAccount.auth_generation, CandidatePasswordAccount.account_binding_id)
        .outerjoin(CandidatePasswordAccount, CandidatePasswordAccount.user_id == User.id)
        .filter(User.id == user_id).one_or_none())
    if row is None or row[2] not in (None, "ACTIVE"):
        raise GuardDenied("Not authenticated")
    return tuple(row)


def _features(user_id: int, email: str) -> dict:
    rows = decisions_for_user(user_id=user_id, email=email)
    if type(rows) is not dict or len(rows) > 64:
        raise ValueError("Feature decisions exceed their supported bound")
    result = {}
    for key, row in rows.items():
        decision = FeatureView.model_validate(row)
        if decision.key != key:
            raise ValueError("Feature decision key differs")
        result[key] = decision.model_dump(mode="json")
    return result


def bootstrap_view(db: Session, user: User) -> OwnedBootstrapView:
    state = inspect(user, raiseerr=False)
    identity = state.identity if state is not None else None
    if (type(user) is not User or object_session(user) is not db or type(identity) is not tuple
            or len(identity) != 1 or type(identity[0]) is not int or identity[0] <= 0):
        raise GuardDenied("Not authenticated")
    user_id = identity[0]
    # No component may implicitly flush a caller draft or manufacture a wallet,
    # profile, quote or entitlement. Failure of an optional advisory component
    # does not turn another successful owned read into a fabricated zero value.
    with db.no_autoflush:
        before = _subject(db, user_id)
        values: dict = {}
        unavailable: list[Component] = []
        readers: tuple[tuple[Component, Callable], ...] = (
            ("profile", lambda: UserProfileResponse.model_validate(profile_view(db, user))),
            ("features", lambda: _features(user_id, user.email)),
            ("credit_availability", lambda: CreditAvailabilityView.model_validate(availability_view(db, user))),
            ("credit_wallet", lambda: CreditWalletView.model_validate(wallet(db, user_id=user_id, max_lots=5000), strict=True)),
        )
        for name, read in readers:
            try:
                value = read()
                raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
                if len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode()) > MAX_COMPONENT_BYTES[name]:
                    raise ValueError("Owned component exceeds its supported response bound")
                values[name] = value
            except GuardDenied:
                raise
            except Exception:
                # A failed SQL statement must not poison later advisory reads.
                # Rollback is never a commit, a grant or successful zero balance.
                db.rollback()
                values[name] = None
                unavailable.append(name)
        if _subject(db, user_id) != before:
            raise GuardDenied("Not authenticated")
        return OwnedBootstrapView(**values, unavailable=unavailable)
