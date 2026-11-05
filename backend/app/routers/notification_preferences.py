"""Bounded, explicit weekly-mail preferences and login-free disable-only proof."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..domains.candidate_accounts.access import context_from_claims
from ..domains.notification_consent import service
from ..domains.notifications.native import require_digest_enabled
from ..domains.recovery.native_metadata_admission import MetadataAdmissionError
from ..domains.recovery.store import GuardDenied, GuardUnavailable
from ..models import User
from ..rate_limiter import limiter
from ..security import JWT_ALGORITHM, JWT_SECRET, get_current_user, oauth2_scheme
from .native_metadata_http import metadata_http_error
from .sensitive_auth import SensitiveAuthRoute


class NotificationRoute(SensitiveAuthRoute):
    bound_auth_body = True


router = APIRouter(prefix="/v1/notifications", tags=["notification-preferences"], route_class=NotificationRoute)
public_router = APIRouter(prefix="/public/notifications", tags=["notification-unsubscribe"], route_class=NotificationRoute)


class PreferenceChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    expected_generation: int = Field(ge=0, lt=2**53)
    weekly_digest: bool
    explicit_confirmation: Literal[True]

    @field_validator("explicit_confirmation", mode="before")
    @classmethod
    def exact_confirmation(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("Explicit confirmation required")
        return value


class UnsubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    token: SecretStr = Field(min_length=1, max_length=4096, repr=False)


class PreferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weekly_digest: bool
    generation: int = Field(ge=0, lt=2**53)
    policy_version: str = Field(min_length=1, max_length=80)
    next_digest_at: datetime | None
    projection_current: bool


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


def _failure(error, *, public=False):
    if isinstance(error, MetadataAdmissionError):
        return metadata_http_error(error)
    if isinstance(error, GuardDenied):
        return HTTPException(403 if public else 409,
            "This unsubscribe link is unavailable or expired." if public else "Refresh notification settings before trying again.",
            headers={"Cache-Control": "private, no-store"})
    return HTTPException(503, "Notification settings are temporarily unavailable. Check current settings before retrying a change.",
                         headers={"Cache-Control": "private, no-store"})


def _preferences_enabled() -> None:
    try:
        require_digest_enabled()
    except GuardUnavailable as error:
        raise _failure(error) from None


@router.get("/preferences", response_model=PreferenceResponse, dependencies=[Depends(_preferences_enabled)])
@limiter.limit("10/minute")
def preferences(request: Request, response: Response, current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _private(response)
    try:
        return service.read_preferences(db, int(current_user.id))
    except (GuardDenied, GuardUnavailable) as error:
        db.rollback()
        raise _failure(error) from None


@router.put("/preferences", response_model=PreferenceResponse, dependencies=[Depends(_preferences_enabled)])
@limiter.limit("5/minute")
def change(request: Request, response: Response, payload: PreferenceChange,
           current_user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
           db: Session = Depends(get_db)):
    _private(response)
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        context, _ = context_from_claims(claims, candidate_id=int(current_user.id))
        return service.change_preferences(db, context, expected_generation=payload.expected_generation,
                                          enabled=payload.weekly_digest)
    except (GuardDenied, GuardUnavailable, JWTError) as error:
        db.rollback()
        raise _failure(error) from None


@public_router.post("/unsubscribe", response_model=PreferenceResponse)
@limiter.limit("5/minute")
def unsubscribe(request: Request, response: Response, payload: UnsubscribeRequest,
                db: Session = Depends(get_db)):
    _private(response)
    try:
        return service.unsubscribe(db, payload.token.get_secret_value())
    except (GuardDenied, GuardUnavailable) as error:
        db.rollback()
        raise _failure(error, public=True) from None
