import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import EntitlementLedger, User, UserProfile, Resume, JobMatch, PaymentOrder
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
from ..rate_limiter import limiter

router = APIRouter(prefix="/auth", tags=["auth"])
CURRENT_POLICY_VERSION = "2026-07-11"

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
    # Report the *effective* tier: an expired premium grant reads as free.
    effective_tier = "premium" if profile.user.is_premium_active() else "free"
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
        tier=effective_tier,
        ai_credits=profile.user.ai_credits,
        premium_until=profile.user.premium_until,
    )

@router.post("/register", response_model=UserMeResponse)
@limiter.limit("5/minute")
def register(request: Request, payload: AuthRegisterRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    exists = db.query(User).filter(User.email == email).first()
    if exists:
        raise HTTPException(status_code=409, detail="Email already registered")

    accepted_at = datetime.now(timezone.utc)
    u = User(
        email=email,
        password_hash=hash_password(payload.password),
        terms_accepted_at=accepted_at,
        terms_version=CURRENT_POLICY_VERSION,
        privacy_version=CURRENT_POLICY_VERSION,
        age_confirmed_at=accepted_at,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    db.add(UserProfile(user_id=u.id))
    db.commit()
    return UserMeResponse(id=u.id, email=u.email, tier=u.tier, ai_credits=u.ai_credits)

@router.post("/login", response_model=AuthTokenResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: AuthLoginRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    u = db.query(User).filter(User.email == email).first()
    if not u or not verify_password(payload.password, u.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(subject=str(u.id))
    return AuthTokenResponse(access_token=token, token_type="bearer")

@router.post("/google-login", response_model=AuthTokenResponse)
@limiter.limit("10/minute")
def google_login(request: Request, payload: AuthGoogleLoginRequest, db: Session = Depends(get_db)):
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")

    try:
        claims = google_id_token.verify_oauth2_token(
            payload.id_token,
            google_requests.Request(),
            client_id,
        )
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid Google identity token")

    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(status_code=401, detail="Invalid Google identity token")
    if claims.get("email_verified") is not True:
        raise HTTPException(status_code=401, detail="Google email is not verified")
    email_claim = claims.get("email")
    subject = claims.get("sub")
    if not isinstance(email_claim, str) or not email_claim.strip() or not isinstance(subject, str):
        raise HTTPException(status_code=401, detail="Invalid Google identity token")

    email = email_claim.strip().lower()
    name = claims.get("name") if isinstance(claims.get("name"), str) else ""

    # Find existing user
    u = db.query(User).filter(User.email == email).first()
    
    if not u:
        if not payload.registration_consent or payload.policy_version != CURRENT_POLICY_VERSION:
            raise HTTPException(
                status_code=403,
                detail="Account registration consent is required before Google sign-in.",
            )
        # Google-only users have no usable local password. Their identity is
        # re-verified by Google on every new OAuth sign-in.
        accepted_at = datetime.now(timezone.utc)
        u = User(
            email=email,
            password_hash="",
            ai_credits=20,
            terms_accepted_at=accepted_at,
            terms_version=CURRENT_POLICY_VERSION,
            privacy_version=CURRENT_POLICY_VERSION,
            age_confirmed_at=accepted_at,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        
        # Create standard profile
        profile = UserProfile(user_id=u.id, full_name=name)
        db.add(profile)
        db.commit()

    token = create_access_token(subject=str(u.id))
    return AuthTokenResponse(access_token=token, token_type="bearer")

@router.get("/me", response_model=UserMeResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserMeResponse(id=current_user.id, email=current_user.email, tier=current_user.tier, ai_credits=current_user.ai_credits)

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


@router.post("/delete-account")
def delete_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Deletes account/profile/resume/job data and unlinks retained financial
    records. Payment, refund, and webhook audit rows are retained for fraud,
    reconciliation, tax, and legal obligations without a live User foreign key.
    """
    uid = current_user.id
    now = datetime.now(timezone.utc)

    # End access before unlinking the ledger. This is an audit transition, not
    # a refund and not a subscription cancellation.
    active_entitlements = (
        db.query(EntitlementLedger)
        .filter(
            EntitlementLedger.user_id == uid,
            EntitlementLedger.status == "active",
        )
        .all()
    )
    for entitlement in active_entitlements:
        entitlement.status = "ended_account_deleted"
        entitlement.revoked_at = now
        entitlement.user_id = None
    db.query(EntitlementLedger).filter(EntitlementLedger.user_id == uid).update(
        {EntitlementLedger.user_id: None}, synchronize_session=False
    )

    orders = db.query(PaymentOrder).filter(PaymentOrder.user_id == uid).all()
    for order in orders:
        if order.status in {"initializing", "created", "client_confirmed"}:
            order.status = "customer_deleted"
        order.active_attempt_key = None
        order.customer_deleted_at = now
        order.user_id = None

    db.query(JobMatch).filter(JobMatch.user_id == uid).delete(synchronize_session=False)
    db.query(Resume).filter(Resume.user_id == uid).delete(synchronize_session=False)
    db.query(UserProfile).filter(UserProfile.user_id == uid).delete(synchronize_session=False)
    db.query(User).filter(User.id == uid).delete(synchronize_session=False)
    db.commit()
    return {"status": "deleted"}
