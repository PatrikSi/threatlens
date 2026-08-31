from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import (
    NotificationEventType,
    NotificationTemplateVariable,
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services import notification_webhook_http
from app.services.authorization import AuthorizationContext
from app.services.daily_brief_notifications import (
    get_latest_daily_brief_notification_context,
)
from app.services.data_access_policy import DataAccessContext
from app.services.integration_compat import WebhookConfigurationCompatibilityError
from app.services.integration_delivery import (
    WebhookDeliveryIneligibleError,
    list_recoverable_webhook_delivery_ids as list_recoverable_notification_delivery_ids,  # noqa: F401 - compatibility re-export
)
from app.services.notification_webhook_compatibility import (
    WebhookExternalIOFenceError,
    defer_claimed_notification_webhook_for_compatibility,
    defer_claimed_notification_webhook_for_preflight_error,
    defer_claimed_notification_webhook_for_temporary_policy,
    finalize_claimed_notification_webhook_for_policy_error,
    lock_notification_webhook_external_io_eligibility as _lock_notification_webhook_external_io_eligibility,
    mark_notification_webhook_external_io_started,
)
from app.services.webhook_delivery_eligibility import (
    WebhookDeliveryTemporarilyIneligibleError,
)
from app.services.notification_webhook_contexts import (  # noqa: F401 - compatibility re-exports
    FEED_FAILING_NOTIFICATION_THRESHOLD,
    build_alert_match_context_for_item,
    build_failed_webhook_retry_context as _build_failed_webhook_retry_context,
    build_item_haystack as _build_item_haystack,
    build_sample_feed_for_event as _build_sample_feed_for_event,
    daily_brief_context_for_webhook_delivery as _daily_brief_context_for_webhook_delivery,
    feed_ids_for_webhook_payload as _feed_ids_for_webhook_payload,
    resolve_sample_feed_and_item as _resolve_sample_feed_and_item,
)
from app.services.notification_webhook_history import (  # noqa: F401 - compatibility re-exports
    NOTIFICATION_DELIVERY_FAILED,
    NOTIFICATION_DELIVERY_PENDING,
    NOTIFICATION_DELIVERY_QUEUE_DEGRADED_AFTER,
    NOTIFICATION_DELIVERY_RECOVERY_BATCH_SIZE,
    NOTIFICATION_DELIVERY_SENDING,
    NOTIFICATION_DELIVERY_STALE_AFTER,
    NOTIFICATION_DELIVERY_SUCCEEDED,
    NOTIFICATION_DELIVERY_TERMINAL_STATES,
    NotificationDeliveryReservationBatch,
    NotificationDeliveryWouldDenySummary,
    NotificationWebhookDeliveryAttempt,
    NotificationWebhookRetryReservation,
    claim_notification_webhook_delivery as _claim_notification_webhook_delivery,
    create_pending_notification_webhook_delivery as _create_pending_notification_webhook_delivery,
    create_pending_notification_webhook_delivery_from_render_failure as _create_pending_notification_webhook_delivery_from_render_failure,
    delivery_has_presend_render_failure as _delivery_has_presend_render_failure,
    delivery_result_from_model as _delivery_result_from_model,
    finalize_notification_webhook_delivery as _finalize_notification_webhook_delivery,
    find_notification_webhook_retry_reuse_candidate as _find_notification_webhook_retry_reuse_candidate,
    get_active_notification_webhook_user as _get_active_notification_webhook_user,
    get_notification_analytics,
    get_notification_delivery_queue_snapshot,
    has_recent_notification_delivery,
    is_retryable_notification_delivery as _is_retryable_notification_delivery,
    is_retryable_notification_outcome as _is_retryable_notification_outcome,
    is_retryable_notification_result as _is_retryable_notification_result,
    notification_delivery_chain_attempt_count as _notification_delivery_chain_attempt_count,
    notification_delivery_data_access_predicate,
    notification_delivery_would_deny_summary,
    notification_delivery_retry_delay_seconds as _notification_delivery_retry_delay_seconds,
    notification_delivery_retry_root_id as _notification_delivery_retry_root_id,
    seconds_since as _seconds_since,
    try_acquire_notification_delivery_lock,
)
from app.services.notification_webhook_requests import (  # noqa: F401 - compatibility re-exports
    THREATLENS_SOURCE_DELIVERY_ID_HEADER,
    RenderedNotificationRequest,
    apply_notification_delivery_headers as _apply_notification_delivery_headers,
    render_notification_request,
    rendered_request_from_delivery as _rendered_request_from_delivery,
    restore_saved_request_target as _restore_saved_request_target,
)
from app.services.notification_webhook_test_policy import (
    NotificationWebhookTestPolicyError,
    NotificationWebhookTestPolicyUnavailable,
    NotificationWebhookTestReplayConflict,
    NotificationWebhookTestReplayUnsafe,
    NotificationWebhookTestSourceRefs,
    authorize_notification_webhook_test,
    lock_notification_webhook_test_receipt_for_outcome,
    notification_webhook_test_request_digests,
    policy_error_from_snapshot,
    record_notification_webhook_test_outcome,
    record_notification_webhook_test_policy_decision,
    require_matching_notification_webhook_test_snapshot,
    reserve_notification_webhook_test_receipt,
    unavailable_notification_webhook_test_snapshot,
)
from app.services.notification_webhook_templates import (
    TEMPLATE_PATTERN,
    TEMPLATE_VARIABLES,
    AlertMatchContext,
    DailyDigestContext,
    FailedWebhookContext,
    TemplateRenderError,
    find_unknown_template_variables_in_texts,
    isoformat as _isoformat,
    list_template_variables,
    render_notification_template_text,
)
from app.services.notification_webhook_storage import (
    POLICY_FAILURE_ERROR_PREFIX,
    RENDER_FAILURE_ERROR_PREFIX,
    apply_notification_webhook_updates,
    build_notification_webhook,
    decrypt_notification_json as _decrypt_notification_json,
    notification_error_for_display as _notification_error_for_display,
    notification_feed_ids_from_storage as _notification_feed_ids_from_storage,
    notification_webhook_delivery_response_from_model,
    notification_webhook_response_from_model,
    notification_webhook_write_from_model,
    redact_delivery_body_preview as _redact_delivery_body_preview,
    redact_notification_field_values as _redact_notification_field_values,
    redact_notification_query_params as _redact_notification_query_params,
    redact_notification_test_response as _redact_notification_test_response,
)
from app.services.notification_webhook_validation import (
    validate_notification_actor_for_delivery,
    validate_notification_delivery_target_for_actor,
    validate_notification_target_url as _validate_notification_target_url,
    validate_notification_webhook_payload,  # noqa: F401 - compatibility re-export
    validate_notification_webhook_payload_for_actor,  # noqa: F401 - compatibility re-export
)

logger = logging.getLogger(__name__)
settings = get_settings()
FEED_FAILING_NOTIFICATION_COOLDOWN_HOURS = 12

__all__ = [
    "NotificationTemplateVariable",
    "TEMPLATE_PATTERN",
    "TEMPLATE_VARIABLES",
    "_decrypt_notification_json",
    "_isoformat",
    "_redact_delivery_body_preview",
    "_redact_notification_field_values",
    "_redact_notification_query_params",
    "apply_notification_webhook_updates",
    "build_notification_webhook",
    "find_unknown_template_variables_in_texts",
    "list_template_variables",
    "_notification_feed_ids_from_storage",
    "notification_webhook_delivery_response_from_model",
    "notification_webhook_response_from_model",
    "render_notification_template_text",
]


class NotificationWebhookRetryInProgressError(RuntimeError):
    pass


def test_notification_webhook(
    db: Session,
    *,
    user: User,
    payload: NotificationWebhookWrite,
    sample_item_id: uuid.UUID | None = None,
    sample_feed_id: uuid.UUID | None = None,
    data_access: DataAccessContext | None = None,
    authorization: AuthorizationContext | None = None,
    operation_id: str | None = None,
) -> NotificationWebhookTestResponse:
    feed, item = _resolve_sample_feed_and_item(
        db,
        payload=payload,
        sample_item_id=sample_item_id,
        sample_feed_id=sample_feed_id,
        data_access=data_access,
    )
    sample_feed = _build_sample_feed_for_event(feed, payload.event_type)
    alert_context = None
    failed_webhook_context = None
    digest_context = None
    daily_brief_id: uuid.UUID | None = None

    if payload.event_type == "alert_match":
        alert_context = build_alert_match_context_for_item(
            db, user_id=user.id, item=item
        )
        if alert_context is None:
            alert_context = AlertMatchContext(
                count=1,
                primary_name="Threat activity",
                names=["Threat activity"],
                categories=["monitoring"],
                matched_keywords=["credential theft"],
            )
    elif payload.event_type == "webhook_failed":
        failed_webhook_context = FailedWebhookContext(
            id=uuid.uuid4(),
            name="Example monitored webhook",
            event_type="rss_item_new",
            status_code=500,
            error="HTTP 500",
            attempted_at=datetime.now(timezone.utc),
        )
    elif payload.event_type in {"daily_digest", "report_ready"}:
        digest_context = get_latest_daily_brief_notification_context(
            db,
            data_access=data_access,
        )
        if digest_context is not None:
            daily_brief_id = digest_context.brief_id
        if digest_context is None:
            now = datetime.now(timezone.utc)
            digest_context = DailyDigestContext(
                window_start=now - timedelta(hours=24),
                window_end=now,
                total_items=7,
                total_feeds=2,
                feed_names=["Example Feed", "CISA"],
                top_titles=["ThreatLens sample brief item", "Second sample brief item"],
                brief_id=uuid.uuid4(),
                brief_date=now.date().isoformat(),
                generated_at=now,
                title="Example AI Daily Brief",
                brief_text="The latest intelligence highlights identity threats and exposed edge services.",
                key_points=[
                    "Prioritize identity telemetry",
                    "Review exposed edge services",
                ],
                recommended_actions=[
                    "Validate MFA coverage",
                    "Confirm edge patch status",
                ],
            )

    rendered = render_notification_request(
        payload,
        user=user,
        feed=sample_feed,
        item=item,
        event_type=payload.event_type,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
    )
    try:
        validate_notification_actor_for_delivery(user)
        _validate_notification_target_url(rendered.url)
    except ValueError as exc:
        return _redact_notification_test_response(
            NotificationWebhookTestResponse(
                success=False,
                status_code=None,
                duration_ms=0,
                rendered_url=rendered.url,
                rendered_method=rendered.method,
                rendered_headers=rendered.headers,
                rendered_query_params=rendered.query_params,
                rendered_body=rendered.body,
                response_body_preview=None,
                error=str(exc),
            )
        )
    if (
        data_access is not None
        and authorization is not None
        and operation_id is not None
    ):
        return _execute_fenced_notification_webhook_test(
            db,
            user=user,
            rendered=rendered,
            logical_request={
                "webhook": payload.model_dump(mode="json"),
                "sample_item_id": (
                    str(sample_item_id) if sample_item_id is not None else None
                ),
                "sample_feed_id": (
                    str(sample_feed_id) if sample_feed_id is not None else None
                ),
            },
            source_refs=NotificationWebhookTestSourceRefs(
                feed_id=feed.id if isinstance(feed, Feed) else None,
                item_id=item.id if isinstance(item, Item) else None,
                daily_brief_id=daily_brief_id,
            ),
            data_access=data_access,
            authorization=authorization,
            operation_id=operation_id,
        )

    result = notification_webhook_http.send_rendered_notification_request(rendered)
    return _redact_notification_test_response(result)


def _execute_fenced_notification_webhook_test(
    db: Session,
    *,
    user: User,
    rendered: RenderedNotificationRequest,
    logical_request: object,
    source_refs: NotificationWebhookTestSourceRefs,
    data_access: DataAccessContext,
    authorization: AuthorizationContext,
    operation_id: str,
) -> NotificationWebhookTestResponse:
    phase_one_error: NotificationWebhookTestPolicyError | None = None
    try:
        snapshot = authorize_notification_webhook_test(
            db,
            user=user,
            authorization=authorization,
            data_access=data_access,
            source_refs=source_refs,
        )
    except NotificationWebhookTestPolicyUnavailable as exc:
        phase_one_error = exc
        snapshot = unavailable_notification_webhook_test_snapshot(
            authorization=authorization,
            data_access=data_access,
            source_refs=source_refs,
        )

    destination_digest, request_fingerprint = (
        notification_webhook_test_request_digests(
            rendered,
            snapshot=snapshot,
            logical_request=logical_request,
        )
    )
    reservation = reserve_notification_webhook_test_receipt(
        db,
        operation_id=operation_id,
        user=user,
        snapshot=snapshot,
        destination_digest=destination_digest,
        request_fingerprint=request_fingerprint,
    )
    if reservation.replay_response is not None:
        db.commit()
        return reservation.replay_response

    record_notification_webhook_test_policy_decision(
        db,
        context=data_access,
        snapshot=snapshot,
        receipt_id=reservation.receipt_id,
        destination_digest=destination_digest,
    )
    policy_error = phase_one_error or policy_error_from_snapshot(snapshot)
    if policy_error is not None:
        record_notification_webhook_test_outcome(
            db,
            receipt_id=reservation.receipt_id,
            operation_id=operation_id,
            user=user,
            snapshot=snapshot,
            destination_digest=destination_digest,
            request_fingerprint=request_fingerprint,
            io_outcome="not_sent",
            state=(
                "denied"
                if snapshot.decision == "egress_denied"
                else "unavailable"
            ),
            error_code=policy_error.code,
        )
        db.commit()
        raise policy_error

    # The receipt and any audit-mode would-deny evidence must survive a crash
    # during the network request. This commit intentionally ends phase one.
    db.commit()

    try:
        lock_notification_webhook_test_receipt_for_outcome(
            db,
            receipt_id=reservation.receipt_id,
            request_fingerprint=request_fingerprint,
        )
        final_snapshot = authorize_notification_webhook_test(
            db,
            user=user,
            authorization=authorization,
            data_access=data_access,
            source_refs=source_refs,
        )
        require_matching_notification_webhook_test_snapshot(snapshot, final_snapshot)
        final_policy_error = policy_error_from_snapshot(final_snapshot)
        if final_policy_error is not None:
            raise final_policy_error
        validate_notification_actor_for_delivery(user)
        _validate_notification_target_url(rendered.url)
    except NotificationWebhookTestReplayConflict:
        db.rollback()
        raise
    except (NotificationWebhookTestPolicyError, ValueError) as exc:
        error = (
            exc
            if isinstance(exc, NotificationWebhookTestPolicyError)
            else NotificationWebhookTestPolicyUnavailable(
                "Webhook test destination changed before outbound delivery. Retry the request."
            )
        )
        try:
            record_notification_webhook_test_outcome(
                db,
                receipt_id=reservation.receipt_id,
                operation_id=operation_id,
                user=user,
                snapshot=snapshot,
                destination_digest=destination_digest,
                request_fingerprint=request_fingerprint,
                io_outcome="not_sent",
                state="unavailable",
                error_code=error.code,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise NotificationWebhookTestReplayUnsafe(
                "Webhook test outcome could not be durably settled. Do not replay it with the same request ID."
            ) from exc
        raise error from exc

    io_started = False

    def _mark_io_started() -> None:
        nonlocal io_started
        io_started = True

    try:
        with notification_webhook_http.notification_delivery_external_io_marker(
            _mark_io_started
        ):
            raw_result = notification_webhook_http.send_rendered_notification_request(
                rendered
            )
        result = _redact_notification_test_response(raw_result)
        if io_started and result.status_code is None:
            raise NotificationWebhookTestReplayUnsafe(
                "Webhook test outcome is ambiguous. Do not replay it with the same request ID."
            )
    except Exception as exc:
        io_outcome = "ambiguous" if io_started else "not_sent"
        state = "ambiguous" if io_started else "unavailable"
        try:
            record_notification_webhook_test_outcome(
                db,
                receipt_id=reservation.receipt_id,
                operation_id=operation_id,
                user=user,
                snapshot=snapshot,
                destination_digest=destination_digest,
                request_fingerprint=request_fingerprint,
                io_outcome=io_outcome,
                state=state,
                error_code=(
                    NotificationWebhookTestReplayUnsafe.code
                    if io_started
                    else NotificationWebhookTestPolicyUnavailable.code
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise NotificationWebhookTestReplayUnsafe(
                "Webhook test outcome could not be durably settled. Do not replay it with the same request ID."
            ) from exc
        if io_started:
            raise NotificationWebhookTestReplayUnsafe(
                "Webhook test may have reached its destination. Do not replay it with the same request ID."
            ) from exc
        raise

    io_outcome = (
        "response_received" if result.status_code is not None else "not_sent"
    )
    try:
        record_notification_webhook_test_outcome(
            db,
            receipt_id=reservation.receipt_id,
            operation_id=operation_id,
            user=user,
            snapshot=snapshot,
            destination_digest=destination_digest,
            request_fingerprint=request_fingerprint,
            io_outcome=io_outcome,
            state="settled",
            response=result,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise NotificationWebhookTestReplayUnsafe(
            "Webhook test outcome could not be durably settled. Do not replay it with the same request ID."
        ) from exc
    return result


test_notification_webhook.__test__ = False


def get_matching_notification_webhooks_for_feed(
    db: Session, *, feed_id: uuid.UUID
) -> list[NotificationWebhook]:
    return get_matching_notification_webhooks(
        db, event_type="rss_item_new", feed_id=feed_id
    )


def get_matching_notification_webhooks(
    db: Session,
    *,
    event_type: NotificationEventType,
    feed_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> list[NotificationWebhook]:
    query = select(NotificationWebhook).where(
        NotificationWebhook.enabled.is_(True),
        NotificationWebhook.event_type == event_type,
    )
    if user_id is not None:
        query = query.where(NotificationWebhook.user_id == user_id)

    enabled_webhooks = db.scalars(
        query.order_by(NotificationWebhook.created_at.asc())
    ).all()
    if feed_id is None:
        return [webhook for webhook in enabled_webhooks if webhook.feed_scope == "all"]
    return [
        webhook
        for webhook in enabled_webhooks
        if webhook.feed_scope == "all" or str(feed_id) in (webhook.feed_ids_json or [])
    ]


def reserve_new_item_notification_deliveries(
    db: Session,
    *,
    item: Item,
    feed: Feed,
    webhooks: list[NotificationWebhook] | None = None,
    user_cache: dict[uuid.UUID, User | None] | None = None,
) -> NotificationDeliveryReservationBatch:
    matched_webhooks = (
        webhooks
        if webhooks is not None
        else get_matching_notification_webhooks_for_feed(db, feed_id=feed.id)
    )
    resolved_user_cache = user_cache if user_cache is not None else {}
    reserved_delivery_ids: list[uuid.UUID] = []
    skipped = 0

    for webhook in matched_webhooks:
        user = _get_active_notification_webhook_user(
            db, webhook=webhook, user_cache=resolved_user_cache
        )
        if user is None:
            skipped += 1
            continue

        if not try_acquire_notification_delivery_lock(
            db,
            webhook_id=webhook.id,
            event_type="rss_item_new",
            item_id=item.id,
        ):
            skipped += 1
            continue

        if has_recent_notification_delivery(
            db,
            webhook_id=webhook.id,
            event_type="rss_item_new",
            item_id=item.id,
        ):
            skipped += 1
            continue

        delivery = reserve_notification_webhook_delivery(
            db,
            webhook=webhook,
            user=user,
            event_type="rss_item_new",
            item=item,
            feed=feed,
        )
        reserved_delivery_ids.append(delivery.id)

    return NotificationDeliveryReservationBatch(
        delivery_ids=reserved_delivery_ids,
        matched_webhooks=len(matched_webhooks),
        skipped=skipped,
    )


def reserve_alert_match_notification_deliveries(
    db: Session,
    *,
    item: Item,
    feed: Feed,
    webhooks: list[NotificationWebhook] | None = None,
    user_cache: dict[uuid.UUID, User | None] | None = None,
) -> NotificationDeliveryReservationBatch:
    matched_webhooks = (
        webhooks
        if webhooks is not None
        else get_matching_notification_webhooks(
            db, event_type="alert_match", feed_id=feed.id
        )
    )
    resolved_user_cache = user_cache if user_cache is not None else {}
    reserved_delivery_ids: list[uuid.UUID] = []
    skipped = 0
    cached_contexts: dict[uuid.UUID, AlertMatchContext | None] = {}

    for webhook in matched_webhooks:
        user = _get_active_notification_webhook_user(
            db, webhook=webhook, user_cache=resolved_user_cache
        )
        if user is None:
            skipped += 1
            continue

        if webhook.user_id not in cached_contexts:
            cached_contexts[webhook.user_id] = build_alert_match_context_for_item(
                db, user_id=webhook.user_id, item=item
            )

        alert_context = cached_contexts[webhook.user_id]
        if alert_context is None:
            skipped += 1
            continue

        if not try_acquire_notification_delivery_lock(
            db,
            webhook_id=webhook.id,
            event_type="alert_match",
            item_id=item.id,
        ):
            skipped += 1
            continue

        if has_recent_notification_delivery(
            db,
            webhook_id=webhook.id,
            event_type="alert_match",
            item_id=item.id,
        ):
            skipped += 1
            continue

        delivery = reserve_notification_webhook_delivery(
            db,
            webhook=webhook,
            user=user,
            event_type="alert_match",
            item=item,
            feed=feed,
            alert_context=alert_context,
        )
        reserved_delivery_ids.append(delivery.id)

    return NotificationDeliveryReservationBatch(
        delivery_ids=reserved_delivery_ids,
        matched_webhooks=len(matched_webhooks),
        skipped=skipped,
    )


def reserve_feed_failing_notification_deliveries(
    db: Session,
    *,
    feed: Feed,
    webhooks: list[NotificationWebhook] | None = None,
    user_cache: dict[uuid.UUID, User | None] | None = None,
    now: datetime | None = None,
) -> NotificationDeliveryReservationBatch:
    if int(feed.error_count or 0) < FEED_FAILING_NOTIFICATION_THRESHOLD:
        return NotificationDeliveryReservationBatch(
            delivery_ids=[], matched_webhooks=0, skipped=0
        )

    matched_webhooks = (
        webhooks
        if webhooks is not None
        else get_matching_notification_webhooks(
            db, event_type="feed_failing", feed_id=feed.id
        )
    )
    resolved_user_cache = user_cache if user_cache is not None else {}
    reserved_delivery_ids: list[uuid.UUID] = []
    skipped = 0
    cooldown_start = (now or datetime.now(timezone.utc)) - timedelta(
        hours=FEED_FAILING_NOTIFICATION_COOLDOWN_HOURS
    )

    for webhook in matched_webhooks:
        user = _get_active_notification_webhook_user(
            db, webhook=webhook, user_cache=resolved_user_cache
        )
        if user is None:
            skipped += 1
            continue

        if not try_acquire_notification_delivery_lock(
            db,
            webhook_id=webhook.id,
            event_type="feed_failing",
            feed_id=feed.id,
        ):
            skipped += 1
            continue

        if has_recent_notification_delivery(
            db,
            webhook_id=webhook.id,
            event_type="feed_failing",
            feed_id=feed.id,
            since=cooldown_start,
        ):
            skipped += 1
            continue

        delivery = reserve_notification_webhook_delivery(
            db,
            webhook=webhook,
            user=user,
            event_type="feed_failing",
            feed=feed,
            item=None,
            feed_name=feed.name,
        )
        reserved_delivery_ids.append(delivery.id)

    return NotificationDeliveryReservationBatch(
        delivery_ids=reserved_delivery_ids,
        matched_webhooks=len(matched_webhooks),
        skipped=skipped,
    )


def reserve_webhook_failed_notification_deliveries(
    db: Session,
    *,
    failed_delivery: NotificationWebhookDelivery,
    source_webhook: NotificationWebhook | None = None,
    user: User | None = None,
    feed: Feed | None = None,
) -> NotificationDeliveryReservationBatch:
    if (
        failed_delivery.success
        or failed_delivery.event_type_snapshot == "webhook_failed"
    ):
        return NotificationDeliveryReservationBatch(
            delivery_ids=[], matched_webhooks=0, skipped=0
        )

    resolved_source_webhook = source_webhook or db.scalar(
        select(NotificationWebhook).where(
            NotificationWebhook.id == failed_delivery.webhook_id
        )
    )
    if resolved_source_webhook is None:
        return NotificationDeliveryReservationBatch(
            delivery_ids=[], matched_webhooks=0, skipped=0
        )

    resolved_user = user or db.scalar(
        select(User).where(User.id == failed_delivery.user_id)
    )
    if (
        resolved_user is None
        or not resolved_user.is_active
        or not resolved_user.is_approved
    ):
        return NotificationDeliveryReservationBatch(
            delivery_ids=[], matched_webhooks=0, skipped=0
        )

    resolved_feed = feed
    if resolved_feed is None and failed_delivery.feed_id is not None:
        resolved_feed = db.scalar(
            select(Feed).where(Feed.id == failed_delivery.feed_id)
        )

    failed_context = FailedWebhookContext(
        id=resolved_source_webhook.id,
        name=resolved_source_webhook.name,
        event_type=failed_delivery.event_type_snapshot,
        status_code=failed_delivery.status_code,
        error=_notification_error_for_display(failed_delivery.error),
        attempted_at=failed_delivery.attempted_at,
    )

    matched_webhooks = get_matching_notification_webhooks(
        db,
        event_type="webhook_failed",
        feed_id=failed_delivery.feed_id,
        user_id=failed_delivery.user_id,
    )
    reserved_delivery_ids: list[uuid.UUID] = []
    skipped = 0

    for webhook in matched_webhooks:
        if webhook.id == failed_delivery.webhook_id:
            skipped += 1
            continue

        if not try_acquire_notification_delivery_lock(
            db,
            webhook_id=webhook.id,
            event_type="webhook_failed",
            source_delivery_id=failed_delivery.id,
        ):
            skipped += 1
            continue

        if has_recent_notification_delivery(
            db,
            webhook_id=webhook.id,
            event_type="webhook_failed",
            source_delivery_id=failed_delivery.id,
        ):
            skipped += 1
            continue

        delivery = reserve_notification_webhook_delivery(
            db,
            webhook=webhook,
            user=resolved_user,
            event_type="webhook_failed",
            feed=resolved_feed,
            item=None,
            failed_webhook_context=failed_context,
            feed_name=getattr(resolved_feed, "name", None),
            source_delivery_id=failed_delivery.id,
        )
        reserved_delivery_ids.append(delivery.id)

    return NotificationDeliveryReservationBatch(
        delivery_ids=reserved_delivery_ids,
        matched_webhooks=len(matched_webhooks),
        skipped=skipped,
    )


def reserve_notification_webhook_delivery(
    db: Session,
    *,
    webhook: NotificationWebhook,
    user: User,
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None = None,
    item: Item | SimpleNamespace | None = None,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
    delivery_kind: str = "live",
    item_title: str | None = None,
    feed_name: str | None = None,
    source_delivery_id: uuid.UUID | None = None,
    scope_key: str | None = None,
    not_before: datetime | None = None,
) -> NotificationWebhookDelivery:
    payload = notification_webhook_write_from_model(webhook)
    delivery_id = uuid.uuid4()
    queued_at = datetime.now(timezone.utc)

    try:
        rendered = render_notification_request(
            payload,
            user=user,
            feed=feed,
            item=item,
            event_type=event_type,
            triggered_at=queued_at,
            delivery_id=delivery_id,
            source_delivery_id=source_delivery_id,
            alert_context=alert_context,
            failed_webhook_context=failed_webhook_context,
            digest_context=digest_context,
        )
    except (TemplateRenderError, ValueError) as exc:
        return _create_pending_notification_webhook_delivery_from_render_failure(
            db,
            delivery_id=delivery_id,
            webhook=webhook,
            event_type=event_type,
            timeout_seconds=payload.timeout_seconds,
            rendered_url=payload.url_template,
            rendered_method=payload.method,
            rendered_headers_json=[field.model_dump() for field in payload.headers],
            rendered_query_params_json=[
                field.model_dump() for field in payload.query_params
            ],
            rendered_body=payload.body_template if payload.body_mode == "raw" else None,
            delivery_kind=delivery_kind,
            item_id=getattr(item, "id", None),
            feed_id=getattr(feed, "id", None),
            item_title=item_title
            if item_title is not None
            else getattr(item, "title", None),
            feed_name=feed_name
            if feed_name is not None
            else getattr(feed, "name", None),
            source_delivery_id=source_delivery_id,
            scope_key=scope_key,
            attempted_at=queued_at,
            not_before=not_before,
            error=f"{RENDER_FAILURE_ERROR_PREFIX}{exc}",
        )

    return _create_pending_notification_webhook_delivery(
        db,
        delivery_id=delivery_id,
        webhook=webhook,
        event_type=event_type,
        rendered=rendered,
        delivery_kind=delivery_kind,
        item_id=getattr(item, "id", None),
        feed_id=getattr(feed, "id", None),
        item_title=item_title
        if item_title is not None
        else getattr(item, "title", None),
        feed_name=feed_name if feed_name is not None else getattr(feed, "name", None),
        source_delivery_id=source_delivery_id,
        scope_key=scope_key,
        attempted_at=queued_at,
        not_before=not_before,
    )


def reserve_notification_webhook_delivery_from_saved_request(
    db: Session,
    *,
    webhook: NotificationWebhook,
    delivery: NotificationWebhookDelivery,
    not_before: datetime | None = None,
) -> NotificationWebhookDelivery:
    rerendered = _reserve_notification_webhook_delivery_from_current_context(
        db,
        webhook=webhook,
        delivery=delivery,
        not_before=not_before,
    )
    if rerendered is not None:
        return rerendered
    return _create_pending_notification_webhook_delivery(
        db,
        delivery_id=uuid.uuid4(),
        webhook=webhook,
        event_type=delivery.event_type_snapshot,
        rendered=_rendered_request_from_delivery(delivery),
        delivery_kind="retry",
        item_id=delivery.item_id,
        feed_id=delivery.feed_id,
        item_title=delivery.item_title_snapshot,
        feed_name=delivery.feed_name_snapshot,
        source_delivery_id=delivery.source_delivery_id or delivery.id,
        scope_key=delivery.scope_key,
        attempted_at=datetime.now(timezone.utc),
        not_before=not_before,
    )


def _reserve_notification_webhook_delivery_from_current_context(
    db: Session,
    *,
    webhook: NotificationWebhook,
    delivery: NotificationWebhookDelivery,
    not_before: datetime | None = None,
) -> NotificationWebhookDelivery | None:
    user = db.scalar(select(User).where(User.id == webhook.user_id))
    if user is None or not user.is_active or not user.is_approved:
        return None

    event_type = delivery.event_type_snapshot
    item = (
        db.scalar(select(Item).where(Item.id == delivery.item_id))
        if delivery.item_id is not None
        else None
    )
    feed = (
        db.scalar(select(Feed).where(Feed.id == delivery.feed_id))
        if delivery.feed_id is not None
        else None
    )
    if feed is None and item is not None:
        feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))

    alert_context = None
    failed_webhook_context = None
    digest_context = None

    if event_type == "rss_item_new" and (item is None or feed is None):
        return None
    if event_type == "alert_match":
        if item is None or feed is None:
            return None
        alert_context = build_alert_match_context_for_item(
            db, user_id=webhook.user_id, item=item
        )
        if alert_context is None:
            return None
    if event_type == "feed_failing" and feed is None:
        return None
    if event_type == "webhook_failed":
        failed_webhook_context = _build_failed_webhook_retry_context(
            db, delivery=delivery
        )
        if failed_webhook_context is None:
            return None
    if event_type in {"daily_digest", "report_ready"}:
        digest_context = _daily_brief_context_for_webhook_delivery(
            db, delivery=delivery
        )
        if digest_context is None:
            return None

    return reserve_notification_webhook_delivery(
        db,
        webhook=webhook,
        user=user,
        event_type=event_type,
        feed=feed,
        item=item,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
        delivery_kind="retry",
        item_title=delivery.item_title_snapshot,
        feed_name=delivery.feed_name_snapshot,
        source_delivery_id=delivery.source_delivery_id or delivery.id,
        scope_key=delivery.scope_key,
        not_before=not_before,
    )


def process_notification_webhook_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    commit_outcome: bool = True,
) -> NotificationWebhookDeliveryAttempt:
    delivery = _claim_notification_webhook_delivery(db, delivery_id=delivery_id)
    if delivery is None:
        current = db.scalar(
            select(NotificationWebhookDelivery).where(
                NotificationWebhookDelivery.id == delivery_id
            )
        )
        if current is None:
            raise ValueError("Webhook delivery not found")
        return NotificationWebhookDeliveryAttempt(
            result=_delivery_result_from_model(current),
            delivery=current,
            claimed=False,
        )

    claimed_attempt_number = max(1, int(delivery.attempt_count or 0))
    actor_user = db.scalar(select(User).where(User.id == delivery.user_id))
    try:
        validate_notification_delivery_target_for_actor(
            delivery,
            actor_user=actor_user,
            require_active=delivery.event_type_snapshot != "report_ready",
        )
        generic_delivery_id = _lock_notification_webhook_external_io_eligibility(
            db,
            delivery=delivery,
            expected_attempt_number=claimed_attempt_number,
        )
    except WebhookConfigurationCompatibilityError as exc:
        return defer_claimed_notification_webhook_for_compatibility(
            db,
            delivery=delivery,
            expected_attempt_number=claimed_attempt_number,
            error=exc,
            commit_outcome=commit_outcome,
        )
    except WebhookDeliveryTemporarilyIneligibleError as exc:
        return defer_claimed_notification_webhook_for_temporary_policy(
            db,
            delivery=delivery,
            expected_attempt_number=claimed_attempt_number,
            error=exc,
            commit_outcome=commit_outcome,
        )
    except (ValueError, WebhookDeliveryIneligibleError) as exc:
        return _finalize_notification_webhook_policy_failure(
            db,
            delivery=delivery,
            expected_attempt_number=claimed_attempt_number,
            error=exc,
            commit_outcome=commit_outcome,
        )
    if _delivery_has_presend_render_failure(delivery):
        current_result = _delivery_result_from_model(delivery)
        result = NotificationWebhookTestResponse(
            success=False,
            status_code=None,
            duration_ms=current_result.duration_ms,
            rendered_url=current_result.rendered_url,
            rendered_method=current_result.rendered_method,
            rendered_headers=current_result.rendered_headers,
            rendered_query_params=current_result.rendered_query_params,
            rendered_body=current_result.rendered_body,
            response_body_preview=current_result.response_body_preview,
            error=delivery.error,
        )
        finalized, recorded = _finalize_notification_webhook_delivery(
            db,
            delivery_id=delivery.id,
            expected_attempt_number=claimed_attempt_number,
            result=result,
            commit_outcome=commit_outcome,
        )
        return NotificationWebhookDeliveryAttempt(
            result=_delivery_result_from_model(finalized),
            delivery=finalized,
            claimed=recorded,
        )
    rendered = _rendered_request_from_delivery(delivery)
    try:
        with notification_webhook_http.notification_delivery_external_io_marker(
            lambda: mark_notification_webhook_external_io_started(
                delivery_id=generic_delivery_id,
                expected_attempt_number=claimed_attempt_number,
            )
        ):
            result = notification_webhook_http.send_rendered_notification_request(
                rendered
            )
    except WebhookExternalIOFenceError as exc:
        return defer_claimed_notification_webhook_for_preflight_error(
            db,
            delivery=delivery,
            expected_attempt_number=claimed_attempt_number,
            error=exc,
            commit_outcome=commit_outcome,
        )
    except WebhookConfigurationCompatibilityError as exc:
        return defer_claimed_notification_webhook_for_compatibility(
            db,
            delivery=delivery,
            expected_attempt_number=claimed_attempt_number,
            error=exc,
            commit_outcome=commit_outcome,
        )
    except WebhookDeliveryTemporarilyIneligibleError as exc:
        return defer_claimed_notification_webhook_for_temporary_policy(
            db,
            delivery=delivery,
            expected_attempt_number=claimed_attempt_number,
            error=exc,
            commit_outcome=commit_outcome,
        )
    except (
        notification_webhook_http.RedirectError,
        notification_webhook_http.WebhookAmbiguousResponseError,
        WebhookDeliveryIneligibleError,
    ) as exc:
        return finalize_claimed_notification_webhook_for_policy_error(
            db,
            delivery=delivery,
            expected_attempt_number=claimed_attempt_number,
            error=exc,
            commit_outcome=commit_outcome,
        )
    finalized, recorded = _finalize_notification_webhook_delivery(
        db,
        delivery_id=delivery.id,
        expected_attempt_number=claimed_attempt_number,
        result=result,
        commit_outcome=commit_outcome,
    )
    return NotificationWebhookDeliveryAttempt(
        result=result if recorded else _delivery_result_from_model(finalized),
        delivery=finalized,
        claimed=recorded,
    )


def _finalize_notification_webhook_policy_failure(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    expected_attempt_number: int,
    error: Exception,
    commit_outcome: bool,
) -> NotificationWebhookDeliveryAttempt:
    current_result = _delivery_result_from_model(delivery)
    result = NotificationWebhookTestResponse(
        success=False,
        status_code=None,
        duration_ms=0,
        rendered_url=current_result.rendered_url,
        rendered_method=current_result.rendered_method,
        rendered_headers=current_result.rendered_headers,
        rendered_query_params=current_result.rendered_query_params,
        rendered_body=current_result.rendered_body,
        response_body_preview=None,
        error=f"{POLICY_FAILURE_ERROR_PREFIX}{error}",
    )
    finalized, recorded = _finalize_notification_webhook_delivery(
        db,
        delivery_id=delivery.id,
        expected_attempt_number=expected_attempt_number,
        result=result,
        commit_outcome=commit_outcome,
    )
    return NotificationWebhookDeliveryAttempt(
        result=_delivery_result_from_model(finalized),
        delivery=finalized,
        claimed=recorded,
    )


def send_notification_webhook(
    db: Session,
    *,
    webhook: NotificationWebhook,
    user: User,
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None = None,
    item: Item | SimpleNamespace | None = None,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
    delivery_kind: str = "live",
    item_title: str | None = None,
    feed_name: str | None = None,
    source_delivery_id: uuid.UUID | None = None,
    scope_key: str | None = None,
) -> NotificationWebhookDeliveryAttempt:
    delivery = reserve_notification_webhook_delivery(
        user=user,
        db=db,
        webhook=webhook,
        event_type=event_type,
        feed=feed,
        item=item,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
        delivery_kind=delivery_kind,
        item_title=item_title,
        feed_name=feed_name,
        source_delivery_id=source_delivery_id,
        scope_key=scope_key,
    )
    db.commit()
    return process_notification_webhook_delivery(db, delivery_id=delivery.id)


def send_notification_webhook_for_item(
    db: Session,
    *,
    webhook: NotificationWebhook,
    item: Item,
    feed: Feed,
    user: User,
) -> NotificationWebhookTestResponse:
    attempt = send_notification_webhook(
        db,
        webhook=webhook,
        user=user,
        event_type="rss_item_new",
        item=item,
        feed=feed,
    )
    return attempt.result


def retry_notification_webhook_delivery(
    db: Session,
    *,
    webhook: NotificationWebhook,
    delivery: NotificationWebhookDelivery,
) -> NotificationWebhookDelivery:
    retry_root_id = _notification_delivery_retry_root_id(delivery)
    if not try_acquire_notification_delivery_lock(
        db,
        webhook_id=webhook.id,
        event_type=delivery.event_type_snapshot,
        delivery_kind="retry",
        source_delivery_id=retry_root_id,
    ):
        reusable_retry = _find_notification_webhook_retry_reuse_candidate(
            db, webhook=webhook, delivery=delivery
        )
        if reusable_retry is not None:
            return reusable_retry
        raise NotificationWebhookRetryInProgressError(
            "Webhook retry is already queued or in progress"
        )

    reusable_retry = _find_notification_webhook_retry_reuse_candidate(
        db, webhook=webhook, delivery=delivery
    )
    if reusable_retry is not None:
        return reusable_retry
    retried = reserve_notification_webhook_delivery_from_saved_request(
        db, webhook=webhook, delivery=delivery
    )
    db.commit()
    return process_notification_webhook_delivery(db, delivery_id=retried.id).delivery


def reserve_retryable_notification_webhook_delivery(
    db: Session,
    *,
    webhook: NotificationWebhook,
    delivery: NotificationWebhookDelivery,
) -> NotificationWebhookRetryReservation | None:
    if delivery.success or not _is_retryable_notification_delivery(delivery):
        return None

    retry_root_id = _notification_delivery_retry_root_id(delivery)
    chain_attempt_count = _notification_delivery_chain_attempt_count(
        db, retry_root_id=retry_root_id
    )
    max_attempts = max(1, int(settings.notification_delivery_retry_max_attempts))
    if chain_attempt_count >= max_attempts:
        return None

    if not try_acquire_notification_delivery_lock(
        db,
        webhook_id=webhook.id,
        event_type=delivery.event_type_snapshot,
        delivery_kind="retry",
        source_delivery_id=retry_root_id,
    ):
        reusable_retry = _find_notification_webhook_retry_reuse_candidate(
            db, webhook=webhook, delivery=delivery
        )
        if reusable_retry is not None:
            countdown_seconds = (
                None
                if reusable_retry.delivery_state == NOTIFICATION_DELIVERY_SUCCEEDED
                else 0
            )
            return NotificationWebhookRetryReservation(
                delivery=reusable_retry,
                created=False,
                countdown_seconds=countdown_seconds,
            )
        return None

    reusable_retry = _find_notification_webhook_retry_reuse_candidate(
        db, webhook=webhook, delivery=delivery
    )
    if reusable_retry is not None:
        return NotificationWebhookRetryReservation(
            delivery=reusable_retry, created=False, countdown_seconds=None
        )

    countdown_seconds = _notification_delivery_retry_delay_seconds(chain_attempt_count)
    retried = reserve_notification_webhook_delivery_from_saved_request(
        db,
        webhook=webhook,
        delivery=delivery,
        not_before=datetime.now(timezone.utc) + timedelta(seconds=countdown_seconds),
    )
    return NotificationWebhookRetryReservation(
        delivery=retried,
        created=True,
        countdown_seconds=countdown_seconds,
    )
