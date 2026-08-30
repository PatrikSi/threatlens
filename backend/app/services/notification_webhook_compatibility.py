from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.integration import IntegrationAttempt
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.integration_compat import WebhookConfigurationCompatibilityError
from app.services.integration_delivery import (
    DELIVERY_RETRY_WAIT,
    IntegrationDeliveryOutcome,
    WebhookDeliveryIneligibleError,
    finalize_integration_delivery,
    lock_webhook_delivery_external_io_eligibility,
)
from app.services.integration_delivery_attempts import (
    persist_external_side_effect_marker,
)
from app.services.notification_webhook_history import (
    NOTIFICATION_DELIVERY_FAILED,
    NOTIFICATION_DELIVERY_PENDING,
    NotificationWebhookDeliveryAttempt,
    delivery_result_from_model,
)
from app.services.notification_webhook_storage import POLICY_FAILURE_ERROR_PREFIX
from app.services.webhook_delivery_eligibility import (
    WebhookDeliveryTemporarilyIneligibleError,
)


class WebhookExternalIOFenceError(RuntimeError):
    code = "webhook_external_io_fence_failed"


def lock_notification_webhook_external_io_eligibility(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    expected_attempt_number: int,
) -> uuid.UUID:
    if delivery.integration_delivery_id is None:
        raise WebhookDeliveryIneligibleError(
            "webhook_projection_missing",
            "Webhook integration delivery configuration is incomplete.",
        )
    lock_webhook_delivery_external_io_eligibility(
        db,
        webhook_id=delivery.webhook_id,
        legacy_delivery_id=delivery.id,
        integration_delivery_id=delivery.integration_delivery_id,
        expected_attempt_number=expected_attempt_number,
    )
    return delivery.integration_delivery_id


def mark_notification_webhook_external_io_started(
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
) -> None:
    try:
        marked = persist_external_side_effect_marker(
            delivery_id=delivery_id,
            expected_attempt_number=expected_attempt_number,
        )
    except (SQLAlchemyError, TimeoutError) as exc:
        raise WebhookExternalIOFenceError(
            "Webhook delivery could not persist its external-I/O fence; "
            "it will retry before sending the request."
        ) from exc
    if not marked:
        raise WebhookExternalIOFenceError(
            "Webhook delivery attempt is no longer active; the request was not sent."
        )


def defer_claimed_notification_webhook_for_compatibility(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    expected_attempt_number: int,
    error: WebhookConfigurationCompatibilityError,
    commit_outcome: bool,
) -> NotificationWebhookDeliveryAttempt:
    if delivery.integration_delivery_id is None:
        raise WebhookDeliveryIneligibleError(
            "webhook_projection_missing",
            "Webhook integration delivery configuration is incomplete.",
        )
    error_message = str(error)
    known_pre_side_effect = (
        _external_side_effect_marker(
            db,
            delivery_id=delivery.integration_delivery_id,
            expected_attempt_number=expected_attempt_number,
        )
        is False
    )
    if known_pre_side_effect:
        outcome = finalize_integration_delivery(
            db,
            delivery_id=delivery.integration_delivery_id,
            expected_attempt_number=expected_attempt_number,
            success=False,
            duration_ms=0,
            error_code=error.code,
            error_message=error_message,
            retryable=True,
            affect_circuit=False,
            response_json={
                "failure_class": "webhook_schema_compatibility",
                "delivery_outcome": "not_attempted",
                "external_side_effect_possible": False,
                "retry_budget_consumed": False,
            },
        )
    else:
        outcome = _finalize_ambiguous_webhook_outcome(
            db,
            delivery_id=delivery.integration_delivery_id,
            expected_attempt_number=expected_attempt_number,
            error_code=error.code,
            error_message=error_message,
            failure_class="webhook_schema_compatibility_after_io",
        )
    return _apply_deferred_outcome(
        db,
        delivery=delivery,
        outcome=outcome,
        error_message=error_message,
        commit_outcome=commit_outcome,
    )


def defer_claimed_notification_webhook_for_preflight_error(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    expected_attempt_number: int,
    error: WebhookExternalIOFenceError,
    commit_outcome: bool,
) -> NotificationWebhookDeliveryAttempt:
    if delivery.integration_delivery_id is None:
        raise WebhookDeliveryIneligibleError(
            "webhook_projection_missing",
            "Webhook integration delivery configuration is incomplete.",
        )
    error_message = str(error)
    marker = _external_side_effect_marker(
        db,
        delivery_id=delivery.integration_delivery_id,
        expected_attempt_number=expected_attempt_number,
    )
    if marker is False:
        outcome = finalize_integration_delivery(
            db,
            delivery_id=delivery.integration_delivery_id,
            expected_attempt_number=expected_attempt_number,
            success=False,
            duration_ms=0,
            error_code=error.code,
            error_message=error_message,
            retryable=True,
            affect_circuit=False,
            response_json={
                "failure_class": "webhook_preflight_database",
                "delivery_outcome": "not_attempted",
                "external_side_effect_possible": False,
            },
        )
    else:
        outcome = _finalize_ambiguous_webhook_outcome(
            db,
            delivery_id=delivery.integration_delivery_id,
            expected_attempt_number=expected_attempt_number,
            error_code=error.code,
            error_message=error_message,
            failure_class="webhook_preflight_after_io",
        )
    return _apply_deferred_outcome(
        db,
        delivery=delivery,
        outcome=outcome,
        error_message=error_message,
        commit_outcome=commit_outcome,
    )


def defer_claimed_notification_webhook_for_temporary_policy(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    expected_attempt_number: int,
    error: WebhookDeliveryTemporarilyIneligibleError,
    commit_outcome: bool,
) -> NotificationWebhookDeliveryAttempt:
    if delivery.integration_delivery_id is None:
        raise WebhookDeliveryIneligibleError(
            "webhook_projection_missing",
            "Webhook integration delivery configuration is incomplete.",
        )
    error_message = str(error)
    marker = _external_side_effect_marker(
        db,
        delivery_id=delivery.integration_delivery_id,
        expected_attempt_number=expected_attempt_number,
    )
    if marker is False:
        outcome = finalize_integration_delivery(
            db,
            delivery_id=delivery.integration_delivery_id,
            expected_attempt_number=expected_attempt_number,
            success=False,
            duration_ms=0,
            error_code=error.code,
            error_message=error_message,
            retryable=True,
            affect_circuit=False,
            response_json={
                "failure_class": "webhook_temporary_policy",
                "delivery_outcome": "not_attempted",
                "external_side_effect_possible": False,
                "retry_budget_consumed": True,
            },
        )
    else:
        outcome = _finalize_ambiguous_webhook_outcome(
            db,
            delivery_id=delivery.integration_delivery_id,
            expected_attempt_number=expected_attempt_number,
            error_code=error.code,
            error_message=error_message,
            failure_class="webhook_temporary_policy_after_io",
        )
    return _apply_deferred_outcome(
        db,
        delivery=delivery,
        outcome=outcome,
        error_message=error_message,
        commit_outcome=commit_outcome,
    )


def _finalize_ambiguous_webhook_outcome(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
    error_code: str,
    error_message: str,
    failure_class: str,
) -> IntegrationDeliveryOutcome:
    """Stop automatic retries after any webhook request may have left ThreatLens."""

    return finalize_integration_delivery(
        db,
        delivery_id=delivery_id,
        expected_attempt_number=expected_attempt_number,
        success=False,
        duration_ms=0,
        error_code=error_code,
        error_message=error_message,
        retryable=False,
        schedule_retry=False,
        affect_circuit=False,
        response_json={
            "failure_class": failure_class,
            "delivery_outcome": "unknown",
            "external_side_effect_possible": True,
            "automatic_retry_suppressed": True,
        },
    )


def finalize_claimed_notification_webhook_for_policy_error(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    expected_attempt_number: int,
    error: Exception,
    commit_outcome: bool,
) -> NotificationWebhookDeliveryAttempt:
    if delivery.integration_delivery_id is None:
        raise WebhookDeliveryIneligibleError(
            "webhook_projection_missing",
            "Webhook integration delivery configuration is incomplete.",
        )
    error_message = f"{POLICY_FAILURE_ERROR_PREFIX}{error}"
    marker = _external_side_effect_marker(
        db,
        delivery_id=delivery.integration_delivery_id,
        expected_attempt_number=expected_attempt_number,
    )
    error_code = getattr(error, "code", "redirect_policy_error")
    status_code = getattr(error, "status_code", None)
    if not isinstance(status_code, int):
        status_code = None
    duration_ms = getattr(error, "duration_ms", 0)
    if not isinstance(duration_ms, int) or duration_ms < 0:
        duration_ms = 0
    external_side_effect_possible = marker is not False
    outcome = finalize_integration_delivery(
        db,
        delivery_id=delivery.integration_delivery_id,
        expected_attempt_number=expected_attempt_number,
        success=False,
        status_code=status_code,
        duration_ms=duration_ms,
        error_code=error_code,
        error_message=error_message,
        retryable=False,
        schedule_retry=False,
        affect_circuit=False,
        response_json={
            "failure_class": (
                "webhook_policy_after_io"
                if external_side_effect_possible
                else "webhook_policy"
            ),
            "delivery_outcome": (
                "unknown" if external_side_effect_possible else "not_attempted"
            ),
            "external_side_effect_possible": external_side_effect_possible,
            "final_response_status_code": status_code,
        },
    )
    return _apply_deferred_outcome(
        db,
        delivery=delivery,
        outcome=outcome,
        error_message=error_message,
        commit_outcome=commit_outcome,
        status_code=status_code,
        duration_ms=duration_ms,
    )


def _apply_deferred_outcome(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    outcome: IntegrationDeliveryOutcome,
    error_message: str,
    commit_outcome: bool,
    status_code: int | None = None,
    duration_ms: int = 0,
) -> NotificationWebhookDeliveryAttempt:
    if not outcome.recorded:
        db.rollback()
        current = db.get(NotificationWebhookDelivery, delivery.id)
        if current is None:
            raise ValueError("Webhook delivery not found")
        return NotificationWebhookDeliveryAttempt(
            result=delivery_result_from_model(current),
            delivery=current,
            claimed=False,
        )

    retry_scheduled = outcome.state == DELIVERY_RETRY_WAIT
    delivery.delivery_state = (
        NOTIFICATION_DELIVERY_PENDING
        if retry_scheduled
        else NOTIFICATION_DELIVERY_FAILED
    )
    delivery.success = False
    delivery.status_code = status_code
    delivery.duration_ms = duration_ms
    delivery.response_body_preview = None
    delivery.error = error_message
    delivery.claimed_at = None
    delivery.not_before = outcome.retry_at if retry_scheduled else None
    delivery.attempted_at = datetime.now(timezone.utc)
    db.add(delivery)
    if commit_outcome:
        db.commit()
        db.refresh(delivery)
    else:
        db.flush()
    return NotificationWebhookDeliveryAttempt(
        result=delivery_result_from_model(delivery),
        delivery=delivery,
        claimed=False,
    )


def _external_side_effect_marker(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
) -> bool | None:
    attempt_response = db.scalar(
        select(IntegrationAttempt.response_json).where(
            IntegrationAttempt.delivery_id == delivery_id,
            IntegrationAttempt.attempt_number == expected_attempt_number,
        )
    )
    if not isinstance(attempt_response, dict):
        return None
    marker = attempt_response.get("external_side_effect_possible")
    return marker if type(marker) is bool else None
