from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, UserProfile
from ..schemas import (
    AuthLoginRequest,
    AuthGoogleLoginRequest,
    AuthRegisterRequest,
    AuthTokenResponse,
    UserMeResponse,
    UserProfileResponse,
    UserProfileUpdate,
)
from ..security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

PROFILE_FIELDS = [
    ("full_name", "Full name"),
    ("headline", "Professional headline"),
    ("phone", "Phone"),
    ("location", "Location"),
    ("linkedin", "LinkedIn"),
    ("github", "GitHub"),
    ("portfolio", "Portfolio"),
    ("target_role", "Target role"),
    ("preferred_job_type", "Preferred job type"),
    ("preferred_location", "Preferred location"),
    ("years_experience", "Years of experience"),
    ("bio", "Short bio"),
    ("skills", "Skills"),
    ("education", "Education"),
    ("certifications", "Certifications"),
]

def _get_or_create_profile(db: Session, user: User) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    if profile:
        return profile
    profile = UserProfile(user_id=user.id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile

def _is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return len([v for v in value if str(v).strip()]) > 0
    if isinstance(value, (int, float)):
        return value > 0
    return bool(str(value).strip())

def _profile_response(profile: UserProfile, email: str) -> UserProfileResponse:
    missing = []
    filled = 0
    for field, label in PROFILE_FIELDS:
        if _is_filled(getattr(profile, field, None)):
            filled += 1
        else:
            missing.append(label)
    completeness = round(filled / len(PROFILE_FIELDS) * 100)
    return UserProfileResponse(
        email=email,
        profile_completeness=completeness,
        missing_fields=missing,
        full_name=profile.full_name or None,
        headline=profile.headline or None,
        phone=profile.phone or None,
        location=profile.location or None,
        linkedin=profile.linkedin or None,
        github=profile.github or None,
        portfolio=profile.portfolio or None,
        target_role=profile.target_role or None,
        preferred_job_type=profile.preferred_job_type or None,
        preferred_location=profile.preferred_location or None,
        years_experience=float(profile.years_experience or 0.0),
        bio=profile.bio or None,
        skills=profile.skills or [],
        education=profile.education or None,
        certifications=profile.certifications or None,
    )

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
    db.add(UserProfile(user_id=u.id))
    db.commit()
    return UserMeResponse(id=u.id, email=u.email)

@router.post("/login", response_model=AuthTokenResponse)
def login(payload: AuthLoginRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    u = db.query(User).filter(User.email == email).first()
    if not u or not verify_password(payload.password, u.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(subject=str(u.id))
    return AuthTokenResponse(access_token=token, token_type="bearer")

@router.post("/google-login", response_model=AuthTokenResponse)
def google_login(payload: AuthGoogleLoginRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    # Find existing user
    u = db.query(User).filter(User.email == email).first()
    
    if not u:
        # Register new user seamlessly with a placeholder hash (they can only login via Google, or reset password later)
        # Grant 5 initial credits as a standard signup bonus
        u = User(
            email=email,
            password_hash=hash_password(payload.email + "_google_placeholder"),
            ai_credits=5
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        
        # Create standard profile
        profile = UserProfile(user_id=u.id, full_name=payload.name)
        db.add(profile)
        db.commit()

    token = create_access_token(subject=str(u.id))
    return AuthTokenResponse(access_token=token, token_type="bearer")

@router.get("/me", response_model=UserMeResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserMeResponse(id=current_user.id, email=current_user.email)

@router.get("/profile", response_model=UserProfileResponse)
def get_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = _get_or_create_profile(db, current_user)
    return _profile_response(profile, current_user.email)

@router.put("/profile", response_model=UserProfileResponse)
def update_profile(
    payload: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = _get_or_create_profile(db, current_user)
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    for key, value in data.items():
        if hasattr(profile, key):
            if isinstance(value, str):
                value = value.strip()
            if key == "skills" and value is None:
                value = []
            setattr(profile, key, value)

    db.add(profile)
    db.commit()
    db.refresh(profile)
    return _profile_response(profile, current_user.email)
