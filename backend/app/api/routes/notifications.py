import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_authorization_context,
    get_data_access_context,
    get_operator_user,
    require_token_scopes,
)
from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.token_scopes import (
    SCOPE_READ_NOTIFICATIONS,
    SCOPE_WRITE_NOTIFICATIONS,
    has_required_scope,
)
from app.db.session import get_db
from app.models.feed import Feed
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import (
    NotificationAnalyticsResponse,
    NotificationWebhookDeliveryListResponse,
    NotificationWebhookDeliveryResponse,
    NotificationTemplateVariable,
    NotificationWebhookResponse,
    NotificationWebhookTestRequest,
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    fence_authorization_context,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyError,
    fence_data_access_context,
    handling_label_access_predicate,
)
from app.services.data_policy_audit import record_data_policy_decision
from app.services.integration_compat import (
    delete_webhook_integration,
    ensure_webhook_integration,
)
from app.services.notification_webhooks import (
    NotificationWebhookRetryInProgressError,
    apply_notification_webhook_updates,
    build_notification_webhook,
    get_notification_analytics,
    list_template_variables,
    NOTIFICATION_DELIVERY_FAILED,
    NOTIFICATION_DELIVERY_PENDING,
    NOTIFICATION_DELIVERY_SENDING,
    notification_webhook_delivery_response_from_model,
    notification_delivery_data_access_predicate,
    notification_delivery_would_deny_summary,
    notification_webhook_response_from_model,
    notification_webhook_write_from_model,
    reserve_webhook_failed_notification_deliveries,
    retry_notification_webhook_delivery,
    test_notification_webhook,
    validate_notification_webhook_payload_for_actor,
)
from app.services.notification_webhook_test_policy import (
    NotificationWebhookTestPolicyError,
)
from app.services.webhook_delivery_locking import WebhookDeliveryBusyError
from app.tasks.feed_tasks import enqueue_notification_webhook_delivery_processing

router = APIRouter(prefix="/notifications", tags=["notifications"])
logger = logging.getLogger(__name__)


@router.get("/template-variables", response_model=list[NotificationTemplateVariable])
def get_notification_template_variables(
    _user: User = Depends(require_token_scopes(SCOPE_READ_NOTIFICATIONS)),
):
    return list_template_variables()


@router.get("/analytics", response_model=NotificationAnalyticsResponse)
def get_notifications_analytics(
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_NOTIFICATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    fence_data_access_context(db, data_access)
    response = get_notification_analytics(
        db,
        user_id=user.id,
        data_access=data_access,
    )
    summary = notification_delivery_would_deny_summary(
        db,
        data_access=data_access,
        user_id=user.id,
    )
    if summary.affected_count:
        record_data_policy_decision(
            db,
            context=data_access,
            decision="would_deny",
            resource_type="notification_webhook_delivery",
            surface="notifications.analytics.read",
            handling_label_ids=summary.handling_label_ids,
            affected_count=summary.affected_count,
            metadata_extra={"history_scope": "user"},
        )
        db.commit()
        fence_data_access_context(db, data_access)
    return response


@router.get("/webhooks", response_model=list[NotificationWebhookResponse])
def list_notification_webhooks(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_NOTIFICATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    webhooks = db.scalars(
        select(NotificationWebhook)
        .where(NotificationWebhook.user_id == user.id)
        .order_by(NotificationWebhook.created_at.asc())
    ).all()
    token_scopes = getattr(request.state, "token_scopes", None)
    can_read_secrets = user.role in {ROLE_ADMIN, ROLE_ANALYST} and (
        token_scopes is None
        or has_required_scope(set(token_scopes), SCOPE_WRITE_NOTIFICATIONS)
    )
    accessible_feed_ids = _accessible_feed_ids(db, data_access=data_access)
    return [
        notification_webhook_response_from_model(
            webhook,
            redact_secrets=not can_read_secrets,
            accessible_feed_ids=accessible_feed_ids,
        )
        for webhook in webhooks
    ]


@router.post(
    "/webhooks",
    response_model=NotificationWebhookResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_notification_webhook(
    payload: NotificationWebhookWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    _require_selected_feed_ids(payload)
    accessible_feed_ids = _validate_payload(
        db,
        payload,
        actor_user=user,
        data_access=data_access,
    )
    webhook = build_notification_webhook(user.id, payload)
    db.add(webhook)
    db.flush()
    ensure_webhook_integration(db, webhook)
    record_audit(
        db,
        actor_user_id=user.id,
        action="notifications.webhook.create",
        resource_type="notification_webhook",
        resource_id=str(webhook.id),
        metadata={"name": webhook.name, "feed_scope": webhook.feed_scope},
    )
    db.commit()
    db.refresh(webhook)
    accessible_feed_ids = _accessible_feed_ids(db, data_access=data_access)
    return notification_webhook_response_from_model(
        webhook,
        accessible_feed_ids=accessible_feed_ids,
    )


@router.patch("/webhooks/{webhook_id}", response_model=NotificationWebhookResponse)
def update_notification_webhook(
    webhook_id: uuid.UUID,
    payload: NotificationWebhookWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    webhook = db.scalar(
        select(NotificationWebhook)
        .where(
            NotificationWebhook.id == webhook_id,
            NotificationWebhook.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found"
        )

    accessible_feed_ids = _validate_payload(
        db,
        payload,
        actor_user=user,
        data_access=data_access,
    )
    _preserve_inaccessible_selected_feeds(
        webhook,
        payload,
        accessible_feed_ids=accessible_feed_ids,
    )
    apply_notification_webhook_updates(webhook, payload)
    db.add(webhook)
    ensure_webhook_integration(db, webhook)
    record_audit(
        db,
        actor_user_id=user.id,
        action="notifications.webhook.update",
        resource_type="notification_webhook",
        resource_id=str(webhook.id),
        metadata={"name": webhook.name, "feed_scope": webhook.feed_scope},
    )
    db.commit()
    db.refresh(webhook)
    accessible_feed_ids = _accessible_feed_ids(db, data_access=data_access)
    return notification_webhook_response_from_model(
        webhook,
        accessible_feed_ids=accessible_feed_ids,
    )


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification_webhook(
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    webhook = db.scalar(
        select(NotificationWebhook).where(
            NotificationWebhook.id == webhook_id, NotificationWebhook.user_id == user.id
        )
    )
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found"
        )

    webhook_name = webhook.name
    try:
        delete_webhook_integration(db, webhook)
    except WebhookDeliveryBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    record_audit(
        db,
        actor_user_id=user.id,
        action="notifications.webhook.delete",
        resource_type="notification_webhook",
        resource_id=str(webhook_id),
        metadata={"name": webhook_name},
    )
    db.commit()


@router.get(
    "/webhooks/{webhook_id}/deliveries",
    response_model=NotificationWebhookDeliveryListResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Webhook not found"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Validation error"},
    },
)
def list_notification_webhook_deliveries(
    webhook_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_NOTIFICATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    fence_data_access_context(db, data_access)
    webhook = db.scalar(
        select(NotificationWebhook).where(
            NotificationWebhook.id == webhook_id, NotificationWebhook.user_id == user.id
        )
    )
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found"
        )

    deliveries_query = select(NotificationWebhookDelivery).where(
        NotificationWebhookDelivery.webhook_id == webhook.id,
        notification_delivery_data_access_predicate(data_access),
    )
    total = (
        db.scalar(select(func.count()).select_from(deliveries_query.subquery())) or 0
    )
    deliveries = db.scalars(
        deliveries_query.order_by(
            NotificationWebhookDelivery.attempted_at.desc(),
            NotificationWebhookDelivery.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    response = NotificationWebhookDeliveryListResponse(
        deliveries=[
            notification_webhook_delivery_response_from_model(delivery)
            for delivery in deliveries
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
    summary = notification_delivery_would_deny_summary(
        db,
        data_access=data_access,
        webhook_id=webhook.id,
    )
    if summary.affected_count:
        record_data_policy_decision(
            db,
            context=data_access,
            decision="would_deny",
            resource_type="notification_webhook",
            resource_id=webhook.id,
            surface="notifications.webhook.deliveries.read",
            handling_label_ids=summary.handling_label_ids,
            affected_count=summary.affected_count,
            metadata_extra={"history_scope": "webhook"},
        )
        db.commit()
        fence_data_access_context(db, data_access)
    return response


@router.post(
    "/webhooks/{webhook_id}/deliveries/{delivery_id}/retry",
    response_model=NotificationWebhookDeliveryResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Webhook or delivery not found"},
        status.HTTP_409_CONFLICT: {
            "description": "Webhook delivery cannot be retried right now"
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Validation error"},
    },
)
def retry_notification_webhook_delivery_route(
    webhook_id: uuid.UUID,
    delivery_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    authorization = get_authorization_context(request)
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook retry authorization is unavailable. Retry the request.",
        )
    webhook = db.scalar(
        select(NotificationWebhook).where(
            NotificationWebhook.id == webhook_id, NotificationWebhook.user_id == user.id
        )
    )
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found"
        )

    delivery = db.scalar(
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.id == delivery_id,
            NotificationWebhookDelivery.webhook_id == webhook.id,
            notification_delivery_data_access_predicate(data_access),
        )
    )
    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook delivery not found"
        )
    if delivery.delivery_state in {"pending", "sending"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook delivery is already queued or in progress",
        )
    if delivery.delivery_state != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed webhook deliveries can be retried",
        )

    try:
        retried = retry_notification_webhook_delivery(
            db, webhook=webhook, delivery=delivery
        )
    except NotificationWebhookRetryInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if retried.delivery_state in {
        NOTIFICATION_DELIVERY_PENDING,
        NOTIFICATION_DELIVERY_SENDING,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook retry is already queued or in progress",
        )
    failed_delivery_reservations = (
        reserve_webhook_failed_notification_deliveries(db, failed_delivery=retried)
        if retried.delivery_state == NOTIFICATION_DELIVERY_FAILED
        and not retried.success
        and retried.event_type_snapshot != "webhook_failed"
        else None
    )
    record_audit(
        db,
        actor_user_id=user.id,
        action="notifications.webhook.retry",
        resource_type="notification_webhook_delivery",
        resource_id=str(retried.id),
        metadata={
            "webhook_id": str(webhook.id),
            "retry_of_delivery_id": str(delivery.id),
            "success": retried.success,
            "status_code": retried.status_code,
        },
        success=retried.success,
    )
    retried_id = retried.id
    retry_warning: str | None = None
    db.commit()
    if failed_delivery_reservations is not None:
        if not enqueue_notification_webhook_delivery_processing(
            failed_delivery_reservations.delivery_ids
        ):
            logger.warning(
                "notification_webhook_retry_followup_enqueue_failed webhook_id=%s delivery_id=%s retried_delivery_id=%s",
                webhook.id,
                delivery.id,
                retried.id,
            )
            retry_warning = (
                "Webhook-failed notification delivery is reserved but enqueue was "
                "delayed; the recovery sweep will retry it."
            )
    final_delivery = _refilter_retry_response(
        db,
        webhook_id=webhook_id,
        delivery_id=retried_id,
        authorization=authorization,
        data_access=data_access,
    )
    retry_summary = notification_delivery_would_deny_summary(
        db,
        data_access=data_access,
        delivery_id=final_delivery.id,
    )
    if retry_summary.affected_count:
        record_data_policy_decision(
            db,
            context=data_access,
            decision="would_deny",
            resource_type="notification_webhook_delivery",
            resource_id=final_delivery.id,
            surface="notifications.webhook.delivery.retry",
            handling_label_ids=retry_summary.handling_label_ids,
            affected_count=retry_summary.affected_count,
            request_served_known=False,
            metadata_extra={"history_scope": "retry"},
        )
        db.commit()
        final_delivery = _refilter_retry_response(
            db,
            webhook_id=webhook_id,
            delivery_id=retried_id,
            authorization=authorization,
            data_access=data_access,
        )
    response = notification_webhook_delivery_response_from_model(final_delivery)
    if retry_warning is not None:
        response.warnings.append(retry_warning)
    return response


@router.post("/webhooks/test", response_model=NotificationWebhookTestResponse)
def test_notification_webhook_route(
    payload: NotificationWebhookTestRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    _require_selected_feed_ids(payload.webhook)
    authorization = get_authorization_context(request)
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook test authorization is unavailable. Retry the request.",
        )
    _validate_payload(
        db,
        payload.webhook,
        actor_user=user,
        data_access=data_access,
    )
    try:
        result = test_notification_webhook(
            db,
            user=user,
            payload=payload.webhook,
            sample_item_id=payload.sample_item_id,
            sample_feed_id=payload.sample_feed_id,
            data_access=data_access,
            authorization=authorization,
            operation_id=str(request.state.request_id),
        )
    except NotificationWebhookTestPolicyError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers={"X-Error-Code": exc.code},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    record_audit(
        db,
        actor_user_id=user.id,
        action="notifications.webhook.test",
        resource_type="notification_webhook",
        metadata={
            "name": payload.webhook.name,
            "success": result.success,
            "status_code": result.status_code,
        },
        success=result.success,
    )
    db.commit()
    return result


def _validate_payload(
    db: Session,
    payload: NotificationWebhookWrite,
    *,
    actor_user: User,
    data_access: DataAccessContext,
) -> set[uuid.UUID]:
    available_feed_ids = _accessible_feed_ids(db, data_access=data_access)
    try:
        validate_notification_webhook_payload_for_actor(
            payload, available_feed_ids, actor_user=actor_user
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return available_feed_ids


def _require_selected_feed_ids(payload: NotificationWebhookWrite) -> None:
    if payload.feed_scope == "selected" and not payload.feed_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least one selected feed is required",
        )


def _preserve_inaccessible_selected_feeds(
    webhook: NotificationWebhook,
    payload: NotificationWebhookWrite,
    *,
    accessible_feed_ids: set[uuid.UUID],
) -> None:
    if payload.feed_scope == "all":
        return

    stored = notification_webhook_write_from_model(webhook)
    stored_hidden_ids = [
        feed_id
        for feed_id in stored.feed_ids
        if feed_id not in accessible_feed_ids
    ]
    stored_visible_ids = [
        feed_id for feed_id in stored.feed_ids if feed_id in accessible_feed_ids
    ]
    if not payload.feed_ids:
        # An empty selected projection is a valid no-op only when the current
        # selected configuration also exposes no feed IDs to this actor. Treat
        # both all-hidden and legacy-empty storage identically so this merge
        # rule cannot be used to count inaccessible selections.
        if stored.feed_scope != "selected" or stored_visible_ids:
            _require_selected_feed_ids(payload)
        payload.feed_ids = list(stored_hidden_ids)
        return

    seen = set(payload.feed_ids)
    payload.feed_ids.extend(
        feed_id for feed_id in stored_hidden_ids if feed_id not in seen
    )


def _refilter_retry_response(
    db: Session,
    *,
    webhook_id: uuid.UUID,
    delivery_id: uuid.UUID,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
) -> NotificationWebhookDelivery:
    try:
        fence_authorization_context(db, authorization)
        fence_data_access_context(db, data_access)
    except (AuthorizationStateUnavailable, DataPolicyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook retry authorization changed. Reload delivery history before retrying again.",
        ) from exc
    delivery = db.scalar(
        select(NotificationWebhookDelivery)
        .where(
            NotificationWebhookDelivery.id == delivery_id,
            NotificationWebhookDelivery.webhook_id == webhook_id,
            notification_delivery_data_access_predicate(data_access),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook delivery not found",
        )
    return delivery


def _accessible_feed_ids(
    db: Session,
    *,
    data_access: DataAccessContext,
) -> set[uuid.UUID]:
    # The shared policy lock is deliberately held until the route transaction
    # ends. For webhook tests, that prevents a feed relabel from racing the
    # final authorization and outbound HTTP request.
    fence_data_access_context(db, data_access)
    return set(
        db.scalars(
            select(Feed.id).where(
                handling_label_access_predicate(
                    Feed.handling_label_id,
                    data_access,
                )
            )
        ).all()
    )
