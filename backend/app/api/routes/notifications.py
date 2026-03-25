import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_token_scopes
from app.core.token_scopes import SCOPE_READ_NOTIFICATIONS, SCOPE_WRITE_NOTIFICATIONS
from app.db.session import get_db
from app.models.feed import Feed
from app.models.notification_webhook import NotificationWebhook
from app.models.user import User
from app.schemas.notification import (
    NotificationTemplateVariable,
    NotificationWebhookResponse,
    NotificationWebhookTestRequest,
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services.audit import record_audit
from app.services.notification_webhooks import (
    apply_notification_webhook_updates,
    build_notification_webhook,
    list_template_variables,
    notification_webhook_response_from_model,
    test_notification_webhook,
    validate_notification_webhook_payload,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/template-variables", response_model=list[NotificationTemplateVariable])
def get_notification_template_variables(
    _user: User = Depends(require_token_scopes(SCOPE_READ_NOTIFICATIONS)),
):
    return list_template_variables()


@router.get("/webhooks", response_model=list[NotificationWebhookResponse])
def list_notification_webhooks(
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_NOTIFICATIONS)),
):
    webhooks = db.scalars(
        select(NotificationWebhook)
        .where(NotificationWebhook.user_id == user.id)
        .order_by(NotificationWebhook.created_at.asc())
    ).all()
    return [notification_webhook_response_from_model(webhook) for webhook in webhooks]


@router.post("/webhooks", response_model=NotificationWebhookResponse, status_code=status.HTTP_201_CREATED)
def create_notification_webhook(
    payload: NotificationWebhookWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    _validate_payload(db, payload)
    webhook = build_notification_webhook(user.id, payload)
    db.add(webhook)
    db.flush()
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
    user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    webhook = db.scalar(
        select(NotificationWebhook).where(NotificationWebhook.id == webhook_id, NotificationWebhook.user_id == user.id)
    )
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    _validate_payload(db, payload)
    apply_notification_webhook_updates(webhook, payload)
    db.add(webhook)
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
    user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    webhook = db.scalar(
        select(NotificationWebhook).where(NotificationWebhook.id == webhook_id, NotificationWebhook.user_id == user.id)
    )
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    db.delete(webhook)
    record_audit(
        db,
        actor_user_id=user.id,
        action="notifications.webhook.delete",
        resource_type="notification_webhook",
        resource_id=str(webhook_id),
        metadata={"name": webhook.name},
    )
    db.commit()


@router.post("/webhooks/test", response_model=NotificationWebhookTestResponse)
def test_notification_webhook_route(
    payload: NotificationWebhookTestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_NOTIFICATIONS)),
):
    _validate_payload(db, payload.webhook)
    try:
        result = test_notification_webhook(
            db,
            user=user,
            payload=payload.webhook,
            sample_item_id=payload.sample_item_id,
            sample_feed_id=payload.sample_feed_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

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


def _validate_payload(db: Session, payload: NotificationWebhookWrite) -> None:
    available_feed_ids = set(db.scalars(select(Feed.id)).all())
    try:
        validate_notification_webhook_payload(payload, available_feed_ids)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
