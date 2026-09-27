"""Account eligibility shared by HTTP authentication and delegated credentials."""

from fastapi import HTTPException, status
from app.models.user import User


def ensure_user_can_authenticate(user: User) -> None:
    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive"
        )
