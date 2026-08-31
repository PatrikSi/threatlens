from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, false, func, or_, select, true
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.data_policy import DataAccessEnvelope, DataAccessEnvelopeLabel
from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import (
    NotificationAnalyticsEventSummary,
    NotificationAnalyticsResponse,
    NotificationAnalyticsWebhookSummary,
    NotificationEventType,
    NotificationQueueSnapshot,
    NotificationWebhookTestResponse,
)
from app.services.integration_compat import lock_notification_webhook
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import (
    DataAccessContext,
    fence_data_access_context,
    handling_label_access_predicate,
)
from app.services.integration_delivery import (
    claim_webhook_delivery as claim_generic_webhook_delivery,
    ensure_webhook_delivery,
    finalize_webhook_delivery as finalize_generic_webhook_delivery,
)
from app.services.notification_webhook_requests import RenderedNotificationRequest
from app.services.notification_webhook_storage import (
    POLICY_FAILURE_ERROR_PREFIX,
    RENDER_FAILURE_ERROR_PREFIX,
    decrypt_notification_text,
    encrypt_notification_json,
    encrypt_notification_text,
    notification_error_for_display,
    notification_fields_from_storage,
    notification_fields_to_storage,
    upgrade_notification_webhook_delivery_secret_storage,
)
from app.services.webhook_delivery_locking import WebhookDeliveryBusyError

settings = get_settings()

NOTIFICATION_DELIVERY_PENDING = "pending"
NOTIFICATION_DELIVERY_SENDING = "sending"
NOTIFICATION_DELIVERY_SUCCEEDED = "succeeded"
NOTIFICATION_DELIVERY_FAILED = "failed"
NOTIFICATION_DELIVERY_TERMINAL_STATES = (
    NOTIFICATION_DELIVERY_SUCCEEDED,
    NOTIFICATION_DELIVERY_FAILED,
)
NOTIFICATION_DELIVERY_RECOVERY_BATCH_SIZE = (
    settings.notification_delivery_recovery_batch_size
)
NOTIFICATION_DELIVERY_STALE_AFTER = timedelta(
    seconds=settings.notification_delivery_sending_stale_after_seconds
)
NOTIFICATION_DELIVERY_QUEUE_DEGRADED_AFTER = timedelta(
    seconds=settings.notification_delivery_queue_degraded_after_seconds
)


@dataclass
class NotificationWebhookDeliveryAttempt:
    result: NotificationWebhookTestResponse
    delivery: NotificationWebhookDelivery
    claimed: bool = True


@dataclass(frozen=True)
class NotificationWebhookRetryReservation:
    delivery: NotificationWebhookDelivery
    created: bool
    countdown_seconds: int | None = None


@dataclass(frozen=True)
class NotificationDeliveryReservationBatch:
    delivery_ids: list[uuid.UUID]
    matched_webhooks: int
    skipped: int


@dataclass(frozen=True, slots=True)
class NotificationDeliveryWouldDenySummary:
    affected_count: int
    handling_label_ids: frozenset[uuid.UUID]


def has_recent_notification_delivery(
    db: Session,
    *,
    webhook_id: uuid.UUID,
    event_type: NotificationEventType,
    since: datetime | None = None,
    item_id: uuid.UUID | None = None,
    feed_id: uuid.UUID | None = None,
    source_delivery_id: uuid.UUID | None = None,
    scope_key: str | None = None,
    delivery_kind: str = "live",
    success_only: bool = False,
    states: tuple[str, ...] | None = None,
) -> bool:
    query = select(NotificationWebhookDelivery.id).where(
        NotificationWebhookDelivery.webhook_id == webhook_id,
        NotificationWebhookDelivery.event_type_snapshot == event_type,
        NotificationWebhookDelivery.delivery_kind == delivery_kind,
    )
    if since is not None:
        query = query.where(NotificationWebhookDelivery.attempted_at >= since)
    if item_id is not None:
        query = query.where(NotificationWebhookDelivery.item_id == item_id)
    if feed_id is not None:
        query = query.where(NotificationWebhookDelivery.feed_id == feed_id)
    if source_delivery_id is not None:
        query = query.where(
            NotificationWebhookDelivery.source_delivery_id == source_delivery_id
        )
    if scope_key is not None:
        query = query.where(NotificationWebhookDelivery.scope_key == scope_key)
    if states:
        query = query.where(NotificationWebhookDelivery.delivery_state.in_(states))
    if success_only:
        query = query.where(
            NotificationWebhookDelivery.delivery_state
            == NOTIFICATION_DELIVERY_SUCCEEDED
        )
    return db.scalar(query.limit(1)) is not None


def try_acquire_notification_delivery_lock(
    db: Session,
    *,
    webhook_id: uuid.UUID,
    event_type: NotificationEventType,
    delivery_kind: str = "live",
    item_id: uuid.UUID | None = None,
    feed_id: uuid.UUID | None = None,
    source_delivery_id: uuid.UUID | None = None,
    scope_key: str | None = None,
) -> bool:
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return True

    digest = hashlib.blake2b(
        "|".join(
            [
                str(webhook_id),
                event_type,
                delivery_kind,
                str(item_id or ""),
                str(feed_id or ""),
                str(source_delivery_id or ""),
                scope_key or "",
            ]
        ).encode("utf-8"),
        digest_size=8,
    ).digest()
    left = int.from_bytes(digest[:4], "big", signed=True)
    right = int.from_bytes(digest[4:], "big", signed=True)
    return bool(db.scalar(select(func.pg_try_advisory_xact_lock(left, right))))


def get_notification_analytics(
    db: Session,
    *,
    user_id: uuid.UUID,
    data_access: DataAccessContext | None = None,
) -> NotificationAnalyticsResponse:
    access_predicate = notification_delivery_data_access_predicate(data_access)
    terminal_filter = NotificationWebhookDelivery.delivery_state.in_(
        NOTIFICATION_DELIVERY_TERMINAL_STATES
    )
    total_deliveries = int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(
                NotificationWebhookDelivery.user_id == user_id,
                terminal_filter,
                access_predicate,
            )
        )
        or 0
    )
    successful_deliveries = int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(
                NotificationWebhookDelivery.user_id == user_id,
                NotificationWebhookDelivery.delivery_state
                == NOTIFICATION_DELIVERY_SUCCEEDED,
                access_predicate,
            )
        )
        or 0
    )
    failed_deliveries = total_deliveries - successful_deliveries
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    failures_last_24h = int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(
                NotificationWebhookDelivery.user_id == user_id,
                NotificationWebhookDelivery.delivery_state
                == NOTIFICATION_DELIVERY_FAILED,
                NotificationWebhookDelivery.attempted_at >= cutoff,
                access_predicate,
            )
        )
        or 0
    )

    event_rows = db.execute(
        select(
            NotificationWebhookDelivery.event_type_snapshot,
            NotificationWebhookDelivery.delivery_state,
            func.count().label("count"),
        )
        .where(
            NotificationWebhookDelivery.user_id == user_id,
            terminal_filter,
            access_predicate,
        )
        .group_by(
            NotificationWebhookDelivery.event_type_snapshot,
            NotificationWebhookDelivery.delivery_state,
        )
        .order_by(NotificationWebhookDelivery.event_type_snapshot.asc())
    ).all()
    events_by_type: dict[str, dict[str, int]] = {}
    for event_type, delivery_state, count in event_rows:
        bucket = events_by_type.setdefault(event_type, {"total": 0, "failed": 0})
        bucket["total"] += int(count)
        if delivery_state == NOTIFICATION_DELIVERY_FAILED:
            bucket["failed"] += int(count)
    events = [
        NotificationAnalyticsEventSummary(
            event_type=event_type,
            total_deliveries=stats["total"],
            failed_deliveries=stats["failed"],
        )
        for event_type, stats in sorted(events_by_type.items())
    ]

    failing_webhook_row = db.execute(
        select(
            NotificationWebhookDelivery.webhook_id,
            NotificationWebhook.name,
            func.count().label("failed_count"),
            func.max(NotificationWebhookDelivery.attempted_at).label("last_failure_at"),
        )
        .join(
            NotificationWebhook,
            NotificationWebhook.id == NotificationWebhookDelivery.webhook_id,
        )
        .where(
            NotificationWebhookDelivery.user_id == user_id,
            NotificationWebhookDelivery.delivery_state == NOTIFICATION_DELIVERY_FAILED,
            access_predicate,
        )
        .group_by(NotificationWebhookDelivery.webhook_id, NotificationWebhook.name)
        .order_by(
            func.count().desc(),
            func.max(NotificationWebhookDelivery.attempted_at).desc(),
        )
        .limit(1)
    ).first()
    most_failing_webhook = None
    if failing_webhook_row is not None:
        most_failing_webhook = NotificationAnalyticsWebhookSummary(
            webhook_id=failing_webhook_row.webhook_id,
            webhook_name=failing_webhook_row.name,
            failed_deliveries=int(failing_webhook_row.failed_count or 0),
            last_failure_at=failing_webhook_row.last_failure_at,
        )

    success_rate_pct = (
        round((successful_deliveries / total_deliveries) * 100, 1)
        if total_deliveries
        else 0.0
    )
    return NotificationAnalyticsResponse(
        total_deliveries=total_deliveries,
        successful_deliveries=successful_deliveries,
        failed_deliveries=failed_deliveries,
        success_rate_pct=success_rate_pct,
        failures_last_24h=failures_last_24h,
        most_failing_webhook=most_failing_webhook,
        events=events,
        queue=get_notification_delivery_queue_snapshot(
            db,
            user_id=user_id,
            data_access=data_access,
        ),
    )


def get_notification_delivery_queue_snapshot(
    db: Session,
    *,
    user_id: uuid.UUID | None = None,
    data_access: DataAccessContext | None = None,
    now: datetime | None = None,
) -> NotificationQueueSnapshot:
    current_time = now or datetime.now(timezone.utc)
    stale_cutoff = current_time - NOTIFICATION_DELIVERY_STALE_AFTER
    base_filters = [notification_delivery_data_access_predicate(data_access)]
    if user_id is not None:
        base_filters.append(NotificationWebhookDelivery.user_id == user_id)
    pending_filters = [
        *base_filters,
        NotificationWebhookDelivery.delivery_state == NOTIFICATION_DELIVERY_PENDING,
    ]
    sending_filters = [
        *base_filters,
        NotificationWebhookDelivery.delivery_state == NOTIFICATION_DELIVERY_SENDING,
    ]
    stale_sending_filters = [
        *sending_filters,
        or_(
            NotificationWebhookDelivery.claimed_at.is_(None),
            NotificationWebhookDelivery.claimed_at < stale_cutoff,
        ),
    ]

    pending_deliveries = int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(*pending_filters)
        )
        or 0
    )
    sending_deliveries = int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(*sending_filters)
        )
        or 0
    )
    stale_sending_deliveries = int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(*stale_sending_filters)
        )
        or 0
    )
    oldest_pending_at = db.scalar(
        select(
            func.min(
                func.coalesce(
                    NotificationWebhookDelivery.not_before,
                    NotificationWebhookDelivery.attempted_at,
                )
            )
        ).where(*pending_filters)
    )
    oldest_sending_at = db.scalar(
        select(
            func.min(
                func.coalesce(
                    NotificationWebhookDelivery.claimed_at,
                    NotificationWebhookDelivery.attempted_at,
                )
            )
        ).where(*sending_filters)
    )
    oldest_pending_age_seconds = seconds_since(current_time, oldest_pending_at)
    oldest_sending_age_seconds = seconds_since(current_time, oldest_sending_at)

    status = "healthy"
    if stale_sending_deliveries > 0:
        status = "critical"
    elif oldest_pending_age_seconds is not None and oldest_pending_age_seconds >= int(
        NOTIFICATION_DELIVERY_QUEUE_DEGRADED_AFTER.total_seconds()
    ):
        status = "degraded"
    return NotificationQueueSnapshot(
        status=status,
        ok=status == "healthy",
        pending_deliveries=pending_deliveries,
        sending_deliveries=sending_deliveries,
        stale_sending_deliveries=stale_sending_deliveries,
        oldest_pending_age_seconds=oldest_pending_age_seconds,
        oldest_sending_age_seconds=oldest_sending_age_seconds,
        degraded_after_seconds=int(
            NOTIFICATION_DELIVERY_QUEUE_DEGRADED_AFTER.total_seconds()
        ),
        stale_after_seconds=int(NOTIFICATION_DELIVERY_STALE_AFTER.total_seconds()),
    )


def notification_delivery_data_access_predicate(
    data_access: DataAccessContext | None,
):
    if data_access is None:
        return true()
    if not data_access.principal_eligible:
        return false()
    if not data_access.enforced:
        return true()
    generic_delivery = and_(
        NotificationWebhookDelivery.integration_delivery_id.is_not(None),
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            NotificationWebhookDelivery.integration_delivery_id,
            data_access,
        ),
    )
    legacy_item = and_(
        NotificationWebhookDelivery.integration_delivery_id.is_(None),
        NotificationWebhookDelivery.item_id.is_not(None),
        exists(
            select(Item.id)
            .join(Feed, Feed.id == Item.feed_id)
            .where(
                Item.id == NotificationWebhookDelivery.item_id,
                or_(
                    NotificationWebhookDelivery.feed_id.is_(None),
                    Item.feed_id == NotificationWebhookDelivery.feed_id,
                ),
                handling_label_access_predicate(
                    Feed.handling_label_id,
                    data_access,
                ),
            )
        ),
    )
    legacy_feed = and_(
        NotificationWebhookDelivery.integration_delivery_id.is_(None),
        NotificationWebhookDelivery.item_id.is_(None),
        NotificationWebhookDelivery.feed_id.is_not(None),
        NotificationWebhookDelivery.event_type_snapshot == "feed_failing",
        exists(
            select(Feed.id).where(
                Feed.id == NotificationWebhookDelivery.feed_id,
                handling_label_access_predicate(
                    Feed.handling_label_id,
                    data_access,
                ),
            )
        ),
    )
    return or_(generic_delivery, legacy_item, legacy_feed)


def notification_delivery_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
    user_id: uuid.UUID | None = None,
    webhook_id: uuid.UUID | None = None,
    delivery_id: uuid.UUID | None = None,
) -> NotificationDeliveryWouldDenySummary:
    """Summarize audit-visible history that enforced mode would withhold."""

    if not data_access.auditing or not data_access.principal_eligible:
        return NotificationDeliveryWouldDenySummary(0, frozenset())

    fence_data_access_context(db, data_access)
    enforced_context = replace(data_access, mode="enforced")
    filters = []
    if user_id is not None:
        filters.append(NotificationWebhookDelivery.user_id == user_id)
    if webhook_id is not None:
        filters.append(NotificationWebhookDelivery.webhook_id == webhook_id)
    if delivery_id is not None:
        filters.append(NotificationWebhookDelivery.id == delivery_id)
    denied = ~notification_delivery_data_access_predicate(enforced_context)
    affected_count = int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(*filters, denied)
        )
        or 0
    )
    if not affected_count:
        return NotificationDeliveryWouldDenySummary(0, frozenset())

    envelope_labels = db.scalars(
        select(DataAccessEnvelopeLabel.label_id)
        .select_from(NotificationWebhookDelivery)
        .join(
            DataAccessEnvelope,
            and_(
                DataAccessEnvelope.resource_type
                == DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
                DataAccessEnvelope.resource_id
                == NotificationWebhookDelivery.integration_delivery_id,
            ),
        )
        .join(
            DataAccessEnvelopeLabel,
            DataAccessEnvelopeLabel.envelope_id == DataAccessEnvelope.id,
        )
        .where(
            *filters,
            DataAccessEnvelopeLabel.label_id.not_in(
                enforced_context.allowed_label_ids
            ),
        )
        .distinct()
    ).all()
    item_labels = db.scalars(
        select(Feed.handling_label_id)
        .select_from(NotificationWebhookDelivery)
        .join(Item, Item.id == NotificationWebhookDelivery.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            *filters,
            NotificationWebhookDelivery.integration_delivery_id.is_(None),
            Feed.handling_label_id.not_in(enforced_context.allowed_label_ids),
        )
        .distinct()
    ).all()
    feed_labels = db.scalars(
        select(Feed.handling_label_id)
        .select_from(NotificationWebhookDelivery)
        .join(Feed, Feed.id == NotificationWebhookDelivery.feed_id)
        .where(
            *filters,
            NotificationWebhookDelivery.integration_delivery_id.is_(None),
            Feed.handling_label_id.not_in(enforced_context.allowed_label_ids),
        )
        .distinct()
    ).all()
    return NotificationDeliveryWouldDenySummary(
        affected_count,
        frozenset((*envelope_labels, *item_labels, *feed_labels)),
    )


def delivery_result_from_model(
    delivery: NotificationWebhookDelivery,
) -> NotificationWebhookTestResponse:
    upgrade_notification_webhook_delivery_secret_storage(delivery)
    rendered_headers = notification_fields_from_storage(delivery.rendered_headers_json)
    rendered_query_params = notification_fields_from_storage(
        delivery.rendered_query_params_json
    )
    return NotificationWebhookTestResponse(
        success=delivery.success,
        status_code=delivery.status_code,
        duration_ms=delivery.duration_ms,
        rendered_url=decrypt_notification_text(delivery.rendered_url) or "",
        rendered_method=delivery.rendered_method,
        rendered_headers=rendered_headers,
        rendered_query_params=rendered_query_params,
        rendered_body=decrypt_notification_text(delivery.rendered_body),
        response_body_preview=decrypt_notification_text(delivery.response_body_preview),
        error=notification_error_for_display(delivery.error),
    )


def delivery_has_presend_render_failure(delivery: NotificationWebhookDelivery) -> bool:
    return delivery.status_code is None and (delivery.error or "").startswith(
        RENDER_FAILURE_ERROR_PREFIX
    )


def is_retryable_notification_delivery(delivery: NotificationWebhookDelivery) -> bool:
    return is_retryable_notification_outcome(
        status_code=delivery.status_code, error=delivery.error
    )


def is_retryable_notification_result(result: NotificationWebhookTestResponse) -> bool:
    return is_retryable_notification_outcome(
        status_code=result.status_code, error=result.error
    )


def is_retryable_notification_outcome(
    *, status_code: int | None, error: str | None
) -> bool:
    if error and error.startswith(
        (RENDER_FAILURE_ERROR_PREFIX, POLICY_FAILURE_ERROR_PREFIX)
    ):
        return False
    if status_code is None:
        return True
    if status_code in {408, 425, 429}:
        return True
    return 500 <= int(status_code) <= 599


def notification_delivery_retry_root_id(
    delivery: NotificationWebhookDelivery,
) -> uuid.UUID:
    return delivery.source_delivery_id or delivery.id


def notification_delivery_chain_attempt_count(
    db: Session, *, retry_root_id: uuid.UUID
) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(
                or_(
                    NotificationWebhookDelivery.id == retry_root_id,
                    NotificationWebhookDelivery.source_delivery_id == retry_root_id,
                )
            )
        )
        or 0
    )


def notification_delivery_retry_delay_seconds(chain_attempt_count: int) -> int:
    base_delay = max(1, int(settings.notification_delivery_retry_backoff_seconds))
    retry_number = max(1, int(chain_attempt_count))
    return min(base_delay * (2 ** (retry_number - 1)), 3600)


def find_notification_webhook_retry_reuse_candidate(
    db: Session,
    *,
    webhook: NotificationWebhook,
    delivery: NotificationWebhookDelivery,
) -> NotificationWebhookDelivery | None:
    retry_root_id = notification_delivery_retry_root_id(delivery)
    return db.scalar(
        select(NotificationWebhookDelivery)
        .where(
            NotificationWebhookDelivery.webhook_id == webhook.id,
            NotificationWebhookDelivery.delivery_kind == "retry",
            NotificationWebhookDelivery.source_delivery_id == retry_root_id,
            NotificationWebhookDelivery.delivery_state.in_(
                [
                    NOTIFICATION_DELIVERY_PENDING,
                    NOTIFICATION_DELIVERY_SENDING,
                    NOTIFICATION_DELIVERY_SUCCEEDED,
                ]
            ),
        )
        .order_by(
            NotificationWebhookDelivery.attempted_at.desc(),
            NotificationWebhookDelivery.id.desc(),
        )
        .limit(1)
    )


def create_pending_notification_webhook_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    webhook: NotificationWebhook,
    event_type: NotificationEventType,
    rendered: RenderedNotificationRequest,
    delivery_kind: str,
    item_id: uuid.UUID | None,
    feed_id: uuid.UUID | None,
    item_title: str | None,
    feed_name: str | None,
    source_delivery_id: uuid.UUID | None,
    scope_key: str | None,
    attempted_at: datetime,
    not_before: datetime | None,
) -> NotificationWebhookDelivery:
    delivery = NotificationWebhookDelivery(
        id=delivery_id,
        webhook_id=webhook.id,
        user_id=webhook.user_id,
        event_type_snapshot=event_type,
        item_id=item_id,
        feed_id=feed_id,
        source_delivery_id=source_delivery_id,
        scope_key=scope_key,
        delivery_kind=delivery_kind,
        delivery_state=NOTIFICATION_DELIVERY_PENDING,
        attempt_count=0,
        not_before=not_before,
        claimed_at=None,
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=rendered.timeout_seconds,
        rendered_url=encrypt_notification_text(rendered.url) or "",
        rendered_method=rendered.method,
        rendered_headers_json=notification_fields_to_storage(rendered.headers),
        rendered_query_params_json=notification_fields_to_storage(
            rendered.query_params
        ),
        rendered_body=encrypt_notification_text(rendered.body),
        response_body_preview=None,
        error=None,
        item_title_snapshot=item_title,
        feed_name_snapshot=feed_name,
        attempted_at=attempted_at,
    )
    db.add(delivery)
    db.flush()
    ensure_webhook_delivery(db, webhook=webhook, legacy_delivery=delivery)
    return delivery


def create_pending_notification_webhook_delivery_from_render_failure(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    webhook: NotificationWebhook,
    event_type: NotificationEventType,
    timeout_seconds: int,
    rendered_url: str,
    rendered_method: str,
    rendered_headers_json: list[dict[str, str]],
    rendered_query_params_json: list[dict[str, str]],
    rendered_body: str | None,
    delivery_kind: str,
    item_id: uuid.UUID | None,
    feed_id: uuid.UUID | None,
    item_title: str | None,
    feed_name: str | None,
    source_delivery_id: uuid.UUID | None,
    scope_key: str | None,
    attempted_at: datetime,
    not_before: datetime | None,
    error: str,
) -> NotificationWebhookDelivery:
    delivery = NotificationWebhookDelivery(
        id=delivery_id,
        webhook_id=webhook.id,
        user_id=webhook.user_id,
        event_type_snapshot=event_type,
        item_id=item_id,
        feed_id=feed_id,
        source_delivery_id=source_delivery_id,
        scope_key=scope_key,
        delivery_kind=delivery_kind,
        delivery_state=NOTIFICATION_DELIVERY_PENDING,
        attempt_count=0,
        not_before=not_before,
        claimed_at=None,
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=timeout_seconds,
        rendered_url=encrypt_notification_text(rendered_url) or "",
        rendered_method=rendered_method,
        rendered_headers_json=encrypt_notification_json(rendered_headers_json),
        rendered_query_params_json=encrypt_notification_json(
            rendered_query_params_json
        ),
        rendered_body=encrypt_notification_text(rendered_body),
        response_body_preview=None,
        error=error,
        item_title_snapshot=item_title,
        feed_name_snapshot=feed_name,
        attempted_at=attempted_at,
    )
    db.add(delivery)
    db.flush()
    ensure_webhook_delivery(db, webhook=webhook, legacy_delivery=delivery)
    return delivery


def claim_notification_webhook_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    now: datetime | None = None,
) -> NotificationWebhookDelivery | None:
    webhook, delivery = _lock_notification_webhook_delivery(
        db,
        delivery_id=delivery_id,
    )
    if webhook is None or delivery is None:
        return None
    upgrade_notification_webhook_delivery_secret_storage(delivery)
    try:
        return claim_generic_webhook_delivery(
            db, webhook=webhook, legacy_delivery=delivery, now=now
        )
    except WebhookDeliveryBusyError:
        db.rollback()
        return None


def _lock_notification_webhook_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
) -> tuple[NotificationWebhook | None, NotificationWebhookDelivery | None]:
    webhook_id = db.scalar(
        select(NotificationWebhookDelivery.webhook_id)
        .where(NotificationWebhookDelivery.id == delivery_id)
        .execution_options(autoflush=False)
    )
    if webhook_id is None:
        return None, None
    webhook = lock_notification_webhook(
        db,
        webhook_id,
        refresh_existing=True,
    )
    if webhook is None:
        return None, None
    delivery = db.scalar(
        select(NotificationWebhookDelivery)
        .where(NotificationWebhookDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None or delivery.webhook_id != webhook.id:
        return webhook, None
    return webhook, delivery


def finalize_notification_webhook_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
    result: NotificationWebhookTestResponse,
    commit_outcome: bool,
) -> tuple[NotificationWebhookDelivery, bool]:
    _webhook, delivery = _lock_notification_webhook_delivery(
        db,
        delivery_id=delivery_id,
    )
    if delivery is None:
        raise ValueError("Webhook delivery not found")

    finished_at = datetime.now(timezone.utc)
    recorded = finalize_generic_webhook_delivery(
        db,
        legacy_delivery=delivery,
        success=result.success,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        error=result.error,
        retryable=not result.success and is_retryable_notification_result(result),
        expected_attempt_number=expected_attempt_number,
        finished_at=finished_at,
    )
    if not recorded:
        db.rollback()
        current = db.get(NotificationWebhookDelivery, delivery_id)
        if current is None:
            raise ValueError("Webhook delivery not found")
        return current, False

    delivery.delivery_state = (
        NOTIFICATION_DELIVERY_SUCCEEDED
        if result.success
        else NOTIFICATION_DELIVERY_FAILED
    )
    delivery.success = result.success
    delivery.status_code = result.status_code
    delivery.duration_ms = result.duration_ms
    delivery.response_body_preview = encrypt_notification_text(
        result.response_body_preview
    )
    delivery.error = result.error
    delivery.attempted_at = finished_at
    db.add(delivery)
    if commit_outcome:
        db.commit()
        db.refresh(delivery)
    else:
        db.flush()
    return delivery, True


def get_active_notification_webhook_user(
    db: Session,
    *,
    webhook: NotificationWebhook,
    user_cache: dict[uuid.UUID, User | None],
) -> User | None:
    if webhook.user_id not in user_cache:
        user_cache[webhook.user_id] = db.scalar(
            select(User).where(User.id == webhook.user_id)
        )
    user = user_cache[webhook.user_id]
    if user is None or not user.is_active or not user.is_approved:
        return None
    return user


def seconds_since(now: datetime, timestamp: datetime | None) -> int | None:
    if timestamp is None:
        return None
    value = (
        timestamp
        if timestamp.tzinfo is not None
        else timestamp.replace(tzinfo=timezone.utc)
    )
    return max(0, int((now - value).total_seconds()))
