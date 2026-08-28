from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.core.logging_config import redact_log_text
from app.models.integration import IntegrationDelivery, IntegrationInstance

settings = get_settings()


def defer_delivery(delivery: IntegrationDelivery, *, until: datetime) -> None:
    delivery.state = "retry_wait"
    delivery.claimed_at = None
    delivery.not_before = until


def dead_letter_without_attempt(
    delivery: IntegrationDelivery,
    *,
    code: str,
    message: str,
    now: datetime,
) -> None:
    delivery.state = "dead_letter"
    delivery.claimed_at = None
    delivery.not_before = None
    delivery.dead_lettered_at = now
    delivery.last_error_code = code
    delivery.last_error_message = safe_error_message(message)
    delivery.last_error_retryable = False


def retry_backoff_seconds(delivery: IntegrationDelivery) -> int:
    base = max(1, int(settings.integration_delivery_retry_backoff_seconds))
    maximum = max(base, int(settings.integration_delivery_retry_max_backoff_seconds))
    exponent = max(0, int(delivery.attempt_count or 1) - 1)
    exponential = min(maximum, base * (2**exponent))
    jitter_ceiling = max(1, exponential // 5)
    digest = hashlib.sha256(
        f"{delivery.id}:{delivery.attempt_count}".encode("ascii")
    ).digest()
    jitter = int.from_bytes(digest[:2], "big") % (jitter_ceiling + 1)
    return min(maximum, exponential + jitter)


def safe_error_message(value: str | None) -> str | None:
    if value is None:
        return None
    return redact_log_text(value, max_chars=4000)


def update_circuit(
    instance: IntegrationInstance,
    *,
    success: bool,
    retryable: bool,
    now: datetime,
) -> None:
    if success:
        instance.circuit_state = "closed"
        instance.circuit_failure_count = 0
        instance.circuit_opened_at = None
        instance.circuit_open_until = None
        return
    if not retryable and instance.circuit_state != "half_open":
        return
    instance.circuit_failure_count = (
        max(0, int(instance.circuit_failure_count or 0)) + 1
    )
    threshold = max(1, int(settings.integration_delivery_circuit_failure_threshold))
    if (
        instance.circuit_state == "half_open"
        or instance.circuit_failure_count >= threshold
    ):
        instance.circuit_state = "open"
        instance.circuit_opened_at = now
        instance.circuit_open_until = now + timedelta(
            seconds=max(1, int(settings.integration_delivery_circuit_open_seconds))
        )


def coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
