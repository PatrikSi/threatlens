from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.integration import IntegrationAttempt, IntegrationDelivery, IntegrationInstance
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

CLAIMED = "claimed"
DEFERRED = "deferred"
TERMINAL = "terminal"
MISSING = "missing"


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
        return IntegrationDeliveryClaim(status=MISSING, delivery_id=delivery_id, reason="delivery_not_found")
    if delivery.state in DELIVERY_TERMINAL_STATES:
        return _claim_result(delivery, status=TERMINAL, reason=f"delivery_{delivery.state}")

    scheduled_for = _coerce_utc(delivery.not_before)
    if scheduled_for is not None and scheduled_for > current_time:
        return _claim_result(delivery, status=DEFERRED, reason="not_due", scheduled_for=scheduled_for)

    instance = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == delivery.integration_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if instance is None:
        _dead_letter_without_attempt(delivery, code="integration_missing", message="Integration instance no longer exists.", now=current_time)
        db.commit()
        return _claim_result(delivery, status=TERMINAL, reason="integration_missing")
    if not instance.enabled:
        _dead_letter_without_attempt(delivery, code="integration_disabled", message="Integration instance is disabled.", now=current_time)
        db.commit()
        return _claim_result(delivery, status=TERMINAL, reason="integration_disabled")

    stale_cutoff = current_time - timedelta(seconds=settings.notification_delivery_sending_stale_after_seconds)
    claimed_at = _coerce_utc(delivery.claimed_at)
    if delivery.state == DELIVERY_SENDING and claimed_at is not None and claimed_at >= stale_cutoff:
        return _claim_result(delivery, status=DEFERRED, reason="already_claimed", scheduled_for=claimed_at)
    if delivery.state == DELIVERY_SENDING:
        _interrupt_running_attempt(db, generic=delivery, now=current_time)

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
    if instance.circuit_state == "open" and circuit_until is not None and circuit_until > current_time:
        _defer_delivery(delivery, until=circuit_until)
        db.commit()
        return _claim_result(delivery, status=DEFERRED, reason="circuit_open", scheduled_for=circuit_until)
    if instance.circuit_state == "open":
        instance.circuit_state = "half_open"
        db.add(instance)

    active_attempts = db.scalar(
        select(func.count())
        .select_from(IntegrationDelivery)
        .where(
            IntegrationDelivery.integration_id == instance.id,
            IntegrationDelivery.id != delivery.id,
            IntegrationDelivery.state == DELIVERY_SENDING,
            IntegrationDelivery.claimed_at >= stale_cutoff,
        )
    ) or 0
    concurrency_limit = 1 if instance.circuit_state == "half_open" else max(1, int(instance.max_concurrency or 1))
    if int(active_attempts) >= concurrency_limit:
        retry_at = current_time + timedelta(seconds=max(1, settings.integration_delivery_concurrency_defer_seconds))
        _defer_delivery(delivery, until=retry_at)
        db.commit()
        return _claim_result(delivery, status=DEFERRED, reason="concurrency_limited", scheduled_for=retry_at)

    rate_window_start = current_time - timedelta(minutes=1)
    recent_attempts = db.scalar(
        select(func.count())
        .select_from(IntegrationAttempt)
        .where(
            IntegrationAttempt.integration_id == instance.id,
            IntegrationAttempt.started_at >= rate_window_start,
        )
    ) or 0
    rate_limit = max(1, int(instance.rate_limit_per_minute or 1))
    if int(recent_attempts) >= rate_limit:
        retry_at = current_time + timedelta(minutes=1)
        _defer_delivery(delivery, until=retry_at)
        db.commit()
        return _claim_result(delivery, status=DEFERRED, reason="rate_limited", scheduled_for=retry_at)

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
            response_json={},
        )
    )
    db.add(delivery)
    db.commit()
    return _claim_result(delivery, status=CLAIMED, attempt_number=attempt_number)


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
) -> IntegrationDeliveryOutcome:
    completed_at = finished_at or datetime.now(timezone.utc)
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
        return IntegrationDeliveryOutcome(recorded=False, state=getattr(delivery, "state", None))

    instance = db.scalar(
        select(IntegrationInstance).where(IntegrationInstance.id == delivery.integration_id).with_for_update()
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
    attempt.error_message = None if success else error_message
    attempt.retryable = False if success else retryable
    attempt.response_json = dict(response_json or {})

    delivery.claimed_at = None
    delivery.completed_at = completed_at if success else None
    delivery.last_status_code = status_code
    delivery.last_duration_ms = duration_ms
    delivery.last_error_code = None if success else error_code
    delivery.last_error_message = None if success else error_message
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
    elif retryable and int(delivery.attempt_count or 0) < max(1, int(delivery.max_attempts or 1)):
        retry_at = completed_at + timedelta(seconds=_retry_backoff_seconds(delivery))
        delivery.state = DELIVERY_RETRY_WAIT
        delivery.not_before = retry_at
    else:
        delivery.state = DELIVERY_DEAD_LETTER
        delivery.not_before = None
        delivery.dead_lettered_at = completed_at

    if instance is not None:
        _update_circuit(instance, success=success, retryable=retryable, now=completed_at)
        db.add(instance)
    db.add_all([attempt, delivery])
    return IntegrationDeliveryOutcome(recorded=True, state=delivery.state, retry_at=retry_at)


def list_recoverable_integration_delivery_ids(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    current_time = now or datetime.now(timezone.utc)
    stale_cutoff = current_time - timedelta(seconds=settings.notification_delivery_sending_stale_after_seconds)
    batch_size = max(1, int(limit or settings.integration_delivery_recovery_batch_size))
    return list(
        db.scalars(
            select(IntegrationDelivery.id)
            .where(
                or_(
                    and_(
                        IntegrationDelivery.state.in_([DELIVERY_PENDING, DELIVERY_RETRY_WAIT]),
                        or_(IntegrationDelivery.not_before.is_(None), IntegrationDelivery.not_before <= current_time),
                    ),
                    and_(
                        IntegrationDelivery.state == DELIVERY_SENDING,
                        or_(IntegrationDelivery.claimed_at.is_(None), IntegrationDelivery.claimed_at < stale_cutoff),
                    ),
                )
            )
            .order_by(func.coalesce(IntegrationDelivery.not_before, IntegrationDelivery.created_at).asc())
            .limit(batch_size)
        ).all()
    )


def mark_integration_delivery_dead_letter(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    error_code: str | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> bool:
    delivery = db.scalar(select(IntegrationDelivery).where(IntegrationDelivery.id == delivery_id).with_for_update())
    if delivery is None or delivery.state == DELIVERY_SUCCEEDED:
        return False
    current_time = now or datetime.now(timezone.utc)
    delivery.state = DELIVERY_DEAD_LETTER
    delivery.claimed_at = None
    delivery.not_before = None
    delivery.dead_lettered_at = current_time
    delivery.last_error_code = error_code or delivery.last_error_code
    delivery.last_error_message = error_message or delivery.last_error_message
    db.add(delivery)
    return True


def replay_dead_letter_delivery(db: Session, *, delivery_id: uuid.UUID) -> IntegrationDelivery:
    source = db.scalar(select(IntegrationDelivery).where(IntegrationDelivery.id == delivery_id).with_for_update())
    if source is None:
        raise ValueError("Integration delivery not found")
    if source.state != DELIVERY_DEAD_LETTER:
        raise ValueError("Only dead-lettered integration deliveries can be replayed")
    legacy_source = _webhook_replay_source(db, source=source) if source.connector_type == "webhook" else None
    replay_id = uuid.uuid4()
    source_payload = source.payload_json if isinstance(source.payload_json, dict) else {}
    replay_payload = dict(source_payload)
    if legacy_source is not None:
        replay_payload["legacy_webhook_delivery_id"] = str(replay_id)
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

    source_payload = source.payload_json if isinstance(source.payload_json, dict) else {}
    legacy_delivery_id = source_payload.get("legacy_webhook_delivery_id")
    try:
        parsed_legacy_delivery_id = uuid.UUID(str(legacy_delivery_id))
    except (TypeError, ValueError):
        parsed_legacy_delivery_id = source.id
    legacy_source = db.get(NotificationWebhookDelivery, parsed_legacy_delivery_id)
    if legacy_source is None or legacy_source.integration_delivery_id not in {None, source.id}:
        raise ValueError("Webhook delivery history is unavailable and cannot be replayed")
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
    generic = ensure_webhook_delivery(db, webhook=webhook, legacy_delivery=legacy_delivery)
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
        response_json={"response_recorded": status_code is not None},
        finished_at=finished_at,
        schedule_retry=False,
    )
    return outcome.recorded


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


def _defer_delivery(delivery: IntegrationDelivery, *, until: datetime) -> None:
    delivery.state = DELIVERY_RETRY_WAIT
    delivery.claimed_at = None
    delivery.not_before = until


def _dead_letter_without_attempt(
    delivery: IntegrationDelivery,
    *,
    code: str,
    message: str,
    now: datetime,
) -> None:
    delivery.state = DELIVERY_DEAD_LETTER
    delivery.claimed_at = None
    delivery.not_before = None
    delivery.dead_lettered_at = now
    delivery.last_error_code = code
    delivery.last_error_message = message
    delivery.last_error_retryable = False


def _retry_backoff_seconds(delivery: IntegrationDelivery) -> int:
    base = max(1, int(settings.integration_delivery_retry_backoff_seconds))
    maximum = max(base, int(settings.integration_delivery_retry_max_backoff_seconds))
    exponent = max(0, int(delivery.attempt_count or 1) - 1)
    exponential = min(maximum, base * (2**exponent))
    jitter_ceiling = max(1, exponential // 5)
    digest = hashlib.sha256(f"{delivery.id}:{delivery.attempt_count}".encode("ascii")).digest()
    jitter = int.from_bytes(digest[:2], "big") % (jitter_ceiling + 1)
    return min(maximum, exponential + jitter)


def _update_circuit(
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
    instance.circuit_failure_count = max(0, int(instance.circuit_failure_count or 0)) + 1
    threshold = max(1, int(settings.integration_delivery_circuit_failure_threshold))
    if instance.circuit_state == "half_open" or instance.circuit_failure_count >= threshold:
        instance.circuit_state = "open"
        instance.circuit_opened_at = now
        instance.circuit_open_until = now + timedelta(
            seconds=max(1, int(settings.integration_delivery_circuit_open_seconds))
        )


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
