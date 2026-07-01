from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session
from ..models import User

def verify_and_deduct_credit(user_id: int, db: Session, amount: int = 1) -> bool:
    """
    Checks if a user has sufficient credits or is on a premium plan.
    Deducts `amount` credits atomically if they are on a free tier.
    Raises a 402 Payment Required exception if they are out of credits.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
        
    if user.tier == "premium":
        # Premium subscription gets unlimited AI operations
        return True
        
    if user.ai_credits < amount:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Operation requires {amount} credit(s). Your balance is {user.ai_credits}. Please upgrade to premium or top-up your credits."
        )
        
    # Atomically decrement credits to prevent race conditions
    db.execute(
        update(User)
        .where(User.id == user_id)
        .values(ai_credits=User.ai_credits - amount)
    )
    db.commit()
    return True
