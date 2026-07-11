from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session
from ..models import User

def verify_and_deduct_credit(user_id: int, db: Session, amount: int = 1) -> bool:
    """
    Checks whether a user has enough analysis units or active Premium access.
    Deducts `amount` atomically for a free account.
    """
    if amount <= 0:
        raise ValueError("analysis-unit deduction must be positive")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
        
    if user.is_premium_active():
        # Active Premium has no analysis-unit deductions.
        return True

    # Premium grant has lapsed — fall back to free access.
    if user.tier == "premium" and not user.is_premium_active():
        db.execute(
            update(User).where(User.id == user_id).values(tier="free", premium_until=None)
        )
        user.tier = "free"

    # Keep the balance predicate inside the UPDATE so concurrent operations
    # cannot both pass a stale read and drive the balance below zero.
    result = db.execute(
        update(User)
        .where(User.id == user_id, User.ai_credits >= amount)
        .values(ai_credits=User.ai_credits - amount)
    )
    if result.rowcount != 1:
        db.rollback()
        balance = db.query(User.ai_credits).filter(User.id == user_id).scalar()
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Operation requires {amount} analysis unit(s). "
                f"Your balance is {int(balance or 0)}. Premium access has no unit deductions."
            ),
        )
    db.commit()
    return True
