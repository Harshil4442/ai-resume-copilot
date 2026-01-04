from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import AuthLoginRequest, AuthRegisterRequest, AuthTokenResponse, UserMeResponse
from ..security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserMeResponse)
def register(payload: AuthRegisterRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    exists = db.query(User).filter(User.email == email).first()
    if exists:
        raise HTTPException(status_code=409, detail="Email already registered")

    u = User(email=email, password_hash=hash_password(payload.password))
    db.add(u)
    db.commit()
    db.refresh(u)
    return UserMeResponse(id=u.id, email=u.email)

@router.post("/login", response_model=AuthTokenResponse)
def login(payload: AuthLoginRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    u = db.query(User).filter(User.email == email).first()
    if not u or not verify_password(payload.password, u.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(subject=str(u.id))
    return AuthTokenResponse(access_token=token, token_type="bearer")

@router.get("/me", response_model=UserMeResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserMeResponse(id=current_user.id, email=current_user.email)