from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.services.integration_storage import ActiveSMTPSettings


@dataclass(frozen=True)
class SMTPNotificationResult:
    success: bool
    duration_ms: int
    recipient_count: int
    accepted_count: int
    error_code: str | None
    error: str | None
    server_message: str | None
    attempted_at: datetime
    delivery_id: uuid.UUID
    delivery_outcome: str = "unknown"
    accepted_recipients: tuple[str, ...] = ()
    refused_recipients: tuple[str, ...] = ()
    unknown_recipients: tuple[str, ...] = ()


@dataclass(frozen=True)
class SMTPDispatchResult:
    status: str
    reason: str | None = None
    delivery: SMTPNotificationResult | None = None

    @property
    def sent(self) -> bool:
        return self.status == "sent"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"


def notification_smtp_exception_result(
    *,
    started_at: float,
    attempted_at: datetime,
    delivery_id: uuid.UUID,
    active: ActiveSMTPSettings,
    error_code: str,
    error: str,
    server_message: str | None,
    delivery_outcome: str = "not_attempted",
    accepted_recipients: tuple[str, ...] = (),
    refused_recipients: tuple[str, ...] = (),
    unknown_recipients: tuple[str, ...] = (),
) -> SMTPNotificationResult:
    return notification_failure_result(
        started_at=started_at,
        attempted_at=attempted_at,
        delivery_id=delivery_id,
        recipient_count=len(active.to_emails),
        accepted_count=0,
        error_code=error_code,
        error=error,
        server_message=server_message,
        delivery_outcome=delivery_outcome,
        accepted_recipients=accepted_recipients,
        refused_recipients=refused_recipients,
        unknown_recipients=unknown_recipients,
    )


def notification_failure_result(
    *,
    started_at: float,
    attempted_at: datetime,
    delivery_id: uuid.UUID,
    recipient_count: int,
    accepted_count: int,
    error_code: str,
    error: str,
    server_message: str | None,
    delivery_outcome: str = "not_attempted",
    accepted_recipients: tuple[str, ...] = (),
    refused_recipients: tuple[str, ...] = (),
    unknown_recipients: tuple[str, ...] = (),
) -> SMTPNotificationResult:
    return SMTPNotificationResult(
        success=False,
        duration_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
        recipient_count=recipient_count,
        accepted_count=accepted_count,
        error_code=error_code,
        error=error,
        server_message=server_message,
        attempted_at=attempted_at,
        delivery_id=delivery_id,
        delivery_outcome=delivery_outcome,
        accepted_recipients=accepted_recipients,
        refused_recipients=refused_recipients,
        unknown_recipients=unknown_recipients,
    )
