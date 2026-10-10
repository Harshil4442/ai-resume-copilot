import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .catalog_timing import catalog_span, current_catalog_timing
from .database import get_db
from .models import User
from .services.legacy_passwords import bounded_password_bytes, verify_legacy_bcrypt

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt_sha256", "bcrypt"],
    deprecated="auto",
)


def hash_password(password: str) -> str:
    # No try/except shim: a hashing failure is a server bug (500), not a 400.
    return pwd_context.hash(str(password), scheme="pbkdf2_sha256")


def verify_password(plain_password: str, password_hash: str) -> bool:
    # An empty/missing hash must never match. Return False instead of letting
    # passlib raise UnknownHashError (which would bubble up as a 500).
    secret = bounded_password_bytes(plain_password, password_hash)
    if secret is None:
        return False
    try:
        if password_hash.startswith(("$2", "$bcrypt-sha256$")):
            return verify_legacy_bcrypt(secret, password_hash)
        return pwd_context.verify(plain_password, password_hash)
    except Exception:
        return False


def _load_jwt_secret() -> str:
    """
    Fail fast if JWT_SECRET is missing or weak.
    Allows a permissive value only when explicitly running under pytest, so
    the existing test suite keeps working without forcing every test to set
    an env var.
    """
    is_test = bool(os.getenv("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules
    if is_test:
        return (os.getenv("JWT_SECRET") or "test-only-secret-not-for-production").strip()

    secret = (os.getenv("JWT_SECRET") or "").strip()
    app_env = (os.getenv("APP_ENV") or "production").strip().lower()
    if not secret:
        if app_env in {"development", "dev", "local"}:
            return "hirewiz-local-development-secret-change-me"
        raise RuntimeError("JWT_SECRET must be configured in non-development environments.")
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters.")
    return secret


JWT_SECRET = _load_jwt_secret()
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
if JWT_ALGORITHM not in {"HS256", "HS384", "HS512"}:
    raise RuntimeError("JWT_ALGORITHM must be HS256, HS384, or HS512.")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def create_access_token(*, subject: str, expires_delta: Optional[timedelta] = None,
                        candidate_lifetime: dict | None = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode: dict = {"sub": subject, "exp": expire}
    if candidate_lifetime is not None:
        from .domains.candidate_accounts.access import context_from_claims
        context_from_claims({"candidate_lifetime": candidate_lifetime}, candidate_id=int(subject))
        to_encode["candidate_lifetime"] = candidate_lifetime
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token_user_id(token: str) -> int:
    """Validate the bearer independently of middleware; existence needs a DB read."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    with catalog_span("dependency_jwt"):
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            sub = payload.get("sub")
            if sub is None:
                raise credentials_exception
            user_id = int(sub)
        except (JWTError, ValueError, TypeError):
            raise credentials_exception from None
    return user_id


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    user_id = decode_access_token_user_id(token)

    if current_catalog_timing() is not None:
        with catalog_span("db_acquire"):
            db.connection()
    with catalog_span("auth_lookup"):
        user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    from .domains.candidate_accounts.access import context_from_claims
    from .domains.candidate_accounts.service import mapped_account, production_candidate_accounts
    from .domains.recovery.store import GuardDenied, GuardUnavailable

    account = mapped_account(db, user_id)
    if account is not None:
        if account.state != "ACTIVE":
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            context, generation = context_from_claims(payload, candidate_id=user_id)
            db.rollback()  # Independent current authority IO does not hold a SQL transaction.
            production_candidate_accounts().validate(context, auth_generation=generation)
        except GuardDenied:
            raise HTTPException(status_code=401, detail="Not authenticated") from None
        except GuardUnavailable:
            raise HTTPException(status_code=503, detail="Candidate account lifetime is unavailable") from None
    return user
