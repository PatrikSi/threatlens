from __future__ import annotations

import time
from datetime import datetime

from app.schemas.integration import SMTPTestResponse
from app.services.integration_storage import ActiveSMTPSettings


def smtp_test_failure_response(
    *,
    action: str,
    started_at: float,
    tested_at: datetime,
    recipient_email: str | None,
    error_code: str,
    error: str,
    server_message: str | None,
) -> SMTPTestResponse:
    return smtp_test_response(
        success=False,
        action=action,
        started_at=started_at,
        tested_at=tested_at,
        recipient_email=recipient_email,
        error_code=error_code,
        error=error,
        server_message=server_message,
    )


def smtp_test_timeout_response(
    *,
    action: str,
    started_at: float,
    tested_at: datetime,
    recipient_email: str | None,
    active: ActiveSMTPSettings,
) -> SMTPTestResponse:
    return smtp_test_failure_response(
        action=action,
        started_at=started_at,
        tested_at=tested_at,
        recipient_email=recipient_email,
        error_code="timeout",
        error=f"SMTP test timed out after {active.timeout_seconds}s.",
        server_message=None,
    )


def smtp_test_response(
    *,
    success: bool,
    action: str,
    started_at: float,
    tested_at: datetime,
    recipient_email: str | None,
    error_code: str | None,
    error: str | None,
    server_message: str | None,
) -> SMTPTestResponse:
    return SMTPTestResponse(
        success=success,
        action=action,
        duration_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
        recipient_email=recipient_email,
        error_code=error_code,
        error=error,
        server_message=server_message,
        tested_at=tested_at,
        used_unsaved_settings=False,
    )
