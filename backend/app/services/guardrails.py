from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session
from ..models import User

def verify_and_deduct_credit(user_id: int, db: Session) -> bool:
    """
    Checks if a user has sufficient credits or is on a premium plan.
    Deducts 1 credit atomically if they are on a free tier.
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
        
    if user.ai_credits < 1:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Operation requires credits. Your balance is 0. Please upgrade to premium or top-up your credits."
        )
        
    # Atomically decrement 1 credit to prevent race conditions
    db.execute(
        update(User)
        .where(User.id == user_id)
        .values(ai_credits=User.ai_credits - 1)
    )
    db.commit()
    return True
