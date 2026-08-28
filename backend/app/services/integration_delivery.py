from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationInstance,
)
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.integration_compat import ensure_webhook_integration
from app.services.integration_delivery_attempts import interrupt_running_attempt
from app.services.integration_delivery_replay import smtp_replay_recipient_override
from app.services.integration_delivery_state import (
    coerce_utc as _coerce_utc,
    dead_letter_without_attempt as _dead_letter_without_attempt,
    defer_delivery as _defer_delivery,
    retry_backoff_seconds as _retry_backoff_seconds,
    safe_error_message as _safe_error_message,
    update_circuit as _update_circuit,
)
from app.services.webhook_delivery_eligibility import (
    WebhookDeliveryIneligibleError as WebhookDeliveryIneligibleError,
)
from app.services.webhook_delivery_eligibility import (
    lock_webhook_delivery_external_io_eligibility as lock_webhook_delivery_external_io_eligibility,
)

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

CLAIMED = "claimed"
DEFERRED = "deferred"
TERMINAL = "terminal"
MISSING = "missing"

_delivery_claim_observer: ContextVar[Callable[[uuid.UUID, int], None] | None] = (
    ContextVar("integration_delivery_claim_observer", default=None)
)


@dataclass(frozen=True)
class IntegrationDeliveryClaim:
    status: str
    delivery_id: uuid.UUID
    integration_id: uuid.UUID | None = None
    connector_type: str | None = None
    event_type: str | None = None
    attempt_number: int | None = None
    reason: str | None = None
    scheduled_for: datetime | None = None


@dataclass(frozen=True)
class IntegrationDeliveryOutcome:
    recorded: bool
    state: str | None
    retry_at: datetime | None = None


@dataclass(frozen=True)
class IntegrationDeliveryRecoveryReservation:
    delivery_ids: tuple[uuid.UUID, ...]
    reserved_at: datetime


@dataclass
class IntegrationDeliveryClaimTracker:
    delivery_id: uuid.UUID
    attempt_number: int | None = None

    def observe(self, claimed_delivery_id: uuid.UUID, attempt_number: int) -> None:
        if claimed_delivery_id == self.delivery_id:
            self.attempt_number = attempt_number


@contextmanager
def integration_delivery_claim_observer(
    callback: Callable[[uuid.UUID, int], None] | None,
) -> Iterator[None]:
    """Expose successful claim identity to the worker handling its failure path."""

    token = _delivery_claim_observer.set(callback)
    try:
        yield
    finally:
        _delivery_claim_observer.reset(token)


def claim_integration_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    now: datetime | None = None,
) -> IntegrationDeliveryClaim:
    current_time = now or datetime.now(timezone.utc)
    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        return IntegrationDeliveryClaim(
            status=MISSING, delivery_id=delivery_id, reason="delivery_not_found"
        )
    if delivery.state in DELIVERY_TERMINAL_STATES:
        return _claim_result(
            delivery, status=TERMINAL, reason=f"delivery_{delivery.state}"
        )

    scheduled_for = _coerce_utc(delivery.not_before)
    if scheduled_for is not None and scheduled_for > current_time:
        reason = "active_lease" if delivery.state == DELIVERY_SENDING else "not_due"
        return _claim_result(
            delivery, status=DEFERRED, reason=reason, scheduled_for=scheduled_for
        )

    instance = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == delivery.integration_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if instance is None:
        _dead_letter_without_attempt(
            delivery,
            code="integration_missing",
            message="Integration instance no longer exists.",
            now=current_time,
        )
        db.commit()
        return _claim_result(delivery, status=TERMINAL, reason="integration_missing")
    if not instance.enabled:
        _dead_letter_without_attempt(
            delivery,
            code="integration_disabled",
            message="Integration instance is disabled.",
            now=current_time,
        )
        db.commit()
        return _claim_result(delivery, status=TERMINAL, reason="integration_disabled")

    stale_cutoff = current_time - timedelta(
        seconds=settings.notification_delivery_sending_stale_after_seconds
    )
    claimed_at = _coerce_utc(delivery.claimed_at)
    if (
        delivery.state == DELIVERY_SENDING
        and claimed_at is not None
        and claimed_at >= stale_cutoff
    ):
        return _claim_result(
            delivery,
            status=DEFERRED,
            reason="already_claimed",
            scheduled_for=claimed_at,
        )
    if delivery.state == DELIVERY_SENDING:
        side_effect_possible = interrupt_running_attempt(
            db, delivery=delivery, now=current_time
        )
        if delivery.connector_type == "smtp" and side_effect_possible is not False:
            _dead_letter_without_attempt(
                delivery,
                code="unknown_delivery_outcome",
                message=(
                    "The SMTP worker stopped after delivery began, so message acceptance "
                    "is unknown. Replay the delivery explicitly to avoid an automatic duplicate."
                ),
                now=current_time,
            )
            db.commit()
            return _claim_result(
                delivery,
                status=TERMINAL,
                reason="unknown_delivery_outcome",
            )

    if int(delivery.attempt_count or 0) >= max(1, int(delivery.max_attempts or 1)):
        _dead_letter_without_attempt(
            delivery,
            code="attempts_exhausted",
            message="Delivery exhausted its configured attempts.",
            now=current_time,
        )
        db.commit()
        return _claim_result(delivery, status=TERMINAL, reason="attempts_exhausted")

    circuit_until = _coerce_utc(instance.circuit_open_until)
    if (
        instance.circuit_state == "open"
        and circuit_until is not None
        and circuit_until > current_time
    ):
        _defer_delivery(delivery, until=circuit_until)
        db.commit()
        return _claim_result(
            delivery,
            status=DEFERRED,
            reason="circuit_open",
            scheduled_for=circuit_until,
        )
    if instance.circuit_state == "open":
        instance.circuit_state = "half_open"
        db.add(instance)

    active_attempts = (
        db.scalar(
            select(func.count())
            .select_from(IntegrationDelivery)
            .where(
                IntegrationDelivery.integration_id == instance.id,
                IntegrationDelivery.id != delivery.id,
                IntegrationDelivery.state == DELIVERY_SENDING,
                or_(
                    IntegrationDelivery.claimed_at >= stale_cutoff,
                    IntegrationDelivery.not_before > current_time,
                ),
            )
        )
        or 0
    )
    concurrency_limit = (
        1
        if instance.circuit_state == "half_open"
        else max(1, int(instance.max_concurrency or 1))
    )
    if int(active_attempts) >= concurrency_limit:
        retry_at = current_time + timedelta(
            seconds=max(1, settings.integration_delivery_concurrency_defer_seconds)
        )
        _defer_delivery(delivery, until=retry_at)
        db.commit()
        return _claim_result(
            delivery,
            status=DEFERRED,
            reason="concurrency_limited",
            scheduled_for=retry_at,
        )

    rate_window_start = current_time - timedelta(minutes=1)
    recent_attempts = (
        db.scalar(
            select(func.count())
            .select_from(IntegrationAttempt)
            .where(
                IntegrationAttempt.integration_id == instance.id,
                IntegrationAttempt.started_at >= rate_window_start,
            )
        )
        or 0
    )
    rate_limit = max(1, int(instance.rate_limit_per_minute or 1))
    if int(recent_attempts) >= rate_limit:
        retry_at = current_time + timedelta(minutes=1)
        _defer_delivery(delivery, until=retry_at)
        db.commit()
        return _claim_result(
            delivery, status=DEFERRED, reason="rate_limited", scheduled_for=retry_at
        )

    attempt_number = max(0, int(delivery.attempt_count or 0)) + 1
    delivery.state = DELIVERY_SENDING
    delivery.claimed_at = current_time
    delivery.not_before = None
    delivery.attempt_count = attempt_number
    delivery.last_status_code = None
    delivery.last_duration_ms = None
    delivery.last_error_code = None
    delivery.last_error_message = None
    delivery.last_error_retryable = None
    db.add(
        IntegrationAttempt(
            delivery_id=delivery.id,
            integration_id=delivery.integration_id,
            attempt_number=attempt_number,
            status=ATTEMPT_RUNNING,
            started_at=current_time,
            response_json=(
                {
                    "delivery_outcome": "not_attempted",
                    "external_side_effect_possible": False,
                }
                if delivery.connector_type == "smtp"
                else {}
            ),
        )
    )
    db.add(delivery)
    db.commit()
    claim = _claim_result(delivery, status=CLAIMED, attempt_number=attempt_number)
    observer = _delivery_claim_observer.get()
    if observer is not None:
        observer(delivery.id, attempt_number)
    return claim


def renew_integration_delivery_lease(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Renew an active delivery heartbeat without changing its attempt identity."""
    current_time = now or datetime.now(timezone.utc)
    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        delivery is None
        or delivery.state != DELIVERY_SENDING
        or int(delivery.attempt_count or 0) != int(expected_attempt_number)
    ):
        return False
    delivery.claimed_at = current_time
    delivery.not_before = current_time + timedelta(seconds=max(1, int(lease_seconds)))
    db.add(delivery)
    return True


def finalize_integration_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
    success: bool,
    duration_ms: int | None,
    error_code: str | None,
    error_message: str | None,
    retryable: bool,
    status_code: int | None = None,
    response_json: dict | None = None,
    finished_at: datetime | None = None,
    schedule_retry: bool = True,
    affect_circuit: bool = True,
) -> IntegrationDeliveryOutcome:
    completed_at = finished_at or datetime.now(timezone.utc)
    safe_error_message = _safe_error_message(error_message)
    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        delivery is None
        or delivery.state != DELIVERY_SENDING
        or int(delivery.attempt_count or 0) != int(expected_attempt_number)
    ):
        return IntegrationDeliveryOutcome(
            recorded=False, state=getattr(delivery, "state", None)
        )

    instance = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == delivery.integration_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    attempt = db.scalar(
        select(IntegrationAttempt).where(
            IntegrationAttempt.delivery_id == delivery.id,
            IntegrationAttempt.attempt_number == expected_attempt_number,
        )
    )
    if attempt is None:
        attempt = IntegrationAttempt(
            delivery_id=delivery.id,
            integration_id=delivery.integration_id,
            attempt_number=expected_attempt_number,
            status=ATTEMPT_RUNNING,
            started_at=completed_at,
            response_json={},
        )
    attempt.status = ATTEMPT_SUCCEEDED if success else ATTEMPT_FAILED
    attempt.finished_at = completed_at
    attempt.duration_ms = duration_ms
    attempt.status_code = status_code
    attempt.error_code = None if success else error_code
    attempt.error_message = None if success else safe_error_message
    attempt.retryable = False if success else retryable
    attempt.response_json = dict(response_json or {})

    delivery.claimed_at = None
    delivery.completed_at = completed_at if success else None
    delivery.last_status_code = status_code
    delivery.last_duration_ms = duration_ms
    delivery.last_error_code = None if success else error_code
    delivery.last_error_message = None if success else safe_error_message
    delivery.last_error_retryable = False if success else retryable
    retry_at: datetime | None = None
    if success:
        delivery.state = DELIVERY_SUCCEEDED
        delivery.not_before = None
        delivery.dead_lettered_at = None
    elif not schedule_retry:
        delivery.state = DELIVERY_FAILED
        delivery.completed_at = completed_at
        delivery.not_before = None
    elif retryable and int(delivery.attempt_count or 0) < max(
        1, int(delivery.max_attempts or 1)
    ):
        retry_at = completed_at + timedelta(seconds=_retry_backoff_seconds(delivery))
        delivery.state = DELIVERY_RETRY_WAIT
        delivery.not_before = retry_at
    else:
        delivery.state = DELIVERY_DEAD_LETTER
        delivery.not_before = None
        delivery.dead_lettered_at = completed_at

    if instance is not None and affect_circuit:
        _update_circuit(
            instance, success=success, retryable=retryable, now=completed_at
        )
        db.add(instance)
    db.add_all([attempt, delivery])
    return IntegrationDeliveryOutcome(
        recorded=True, state=delivery.state, retry_at=retry_at
    )


def record_integration_delivery_unknown_outcome(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
    error_code: str,
    error_message: str,
    now: datetime | None = None,
) -> IntegrationDeliveryOutcome:
    """Record a claimed worker exit using its durable side-effect boundary."""
    current_time = now or datetime.now(timezone.utc)
    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        return IntegrationDeliveryOutcome(recorded=False, state=None)
    if delivery.state != DELIVERY_SENDING or int(delivery.attempt_count or 0) != int(
        expected_attempt_number
    ):
        return IntegrationDeliveryOutcome(recorded=False, state=delivery.state)
    attempt = db.scalar(
        select(IntegrationAttempt).where(
            IntegrationAttempt.delivery_id == delivery.id,
            IntegrationAttempt.attempt_number == expected_attempt_number,
        )
    )
    attempt_response = (
        attempt.response_json
        if attempt is not None and isinstance(attempt.response_json, dict)
        else {}
    )
    marker = attempt_response.get("external_side_effect_possible")
    known_pre_side_effect = delivery.connector_type == "smtp" and marker is False
    external_side_effect_possible = not known_pre_side_effect
    return finalize_integration_delivery(
        db,
        delivery_id=delivery.id,
        expected_attempt_number=expected_attempt_number,
        success=False,
        duration_ms=None,
        error_code=("worker_preflight_error" if known_pre_side_effect else error_code),
        error_message=error_message,
        retryable=delivery.connector_type != "smtp" or known_pre_side_effect,
        affect_circuit=False,
        response_json={
            "delivery_outcome": (
                "not_attempted" if known_pre_side_effect else "unknown"
            ),
            "external_side_effect_possible": external_side_effect_possible,
        },
        finished_at=current_time,
    )


def defer_unclaimed_integration_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    error_code: str,
    error_message: str,
    delay_seconds: int = 60,
    now: datetime | None = None,
) -> bool:
    """Defer only a delivery that no worker has moved into an active attempt."""

    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None or delivery.state not in {
        DELIVERY_PENDING,
        DELIVERY_RETRY_WAIT,
    }:
        return False
    current_time = now or datetime.now(timezone.utc)
    delivery.state = DELIVERY_RETRY_WAIT
    delivery.claimed_at = None
    delivery.not_before = current_time + timedelta(seconds=max(1, int(delay_seconds)))
    delivery.last_error_code = error_code
    delivery.last_error_message = _safe_error_message(error_message)
    delivery.last_error_retryable = True
    db.add(delivery)
    return True


def list_recoverable_integration_delivery_ids(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    current_time = now or datetime.now(timezone.utc)
    batch_size = max(1, int(limit or settings.integration_delivery_recovery_batch_size))
    return list(
        db.scalars(
            select(IntegrationDelivery.id)
            .where(_recoverable_delivery_predicate(current_time))
            .order_by(
                func.coalesce(
                    IntegrationDelivery.not_before, IntegrationDelivery.created_at
                ).asc()
            )
            .limit(batch_size)
        ).all()
    )


def _recoverable_delivery_predicate(current_time: datetime):
    stale_cutoff = current_time - timedelta(
        seconds=settings.notification_delivery_sending_stale_after_seconds
    )
    publication_cutoff = current_time - _recovery_publication_lease()
    return or_(
        and_(
            IntegrationDelivery.state.in_([DELIVERY_PENDING, DELIVERY_RETRY_WAIT]),
            or_(
                IntegrationDelivery.not_before.is_(None),
                IntegrationDelivery.not_before <= current_time,
            ),
            or_(
                IntegrationDelivery.claimed_at.is_(None),
                IntegrationDelivery.claimed_at < publication_cutoff,
            ),
        ),
        and_(
            IntegrationDelivery.state == DELIVERY_SENDING,
            or_(
                IntegrationDelivery.claimed_at.is_(None),
                IntegrationDelivery.claimed_at < stale_cutoff,
            ),
            or_(
                IntegrationDelivery.not_before.is_(None),
                IntegrationDelivery.not_before <= current_time,
            ),
            or_(
                IntegrationDelivery.updated_at.is_(None),
                IntegrationDelivery.updated_at < publication_cutoff,
            ),
        ),
    )


def _recovery_publication_lease() -> timedelta:
    return timedelta(
        seconds=max(10, int(settings.notification_delivery_sending_stale_after_seconds))
    )


def reserve_recoverable_integration_deliveries(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> IntegrationDeliveryRecoveryReservation:
    """Reserve recovery publication so repeated sweeps do not amplify queue work."""
    current_time = now or datetime.now(timezone.utc)
    batch_size = max(1, int(limit or settings.integration_delivery_recovery_batch_size))
    deliveries = list(
        db.scalars(
            select(IntegrationDelivery)
            .where(_recoverable_delivery_predicate(current_time))
            .order_by(
                func.coalesce(
                    IntegrationDelivery.not_before, IntegrationDelivery.created_at
                ).asc()
            )
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        ).all()
    )
    for delivery in deliveries:
        if delivery.state == DELIVERY_SENDING:
            delivery.updated_at = current_time
        else:
            delivery.claimed_at = current_time
        db.add(delivery)
    return IntegrationDeliveryRecoveryReservation(
        delivery_ids=tuple(delivery.id for delivery in deliveries),
        reserved_at=current_time,
    )


def release_integration_delivery_publications(
    db: Session,
    *,
    delivery_ids: list[uuid.UUID] | tuple[uuid.UUID, ...],
    reserved_at: datetime,
) -> None:
    if not delivery_ids:
        return
    deliveries = db.scalars(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id.in_(delivery_ids))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for delivery in deliveries:
        claimed_at = _coerce_utc(delivery.claimed_at)
        updated_at = _coerce_utc(delivery.updated_at)
        if (
            delivery.state in {DELIVERY_PENDING, DELIVERY_RETRY_WAIT}
            and claimed_at == reserved_at
        ):
            delivery.claimed_at = None
            db.add(delivery)
        elif (
            delivery.state == DELIVERY_SENDING
            and updated_at is not None
            and updated_at >= reserved_at
            and (claimed_at is None or claimed_at < reserved_at)
        ):
            delivery.updated_at = (
                claimed_at or reserved_at - _recovery_publication_lease()
            )
            db.add(delivery)


def mark_integration_delivery_dead_letter(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    error_code: str | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> bool:
    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
    )
    if delivery is None or delivery.state == DELIVERY_SUCCEEDED:
        return False
    current_time = now or datetime.now(timezone.utc)
    delivery.state = DELIVERY_DEAD_LETTER
    delivery.claimed_at = None
    delivery.not_before = None
    delivery.dead_lettered_at = current_time
    delivery.last_error_code = error_code or delivery.last_error_code
    delivery.last_error_message = (
        _safe_error_message(error_message) or delivery.last_error_message
    )
    db.add(delivery)
    return True


def replay_dead_letter_delivery(
    db: Session, *, delivery_id: uuid.UUID
) -> IntegrationDelivery:
    source = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
    )
    if source is None:
        raise ValueError("Integration delivery not found")
    if source.state != DELIVERY_DEAD_LETTER:
        raise ValueError("Only dead-lettered integration deliveries can be replayed")
    legacy_source = (
        _webhook_replay_source(db, source=source)
        if source.connector_type == "webhook"
        else None
    )
    replay_id = uuid.uuid4()
    source_payload = (
        source.payload_json if isinstance(source.payload_json, dict) else {}
    )
    replay_payload = dict(source_payload)
    if legacy_source is not None:
        replay_payload["legacy_webhook_delivery_id"] = str(replay_id)
    elif source.connector_type == "smtp":
        recipient_override = smtp_replay_recipient_override(db, source=source)
        if recipient_override:
            replay_payload["smtp_recipient_override"] = recipient_override
    replay = IntegrationDelivery(
        id=replay_id,
        integration_id=source.integration_id,
        subscription_id=source.subscription_id,
        event_id=source.event_id,
        owner_user_id=source.owner_user_id,
        source_delivery_id=source.id,
        connector_type=source.connector_type,
        event_type=source.event_type,
        delivery_kind="replay",
        state=DELIVERY_PENDING,
        idempotency_key=f"replay:{source.id}:{replay_id}",
        payload_json=replay_payload,
        max_attempts=source.max_attempts,
    )
    db.add(replay)
    db.flush()
    if legacy_source is not None:
        db.add(_clone_webhook_replay(source=legacy_source, replay_id=replay.id))
        db.flush()
    return replay


def _webhook_replay_source(
    db: Session,
    *,
    source: IntegrationDelivery,
) -> NotificationWebhookDelivery:
    legacy_source = db.scalar(
        select(NotificationWebhookDelivery)
        .where(NotificationWebhookDelivery.integration_delivery_id == source.id)
        .with_for_update()
    )
    if legacy_source is not None:
        return legacy_source

    source_payload = (
        source.payload_json if isinstance(source.payload_json, dict) else {}
    )
    legacy_delivery_id = source_payload.get("legacy_webhook_delivery_id")
    try:
        parsed_legacy_delivery_id = uuid.UUID(str(legacy_delivery_id))
    except (TypeError, ValueError):
        parsed_legacy_delivery_id = source.id
    legacy_source = db.get(NotificationWebhookDelivery, parsed_legacy_delivery_id)
    if legacy_source is None or legacy_source.integration_delivery_id not in {
        None,
        source.id,
    }:
        raise ValueError(
            "Webhook delivery history is unavailable and cannot be replayed"
        )
    legacy_source.integration_delivery_id = source.id
    db.add(legacy_source)
    db.flush()
    return legacy_source


def _clone_webhook_replay(
    *,
    source: NotificationWebhookDelivery,
    replay_id: uuid.UUID,
) -> NotificationWebhookDelivery:
    return NotificationWebhookDelivery(
        id=replay_id,
        integration_delivery_id=replay_id,
        webhook_id=source.webhook_id,
        user_id=source.user_id,
        event_type_snapshot=source.event_type_snapshot,
        item_id=source.item_id,
        feed_id=source.feed_id,
        source_delivery_id=source.source_delivery_id or source.id,
        scope_key=source.scope_key,
        delivery_kind="retry",
        delivery_state=DELIVERY_PENDING,
        attempt_count=0,
        not_before=None,
        claimed_at=None,
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=source.timeout_seconds,
        rendered_url=source.rendered_url,
        rendered_method=source.rendered_method,
        rendered_headers_json=list(source.rendered_headers_json or []),
        rendered_query_params_json=list(source.rendered_query_params_json or []),
        rendered_body=source.rendered_body,
        response_body_preview=None,
        error=None,
        item_title_snapshot=source.item_title_snapshot,
        feed_name_snapshot=source.feed_name_snapshot,
        attempted_at=datetime.now(timezone.utc),
    )


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
        delivery_id = (
            legacy_delivery.id
            if db.get(IntegrationDelivery, legacy_delivery.id) is None
            else uuid.uuid4()
        )
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
                if legacy_delivery.delivery_state
                in {DELIVERY_SUCCEEDED, DELIVERY_FAILED}
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
    generic = ensure_webhook_delivery(
        db, webhook=webhook, legacy_delivery=legacy_delivery
    )
    if _reconcile_legacy_claim_state(generic=generic, legacy_delivery=legacy_delivery):
        db.add(generic)
        db.commit()
        if generic.state in DELIVERY_TERMINAL_STATES:
            return None
    claim = claim_integration_delivery(db, delivery_id=generic.id, now=current_time)
    if claim.status != CLAIMED or claim.attempt_number is None:
        return None
    preserve_error = (
        legacy_delivery.delivery_state == DELIVERY_PENDING
        and legacy_delivery.status_code is None
        and legacy_delivery.error is not None
    )
    legacy_delivery.delivery_state = DELIVERY_SENDING
    legacy_delivery.claimed_at = current_time
    legacy_delivery.attempt_count = claim.attempt_number
    legacy_delivery.attempted_at = current_time
    legacy_delivery.status_code = None
    legacy_delivery.duration_ms = None
    legacy_delivery.response_body_preview = None
    if not preserve_error:
        legacy_delivery.error = None
    db.add(legacy_delivery)
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
    generic = ensure_webhook_delivery(
        db, webhook=webhook, legacy_delivery=legacy_delivery
    )
    outcome = finalize_integration_delivery(
        db,
        delivery_id=generic.id,
        expected_attempt_number=expected_attempt_number,
        success=success,
        status_code=status_code,
        duration_ms=duration_ms,
        error_code=_webhook_error_code(status_code=status_code, error=error),
        error_message=error,
        retryable=retryable,
        response_json={
            "response_recorded": status_code is not None,
            "delivery_outcome": (
                "accepted"
                if success
                else "rejected"
                if status_code is not None
                else "unknown"
                if retryable
                else "not_attempted"
            ),
            "external_side_effect_possible": bool(
                not success and retryable and status_code is None
            ),
        },
        finished_at=finished_at,
        schedule_retry=False,
    )
    if outcome.recorded:
        legacy_delivery.claimed_at = None
        legacy_delivery.not_before = None
        db.add(legacy_delivery)
    return outcome.recorded


def list_recoverable_webhook_delivery_ids(
    db: Session,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    current_time = now or datetime.now(timezone.utc)
    stale_cutoff = current_time - timedelta(
        seconds=settings.notification_delivery_sending_stale_after_seconds
    )
    generic_due = or_(
        and_(
            IntegrationDelivery.state.in_([DELIVERY_PENDING, DELIVERY_RETRY_WAIT]),
            or_(
                and_(
                    IntegrationDelivery.not_before.is_(None),
                    IntegrationDelivery.created_at < stale_cutoff,
                ),
                IntegrationDelivery.not_before <= current_time,
            ),
        ),
        and_(
            IntegrationDelivery.state == DELIVERY_SENDING,
            or_(
                IntegrationDelivery.claimed_at.is_(None),
                IntegrationDelivery.claimed_at < stale_cutoff,
            ),
            or_(
                IntegrationDelivery.not_before.is_(None),
                IntegrationDelivery.not_before <= current_time,
            ),
        ),
    )
    linked_ids = list(
        db.scalars(
            select(NotificationWebhookDelivery.id)
            .join(
                IntegrationDelivery,
                IntegrationDelivery.id
                == NotificationWebhookDelivery.integration_delivery_id,
            )
            .where(IntegrationDelivery.connector_type == "webhook", generic_due)
            .order_by(
                func.coalesce(
                    IntegrationDelivery.not_before, IntegrationDelivery.created_at
                ).asc()
            )
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
            or_(
                NotificationWebhookDelivery.not_before.is_(None),
                NotificationWebhookDelivery.not_before <= current_time,
            ),
        ),
    )
    unlinked_ids = list(
        db.scalars(
            select(NotificationWebhookDelivery.id)
            .where(
                NotificationWebhookDelivery.integration_delivery_id.is_(None),
                legacy_due,
            )
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


def _reconcile_legacy_claim_state(
    *,
    generic: IntegrationDelivery,
    legacy_delivery: NotificationWebhookDelivery,
) -> bool:
    legacy_state = legacy_delivery.delivery_state
    if (
        legacy_state in {DELIVERY_SUCCEEDED, DELIVERY_FAILED}
        and generic.state not in DELIVERY_TERMINAL_STATES
    ):
        generic.state = legacy_state
        generic.attempt_count = max(
            int(generic.attempt_count or 0), int(legacy_delivery.attempt_count or 0)
        )
        generic.claimed_at = None
        generic.completed_at = legacy_delivery.attempted_at
        generic.last_status_code = legacy_delivery.status_code
        generic.last_duration_ms = legacy_delivery.duration_ms
        generic.last_error_message = legacy_delivery.error
        return True
    if legacy_state == DELIVERY_SENDING and generic.state in {
        DELIVERY_PENDING,
        DELIVERY_RETRY_WAIT,
    }:
        generic.state = DELIVERY_SENDING
        generic.attempt_count = max(
            int(generic.attempt_count or 0), int(legacy_delivery.attempt_count or 0)
        )
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


def _claim_result(
    delivery: IntegrationDelivery,
    *,
    status: str,
    reason: str | None = None,
    scheduled_for: datetime | None = None,
    attempt_number: int | None = None,
) -> IntegrationDeliveryClaim:
    return IntegrationDeliveryClaim(
        status=status,
        delivery_id=delivery.id,
        integration_id=delivery.integration_id,
        connector_type=delivery.connector_type,
        event_type=delivery.event_type,
        attempt_number=attempt_number,
        reason=reason,
        scheduled_for=scheduled_for,
    )
