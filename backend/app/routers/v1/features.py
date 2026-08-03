from __future__ import annotations

from fastapi import APIRouter, Depends

from ... import models
from ...feature_flags import decisions_for_user
from ...security import get_current_user

router = APIRouter(prefix="/features", tags=["feature-flags"])


@router.get("")
def get_feature_decisions(
    current_user: models.User = Depends(get_current_user),
) -> dict[str, object]:
    return {
        "features": decisions_for_user(
            user_id=int(current_user.id),
            email=str(current_user.email),
        )
    }
