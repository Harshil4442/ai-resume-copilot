"""Owned profile read projections, without creating an empty profile record."""
from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Load, Session

from ...models import User, UserProfile
from ...schemas import UserProfileResponse
from ..recovery.store import GuardDenied

PROFILE_FIELDS = (
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
)


def _is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return len([item for item in value if str(item).strip()]) > 0
    if isinstance(value, (int, float)):
        return value > 0
    return bool(str(value).strip())


def profile_response(profile: UserProfile | None, user: User) -> UserProfileResponse:
    """Project loaded values only; absent profile fields have the existing defaults.

    This function neither creates an ORM profile nor reads its ``user`` relationship.
    Its caller supplies the real, independently authenticated owner. No persisted
    profile identity is exposed or synthesized by the response contract.
    """
    if type(user) is not User or profile is not None and type(profile) is not UserProfile:
        raise GuardDenied("Not authenticated")
    # Inspect loaded state before any attribute access: even a nominally pure
    # getattr on an expired ORM field could otherwise issue another SQL read.
    required_user = {"id", "email", "tier", "ai_credits", "premium_until"}
    if not required_user <= inspect(user).dict.keys():
        raise GuardDenied("Not authenticated")
    if profile is not None and not {"user_id", *(field for field, _ in PROFILE_FIELDS)} <= inspect(profile).dict.keys():
        raise GuardDenied("Not authenticated")
    if (type(user.id) is not int or user.id <= 0
            or profile is not None and profile.user_id != user.id):
        raise GuardDenied("Not authenticated")
    missing = [label for field, label in PROFILE_FIELDS
        if not _is_filled(getattr(profile, field, None))]
    values = {field: getattr(profile, field, None) or None for field, _ in PROFILE_FIELDS}
    values["years_experience"] = float(getattr(profile, "years_experience", None) or 0.0)
    values["skills"] = getattr(profile, "skills", None) or []
    return UserProfileResponse(
        **values,
        email=user.email,
        profile_completeness=round((len(PROFILE_FIELDS) - len(missing)) / len(PROFILE_FIELDS) * 100),
        missing_fields=missing,
        tier="premium" if user.is_premium_active() else "free",
        ai_credits=user.ai_credits,
        premium_until=user.premium_until,
    )


def profile_view(db: Session, user: User) -> UserProfileResponse:
    """Read actual owner existence, entitlements and optional profile in one snapshot.

    The authenticated ORM identity supplies only the lookup key. A fresh joined
    query cannot export an erased owner's cached profile or stale entitlement
    values. Pending caller changes are never implicitly flushed by this read.
    """
    state = inspect(user, raiseerr=False)
    identity = state.identity if state is not None else None
    if (type(user) is not User or type(identity) is not tuple or len(identity) != 1
            or type(identity[0]) is not int or identity[0] <= 0):
        raise GuardDenied("Not authenticated")
    with db.no_autoflush:
        row = (db.query(User, UserProfile)
            .outerjoin(UserProfile, UserProfile.user_id == User.id)
            .options(Load(User).load_only(User.id, User.email, User.tier, User.ai_credits, User.premium_until))
            .filter(User.id == identity[0])
            .populate_existing()
            .one_or_none())
        if row is None:
            raise GuardDenied("Not authenticated")
        owner, profile = row
        return profile_response(profile, owner)
