from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.schemas.notification import (
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services import notification_webhook_http
from app.services.authorization import AuthorizationContext
from app.services.daily_brief_notifications import (
    get_latest_daily_brief_notification_context,
)
from app.services.data_access_policy import DataAccessContext
from app.services.notification_webhook_contexts import (
    build_alert_match_context_for_item,
    build_sample_feed_for_event as _build_sample_feed_for_event,
    resolve_sample_feed_and_item as _resolve_sample_feed_and_item,
)
from app.services.notification_webhook_requests import (
    RenderedNotificationRequest,
    render_notification_request,
)
from app.services.notification_webhook_storage import (
    redact_notification_test_response as _redact_notification_test_response,
)
from app.services.notification_webhook_templates import (
    AlertMatchContext,
    DailyDigestContext,
    FailedWebhookContext,
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
from app.services.notification_webhook_validation import (
    validate_notification_actor_for_delivery,
    validate_notification_target_url as _validate_notification_target_url,
)


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

    destination_digest, request_fingerprint = notification_webhook_test_request_digests(
        rendered,
        snapshot=snapshot,
        logical_request=logical_request,
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
            state=("denied" if snapshot.decision == "egress_denied" else "unavailable"),
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

    io_outcome = "response_received" if result.status_code is not None else "not_sent"
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
