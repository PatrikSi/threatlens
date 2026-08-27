from fastapi import HTTPException, status

from app.core.security import verify_password
from app.models.user import User
from app.services.auth_rate_limit import (
    check_password_verification_throttle,
    clear_password_verification_failures,
    record_password_verification_failure,
)


def verify_current_password_or_raise(
    *,
    user: User,
    candidate_password: str,
    client_ip: str,
) -> None:
    throttle = check_password_verification_throttle(user.email, client_ip)
    if throttle.blocked:
        headers = (
            {"Retry-After": str(throttle.retry_after_seconds)}
            if throttle.retry_after_seconds
            else None
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed current password verification attempts. Try again later.",
            headers=headers,
        )

    if not verify_password(candidate_password, user.password_hash):
        record_password_verification_failure(user.email, client_ip)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    clear_password_verification_failures(
        user.email,
        client_ip,
        observed_failure_version=throttle.failure_version,
    )
