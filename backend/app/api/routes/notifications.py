import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_operator_user, require_token_scopes
from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.token_scopes import SCOPE_READ_NOTIFICATIONS, SCOPE_WRITE_NOTIFICATIONS, has_required_scope
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
from app.services.integration_compat import delete_webhook_integration, ensure_webhook_integration
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
    notification_webhook_response_from_model,
    reserve_webhook_failed_notification_deliveries,
    retry_notification_webhook_delivery,
    test_notification_webhook,
    validate_notification_webhook_payload_for_actor,
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
):
    return get_notification_analytics(db, user_id=user.id)


@router.get("/webhooks", response_model=list[NotificationWebhookResponse])
def list_notification_webhooks(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_NOTIFICATIONS)),
):
    webhooks = db.scalars(
        select(NotificationWebhook)
        .where(NotificationWebhook.user_id == user.id)
        .order_by(NotificationWebhook.created_at.asc())
    ).all()
    token_scopes = getattr(request.state, "token_scopes", None)
    can_read_secrets = user.role in {ROLE_ADMIN, ROLE_ANALYST} and (
        token_scopes is None or has_required_scope(set(token_scopes), SCOPE_WRITE_NOTIFICATIONS)
    )
    return [
        notification_webhook_response_from_model(webhook, redact_secrets=not can_read_secrets)
        for webhook in webhooks
    ]


@router.post("/webhooks", response_model=NotificationWebhookResponse, status_code=status.HTTP_201_CREATED)
def create_notification_webhook(
    payload: NotificationWebhookWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    _validate_payload(db, payload, actor_user=user)
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
    return notification_webhook_response_from_model(webhook)


@router.patch("/webhooks/{webhook_id}", response_model=NotificationWebhookResponse)
def update_notification_webhook(
    webhook_id: uuid.UUID,
    payload: NotificationWebhookWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    webhook = db.scalar(
        select(NotificationWebhook).where(NotificationWebhook.id == webhook_id, NotificationWebhook.user_id == user.id)
    )
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    _validate_payload(db, payload, actor_user=user)
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
    return notification_webhook_response_from_model(webhook)


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification_webhook(
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    webhook = db.scalar(
        select(NotificationWebhook).where(NotificationWebhook.id == webhook_id, NotificationWebhook.user_id == user.id)
    )
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

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
):
    webhook = db.scalar(
        select(NotificationWebhook).where(NotificationWebhook.id == webhook_id, NotificationWebhook.user_id == user.id)
    )
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    deliveries_query = select(NotificationWebhookDelivery).where(NotificationWebhookDelivery.webhook_id == webhook.id)
    total = db.scalar(select(func.count()).select_from(deliveries_query.subquery())) or 0
    deliveries = db.scalars(
        deliveries_query
        .order_by(NotificationWebhookDelivery.attempted_at.desc(), NotificationWebhookDelivery.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return NotificationWebhookDeliveryListResponse(
        deliveries=[notification_webhook_delivery_response_from_model(delivery) for delivery in deliveries],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/webhooks/{webhook_id}/deliveries/{delivery_id}/retry",
    response_model=NotificationWebhookDeliveryResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Webhook or delivery not found"},
        status.HTTP_409_CONFLICT: {"description": "Webhook delivery cannot be retried right now"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Validation error"},
    },
)
def retry_notification_webhook_delivery_route(
    webhook_id: uuid.UUID,
    delivery_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    webhook = db.scalar(
        select(NotificationWebhook).where(NotificationWebhook.id == webhook_id, NotificationWebhook.user_id == user.id)
    )
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    delivery = db.scalar(
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.id == delivery_id,
            NotificationWebhookDelivery.webhook_id == webhook.id,
        )
    )
    if delivery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook delivery not found")
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
        retried = retry_notification_webhook_delivery(db, webhook=webhook, delivery=delivery)
    except NotificationWebhookRetryInProgressError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if retried.delivery_state in {NOTIFICATION_DELIVERY_PENDING, NOTIFICATION_DELIVERY_SENDING}:
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
    db.commit()
    db.refresh(retried)
    response = notification_webhook_delivery_response_from_model(retried)
    if failed_delivery_reservations is not None:
        if not enqueue_notification_webhook_delivery_processing(failed_delivery_reservations.delivery_ids):
            logger.warning(
                "notification_webhook_retry_followup_enqueue_failed webhook_id=%s delivery_id=%s retried_delivery_id=%s",
                webhook.id,
                delivery.id,
                retried.id,
            )
            response.warnings.append(
                "Webhook-failed notification delivery is reserved but enqueue was delayed; the recovery sweep will retry it."
            )
    return response


@router.post("/webhooks/test", response_model=NotificationWebhookTestResponse)
def test_notification_webhook_route(
    payload: NotificationWebhookTestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    _validate_payload(db, payload.webhook, actor_user=user)
    try:
        result = test_notification_webhook(
            db,
            user=user,
            payload=payload.webhook,
            sample_item_id=payload.sample_item_id,
            sample_feed_id=payload.sample_feed_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

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

def _validate_payload(db: Session, payload: NotificationWebhookWrite, *, actor_user: User) -> None:
    available_feed_ids = set(db.scalars(select(Feed.id)).all())
    try:
        validate_notification_webhook_payload_for_actor(payload, available_feed_ids, actor_user=actor_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
