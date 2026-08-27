from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import User
from app.services.auth_rate_limit import (
    check_mfa_action_throttle,
    clear_mfa_action_failures,
    record_mfa_action_failure,
)
from app.services.local_mfa import (
    MFAError,
    MFAInvalidCodeError,
    MFAVerification,
    verify_active_mfa_code,
    verify_active_totp_code,
)


class MFASensitiveActionRateLimitError(MFAError):
    def __init__(self, *, retry_after_seconds: int | None) -> None:
        super().__init__("Too many failed MFA verification attempts. Try again later.")
        self.retry_after_seconds = retry_after_seconds


class MFASensitiveActionThrottleUnavailableError(MFAError):
    pass


def verify_sensitive_mfa_code(
    db: Session,
    *,
    user: User,
    code: str,
    client_ip: str,
    allow_recovery_code: bool = True,
) -> MFAVerification:
    throttle = check_mfa_action_throttle(user.email, client_ip)
    if not throttle.backend_available:
        raise MFASensitiveActionThrottleUnavailableError(
            "Shared MFA verification throttling is temporarily unavailable. Try again later."
        )
    if throttle.blocked:
        raise MFASensitiveActionRateLimitError(
            retry_after_seconds=throttle.retry_after_seconds
        )

    try:
        verifier = (
            verify_active_mfa_code if allow_recovery_code else verify_active_totp_code
        )
        verification = verifier(db, user_id=user.id, code=code)
    except MFAInvalidCodeError as exc:
        record_mfa_action_failure(user.email, client_ip)
        throttle = check_mfa_action_throttle(user.email, client_ip)
        if throttle.blocked:
            raise MFASensitiveActionRateLimitError(
                retry_after_seconds=throttle.retry_after_seconds
            ) from exc
        raise

    clear_mfa_action_failures(
        user.email,
        client_ip,
        observed_failure_version=throttle.failure_version,
    )
    return verification


__all__ = [
    "MFASensitiveActionRateLimitError",
    "MFASensitiveActionThrottleUnavailableError",
    "verify_sensitive_mfa_code",
]
