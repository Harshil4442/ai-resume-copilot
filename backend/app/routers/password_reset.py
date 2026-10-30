"""Public website reset operations behind the existing private BFF ingress."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import EmailStr, Field, SecretStr
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..domains.candidate_accounts import email_reset
from ..domains.notifications.password_reset import configured, deliver
from ..domains.recovery.pairing_contracts import PairingContract
from ..models import NotificationOutbox
from ..rate_limiter import limiter
from .sensitive_auth import SensitiveAuthRoute


class ResetRoute(SensitiveAuthRoute):
    bound_auth_body = True
    private_candidate_ingress = True


router = APIRouter(prefix="/auth/password-reset", tags=["password-reset"], route_class=ResetRoute)


class ResetRequest(PairingContract):
    email: EmailStr


class ResetConfirm(PairingContract):
    token: SecretStr = Field(min_length=43, max_length=43, repr=False)
    new_password: SecretStr = Field(min_length=10, max_length=128, repr=False)


class ResetRequested(PairingContract):
    status: Literal["RESET_LINK_REQUESTED"]


class ResetCompleted(PairingContract):
    status: Literal["PASSWORD_RESET"]


class ResetInvalid(PairingContract):
    status: Literal["RESET_LINK_INVALID"]


class ResetFailed(PairingContract):
    status: Literal["RESET_UNAVAILABLE"]


def _response(status: int, code: str) -> JSONResponse:
    return JSONResponse({"status": code}, status_code=status,
        headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"})


def _request_delivery(email: str) -> None:
    try:
        with SessionLocal() as db:
            item = email_reset.request_reset(db, email)
            if item is not None:
                deliver(db, item)
    except Exception:  # noqa: BLE001 -- background error text may contain secrets.
        logging.getLogger(__name__).error("Password reset email request unavailable")


def _changed_delivery(token: str) -> None:
    try:
        with SessionLocal() as db:
            from ..models import PasswordResetToken
            row = db.query(PasswordResetToken).filter_by(token_sha256=email_reset.digest(token), purpose="password_reset", status="COMPLETED").one_or_none()
            if row is not None:
                item = db.query(NotificationOutbox.id).filter_by(idempotency_key="password-changed:" + row.id).scalar()
                if item is not None:
                    db.rollback()
                    deliver(db, item)
    except Exception:  # noqa: BLE001 -- fixed security-delivery telemetry only.
        logging.getLogger(__name__).error("Password changed email unavailable")


@router.post("/request", status_code=202, response_model=ResetRequested, responses={503: {"model": ResetFailed}})
@limiter.limit("3/minute")
def request_link(request: Request, payload: ResetRequest, background_tasks: BackgroundTasks):
    if not configured():
        return _response(503, "RESET_UNAVAILABLE")
    background_tasks.add_task(_request_delivery, str(payload.email))
    return _response(202, "RESET_LINK_REQUESTED")


@router.post("/confirm", response_model=ResetCompleted, responses={400: {"model": ResetInvalid}, 503: {"model": ResetFailed}})
@limiter.limit("5/minute")
def confirm(request: Request, payload: ResetConfirm, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        email_reset.confirm_reset(db, payload.token.get_secret_value(), payload.new_password.get_secret_value())
    except email_reset.ResetLinkInvalid:
        return _response(400, "RESET_LINK_INVALID")
    except email_reset.ResetUnavailable:
        return _response(503, "RESET_UNAVAILABLE")
    background_tasks.add_task(_changed_delivery, payload.token.get_secret_value())
    return _response(200, "PASSWORD_RESET")
