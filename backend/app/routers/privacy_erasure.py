"""Fresh privacy confirmation for proved DELETE_DENIED owners; no normal login."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import EmailStr, Field, SecretStr, field_validator
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..domains.candidate_accounts import email_erasure
from ..domains.notifications.privacy_erasure import configured, deliver
from ..domains.recovery.pairing_contracts import PairingContract
from ..rate_limiter import limiter
from .sensitive_auth import SensitiveAuthRoute


class ErasureRoute(SensitiveAuthRoute):
    bound_auth_body = True
    private_candidate_ingress = True


router = APIRouter(prefix="/auth/privacy-erasure", tags=["privacy-erasure"], route_class=ErasureRoute)


class ErasureRequest(PairingContract):
    email: EmailStr


class ErasureConfirm(PairingContract):
    token: SecretStr = Field(min_length=43, max_length=43, repr=False)
    confirm_erasure: Literal[True]

    @field_validator("confirm_erasure", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Explicit erasure confirmation required")
        return value


class ErasureRequested(PairingContract):
    status: Literal["ERASURE_LINK_REQUESTED"]


class ErasureCompleted(PairingContract):
    status: Literal["ACCOUNT_ERASED"]


def response(status, code):
    return JSONResponse({"status": code}, status_code=status,
        headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"})


def _delivery(email):
    try:
        with SessionLocal() as db:
            identifier = email_erasure.request_erasure(db, email)
            if identifier is not None:
                deliver(db, identifier)
    except Exception:
        logging.getLogger(__name__).error("Privacy erasure email request unavailable")


@router.post("/request", status_code=202, response_model=ErasureRequested)
@limiter.limit("3/minute")
def request_link(request: Request, payload: ErasureRequest, background_tasks: BackgroundTasks):
    if not configured():
        return response(503, "ERASURE_UNAVAILABLE")
    background_tasks.add_task(_delivery, str(payload.email))
    return response(202, "ERASURE_LINK_REQUESTED")


@router.post("/confirm", response_model=ErasureCompleted)
@limiter.limit("3/minute")
def confirm(request: Request, payload: ErasureConfirm, db: Session = Depends(get_db)):
    try:
        email_erasure.confirm_erasure(db, payload.token.get_secret_value(), payload.confirm_erasure)
    except email_erasure.ErasureLinkInvalid:
        return response(400, "ERASURE_LINK_INVALID")
    except Exception:
        db.rollback()
        return response(503, "ERASURE_UNAVAILABLE")
    return response(200, "ACCOUNT_ERASED")
