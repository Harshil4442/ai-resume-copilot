"""Versioned genuine password enrollment; dependencies are unavailable by default."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import jwt
from pydantic import ConfigDict, EmailStr, Field, SecretStr, field_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..domains.candidate_accounts.access import context_from_claims
from ..domains.candidate_accounts.service import production_candidate_accounts
from ..domains.recovery.pairing_contracts import PairingContract
from ..domains.recovery.store import GuardDenied, GuardUnavailable
from ..models import User
from ..rate_limiter import limiter
from ..schemas import AuthTokenResponse
from ..security import (
    JWT_ALGORITHM,
    JWT_SECRET,
    create_access_token,
    get_current_user,
    oauth2_scheme,
)
from .sensitive_auth import SensitiveAuthRoute

router = APIRouter(prefix="/auth/candidate/v1", tags=["candidate-password-lifecycle"], route_class=SensitiveAuthRoute)


class RegisterCandidate(PairingContract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    email: EmailStr
    password: SecretStr = Field(min_length=10, max_length=128, repr=False)
    accepted_terms: Literal[True]
    confirmed_age_18: Literal[True]

    @field_validator("accepted_terms", "confirmed_age_18", mode="before")
    @classmethod
    def exact_consent(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("Explicit true registration consent is required")
        return value


class LoginCandidate(PairingContract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128, repr=False)


class ChangeCandidatePassword(PairingContract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    current_password: SecretStr = Field(min_length=1, max_length=128, repr=False)
    new_password: SecretStr = Field(min_length=10, max_length=128, repr=False)


def _failure(error: Exception) -> HTTPException:
    if isinstance(error, GuardDenied):
        return HTTPException(status_code=401, detail="Candidate password authentication failed")
    return HTTPException(status_code=503, detail="Candidate account lifetime is unavailable")


@router.post("/register")
@limiter.limit("5/minute")
def register(request: Request, payload: RegisterCandidate):
    try:
        candidate = production_candidate_accounts().register(email=str(payload.email),
            password=payload.password.get_secret_value(), policy_version="2026-10-08")
    except (GuardDenied, GuardUnavailable) as error:
        raise _failure(error) from None
    return {"user_id": candidate, "status": "ENROLLED"}


@router.post("/login", response_model=AuthTokenResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginCandidate):
    try:
        result = production_candidate_accounts().login(email=str(payload.email), password=payload.password.get_secret_value())
    except (GuardDenied, GuardUnavailable) as error:
        raise _failure(error) from None
    token = create_access_token(subject=str(result.context.candidate_id), expires_delta=timedelta(days=1),
        candidate_lifetime={"version": 1, "context": result.context.model_dump(mode="json"),
                            "auth_generation": result.auth_generation})
    return AuthTokenResponse(access_token=token, user_id=result.context.candidate_id,
                             browser_pairing_session=result.context.model_dump(mode="json"))


@router.post("/logout")
def logout(current_user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
           db: Session = Depends(get_db)):
    candidate_id = int(current_user.id)
    db.rollback()
    try:
        context, _ = context_from_claims(jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM]), candidate_id=candidate_id)
        production_candidate_accounts().logout(context)
    except (GuardDenied, GuardUnavailable) as error:
        raise _failure(error) from None
    return {"status": "REVOKED"}


@router.post("/password")
def change_password(payload: ChangeCandidatePassword, current_user: User = Depends(get_current_user),
                    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    candidate_id = int(current_user.id)
    db.rollback()
    try:
        context, _ = context_from_claims(jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM]), candidate_id=candidate_id)
        production_candidate_accounts().change_password(context,
            current_password=payload.current_password.get_secret_value(),
            new_password=payload.new_password.get_secret_value())
    except (GuardDenied, GuardUnavailable) as error:
        raise _failure(error) from None
    return {"status": "CHANGED_REAUTHENTICATION_REQUIRED"}
