import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .database import get_db
from .models import User

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
    if not password_hash:
        return False
    try:
        return pwd_context.verify(str(plain_password), password_hash)
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


def create_access_token(*, subject: str, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode = {"sub": subject, "exp": expire}
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        user_id = int(sub)
    except (JWTError, ValueError):
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise credentials_exception
    return user
