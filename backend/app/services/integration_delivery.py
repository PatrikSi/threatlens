from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.integration import IntegrationAttempt, IntegrationDelivery
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.integration_compat import ensure_webhook_integration

settings = get_settings()

DELIVERY_PENDING = "pending"
DELIVERY_SENDING = "sending"
DELIVERY_SUCCEEDED = "succeeded"
DELIVERY_FAILED = "failed"
DELIVERY_RETRY_WAIT = "retry_wait"
DELIVERY_DEAD_LETTER = "dead_letter"
DELIVERY_TERMINAL_STATES = (DELIVERY_SUCCEEDED, DELIVERY_FAILED, DELIVERY_DEAD_LETTER)

ATTEMPT_RUNNING = "running"
ATTEMPT_SUCCEEDED = "succeeded"
ATTEMPT_FAILED = "failed"
ATTEMPT_INTERRUPTED = "interrupted"


def ensure_webhook_delivery(
    db: Session,
    *,
    webhook: NotificationWebhook,
    legacy_delivery: NotificationWebhookDelivery,
    event_id: uuid.UUID | None = None,
) -> IntegrationDelivery:
    instance, subscription = ensure_webhook_integration(db, webhook)
    delivery = (
        db.get(IntegrationDelivery, legacy_delivery.integration_delivery_id)
        if legacy_delivery.integration_delivery_id is not None
        else None
    )
    if delivery is None:
        delivery_id = legacy_delivery.id if db.get(IntegrationDelivery, legacy_delivery.id) is None else uuid.uuid4()
        delivery = IntegrationDelivery(
            id=delivery_id,
            integration_id=instance.id,
            subscription_id=subscription.id,
            event_id=event_id,
            owner_user_id=legacy_delivery.user_id,
            source_delivery_id=_generic_source_delivery_id(db, legacy_delivery),
            connector_type="webhook",
            event_type=legacy_delivery.event_type_snapshot,
            delivery_kind=legacy_delivery.delivery_kind,
            state=legacy_delivery.delivery_state,
            idempotency_key=f"legacy-webhook-delivery:{legacy_delivery.id}",
            payload_json={"legacy_webhook_delivery_id": str(legacy_delivery.id)},
            attempt_count=max(0, int(legacy_delivery.attempt_count or 0)),
            max_attempts=max(1, int(settings.notification_delivery_retry_max_attempts)),
            not_before=legacy_delivery.not_before,
            claimed_at=legacy_delivery.claimed_at,
            completed_at=(
                legacy_delivery.attempted_at
                if legacy_delivery.delivery_state in {DELIVERY_SUCCEEDED, DELIVERY_FAILED}
                else None
            ),
            last_status_code=legacy_delivery.status_code,
            last_duration_ms=legacy_delivery.duration_ms,
            last_error_message=legacy_delivery.error,
            created_at=legacy_delivery.attempted_at,
        )
        db.add(delivery)
        db.flush()
        legacy_delivery.integration_delivery_id = delivery.id
        db.add(legacy_delivery)
        db.flush()
    elif event_id is not None and delivery.event_id is None:
        delivery.event_id = event_id
        db.add(delivery)
        db.flush()
    return delivery


def claim_webhook_delivery(
    db: Session,
    *,
    webhook: NotificationWebhook,
    legacy_delivery: NotificationWebhookDelivery,
    now: datetime | None = None,
) -> NotificationWebhookDelivery | None:
    current_time = now or datetime.now(timezone.utc)
    generic = ensure_webhook_delivery(db, webhook=webhook, legacy_delivery=legacy_delivery)
    generic = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == generic.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if generic is None:
        return None
    reconciled = _reconcile_legacy_claim_state(generic=generic, legacy_delivery=legacy_delivery)
    if generic.state in DELIVERY_TERMINAL_STATES:
        if reconciled:
            db.add(generic)
            db.commit()
        return None

    scheduled_for = _coerce_utc(generic.not_before)
    if scheduled_for is not None and scheduled_for > current_time:
        return None

    stale_cutoff = current_time - timedelta(seconds=settings.notification_delivery_sending_stale_after_seconds)
    claimed_at = _coerce_utc(generic.claimed_at)
    if generic.state == DELIVERY_SENDING and claimed_at is not None and claimed_at >= stale_cutoff:
        if reconciled:
            db.add(generic)
            db.commit()
        return None
    if generic.state == DELIVERY_SENDING:
        _interrupt_running_attempt(db, generic=generic, now=current_time)

    attempt_number = max(0, int(generic.attempt_count or 0)) + 1
    generic.state = DELIVERY_SENDING
    generic.claimed_at = current_time
    generic.attempt_count = attempt_number
    generic.not_before = None
    generic.last_status_code = None
    generic.last_duration_ms = None
    generic.last_error_code = None
    generic.last_error_message = None
    generic.last_error_retryable = None
    db.add(
        IntegrationAttempt(
            delivery_id=generic.id,
            integration_id=generic.integration_id,
            attempt_number=attempt_number,
            status=ATTEMPT_RUNNING,
            started_at=current_time,
            response_json={},
        )
    )

    preserve_error = (
        legacy_delivery.delivery_state == DELIVERY_PENDING
        and legacy_delivery.status_code is None
        and legacy_delivery.error is not None
    )
    legacy_delivery.delivery_state = DELIVERY_SENDING
    legacy_delivery.claimed_at = current_time
    legacy_delivery.attempt_count = attempt_number
    legacy_delivery.attempted_at = current_time
    legacy_delivery.status_code = None
    legacy_delivery.duration_ms = None
    legacy_delivery.response_body_preview = None
    if not preserve_error:
        legacy_delivery.error = None
    db.add_all([generic, legacy_delivery])
    db.commit()
    db.refresh(legacy_delivery)
    return legacy_delivery


def finalize_webhook_delivery(
    db: Session,
    *,
    legacy_delivery: NotificationWebhookDelivery,
    success: bool,
    status_code: int | None,
    duration_ms: int | None,
    error: str | None,
    retryable: bool,
    expected_attempt_number: int,
    finished_at: datetime | None = None,
) -> bool:
    webhook = db.get(NotificationWebhook, legacy_delivery.webhook_id)
    if webhook is None:
        return False
    generic = ensure_webhook_delivery(db, webhook=webhook, legacy_delivery=legacy_delivery)
    generic = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == generic.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if generic is None:
        return False
    if generic.state != DELIVERY_SENDING or int(generic.attempt_count or 0) != int(expected_attempt_number):
        return False

    completed_at = finished_at or datetime.now(timezone.utc)
    attempt_number = max(1, int(expected_attempt_number))
    attempt = db.scalar(
        select(IntegrationAttempt).where(
            IntegrationAttempt.delivery_id == generic.id,
            IntegrationAttempt.attempt_number == attempt_number,
        )
    )
    if attempt is None:
        attempt = IntegrationAttempt(
            delivery_id=generic.id,
            integration_id=generic.integration_id,
            attempt_number=attempt_number,
            status=ATTEMPT_RUNNING,
            started_at=completed_at,
            response_json={},
        )
    attempt.status = ATTEMPT_SUCCEEDED if success else ATTEMPT_FAILED
    attempt.finished_at = completed_at
    attempt.duration_ms = duration_ms
    attempt.status_code = status_code
    attempt.error_code = _webhook_error_code(status_code=status_code, error=error)
    attempt.error_message = error
    attempt.retryable = False if success else retryable
    attempt.response_json = {"response_recorded": status_code is not None}

    generic.state = DELIVERY_SUCCEEDED if success else DELIVERY_FAILED
    generic.completed_at = completed_at
    generic.claimed_at = None
    generic.last_status_code = status_code
    generic.last_duration_ms = duration_ms
    generic.last_error_code = None if success else attempt.error_code
    generic.last_error_message = None if success else error
    generic.last_error_retryable = False if success else retryable
    db.add_all([attempt, generic])
    return True


def list_recoverable_webhook_delivery_ids(
    db: Session,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    current_time = now or datetime.now(timezone.utc)
    stale_cutoff = current_time - timedelta(seconds=settings.notification_delivery_sending_stale_after_seconds)
    generic_due = or_(
        and_(
            IntegrationDelivery.state.in_([DELIVERY_PENDING, DELIVERY_RETRY_WAIT]),
            or_(
                and_(IntegrationDelivery.not_before.is_(None), IntegrationDelivery.created_at < stale_cutoff),
                IntegrationDelivery.not_before <= current_time,
            ),
        ),
        and_(
            IntegrationDelivery.state == DELIVERY_SENDING,
            or_(IntegrationDelivery.claimed_at.is_(None), IntegrationDelivery.claimed_at < stale_cutoff),
        ),
    )
    linked_ids = list(
        db.scalars(
            select(NotificationWebhookDelivery.id)
            .join(
                IntegrationDelivery,
                IntegrationDelivery.id == NotificationWebhookDelivery.integration_delivery_id,
            )
            .where(IntegrationDelivery.connector_type == "webhook", generic_due)
            .order_by(func.coalesce(IntegrationDelivery.not_before, IntegrationDelivery.created_at).asc())
            .limit(max(0, int(limit)))
        ).all()
    )
    remaining = max(0, int(limit) - len(linked_ids))
    if remaining == 0:
        return linked_ids

    legacy_due = or_(
        and_(
            NotificationWebhookDelivery.delivery_state == DELIVERY_PENDING,
            or_(
                and_(
                    NotificationWebhookDelivery.not_before.is_(None),
                    NotificationWebhookDelivery.attempted_at < stale_cutoff,
                ),
                NotificationWebhookDelivery.not_before <= current_time,
            ),
        ),
        and_(
            NotificationWebhookDelivery.delivery_state == DELIVERY_SENDING,
            or_(
                NotificationWebhookDelivery.claimed_at.is_(None),
                NotificationWebhookDelivery.claimed_at < stale_cutoff,
            ),
        ),
    )
    unlinked_ids = list(
        db.scalars(
            select(NotificationWebhookDelivery.id)
            .where(NotificationWebhookDelivery.integration_delivery_id.is_(None), legacy_due)
            .order_by(
                func.coalesce(
                    NotificationWebhookDelivery.not_before,
                    NotificationWebhookDelivery.attempted_at,
                ).asc()
            )
            .limit(remaining)
        ).all()
    )
    return [*linked_ids, *unlinked_ids]


def _generic_source_delivery_id(
    db: Session,
    legacy_delivery: NotificationWebhookDelivery,
) -> uuid.UUID | None:
    if legacy_delivery.source_delivery_id is None:
        return None
    source = db.get(NotificationWebhookDelivery, legacy_delivery.source_delivery_id)
    if source is None:
        return None
    return source.integration_delivery_id


def _interrupt_running_attempt(db: Session, *, generic: IntegrationDelivery, now: datetime) -> None:
    attempt = db.scalar(
        select(IntegrationAttempt).where(
            IntegrationAttempt.delivery_id == generic.id,
            IntegrationAttempt.attempt_number == max(1, int(generic.attempt_count or 0)),
            IntegrationAttempt.status == ATTEMPT_RUNNING,
        )
    )
    if attempt is None:
        return
    attempt.status = ATTEMPT_INTERRUPTED
    attempt.finished_at = now
    attempt.error_code = "worker_interrupted"
    attempt.error_message = "Delivery worker stopped before recording an outcome."
    attempt.retryable = True
    db.add(attempt)


def _reconcile_legacy_claim_state(
    *,
    generic: IntegrationDelivery,
    legacy_delivery: NotificationWebhookDelivery,
) -> bool:
    legacy_state = legacy_delivery.delivery_state
    if legacy_state in {DELIVERY_SUCCEEDED, DELIVERY_FAILED} and generic.state not in DELIVERY_TERMINAL_STATES:
        generic.state = legacy_state
        generic.attempt_count = max(int(generic.attempt_count or 0), int(legacy_delivery.attempt_count or 0))
        generic.claimed_at = None
        generic.completed_at = legacy_delivery.attempted_at
        generic.last_status_code = legacy_delivery.status_code
        generic.last_duration_ms = legacy_delivery.duration_ms
        generic.last_error_message = legacy_delivery.error
        return True
    if legacy_state == DELIVERY_SENDING and generic.state in {DELIVERY_PENDING, DELIVERY_RETRY_WAIT}:
        generic.state = DELIVERY_SENDING
        generic.attempt_count = max(int(generic.attempt_count or 0), int(legacy_delivery.attempt_count or 0))
        generic.claimed_at = legacy_delivery.claimed_at
        return True
    return False


def _webhook_error_code(*, status_code: int | None, error: str | None) -> str | None:
    if not error and status_code is None:
        return None
    if error and error.startswith("render_error:"):
        return "render_error"
    if error and error.startswith("policy_error:"):
        return "policy_error"
    if status_code is not None:
        return f"http_{status_code}"
    return "network_error"


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
