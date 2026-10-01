"""Delegated administration with explicit authorization-custodian adoption."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import uuid
from pydantic import ValidationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.api.deps import (
    get_current_user,
    get_authorization_context,
    get_data_access_context,
    require_token_scopes,
)
from app.db.session import get_db
from app.models.automation_receiver import AutomationReceiverCredential
from app.models.feed import Feed
from app.models.notification_webhook import NotificationWebhook
from app.models.user import User
from app.schemas.notification import NotificationWebhookWrite
from app.services.notification_webhook_storage import (
    apply_notification_webhook_updates,
    notification_webhook_response_from_model,
)
from app.services.notification_webhook_validation import (
    validate_notification_webhook_payload_for_actor,
)
from app.services.webhook_credentials import load_credential
from app.schemas.team_integration import (
    AdoptIntegration,
    ReceiverCredentialWrite,
    IntegrationEnabled,
)
from app.services.audit import record_audit
from app.services.authorization import AuthorizationContext
from app.services.data_access_policy import (
    DataAccessContext,
    handling_label_access_predicate,
)
from app.services.integration_compat import ensure_webhook_integration
from app.services.team_access import (
    lock_team_for_current_access,
    assert_current_team_access,
)
from app.services.team_integrations import (
    adopt_destination,
    destination_summary,
    team_destination,
)
from app.services.webhook_request_authority import fence_webhook_request


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/teams/{team_id}/integrations",
    tags=["teams"],
    dependencies=[Depends(_no_store)],
)


def _manager(
    team_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    access: DataAccessContext = Depends(get_data_access_context),
    _scope=Depends(require_token_scopes("write:teams", "write:notifications")),
):
    if not authorization.has("write:teams"):
        raise HTTPException(403, "Team integration administration requires write:teams")
    fence_webhook_request(
        db,
        request=request,
        authorization=authorization,
        data_access=access,
        permission="write:notifications",
    )
    fence_webhook_request(
        db,
        request=request,
        authorization=authorization,
        data_access=access,
        permission="write:teams",
    )
    if (
        lock_team_for_current_access(
            db, team_id=team_id, user_id=user.id, manage=True, for_update=True
        )
        is None
    ):
        raise HTTPException(
            404, "Team not found or current membership does not permit administration"
        )
    return user, access


@router.get("")
def list_team_integrations(
    team_id: uuid.UUID, db: Session = Depends(get_db), actor=Depends(_manager)
):
    rows = db.scalars(
        select(NotificationWebhook)
        .where(NotificationWebhook.team_id == team_id)
        .order_by(NotificationWebhook.name)
        .limit(101)
    ).all()
    return {
        "items": [destination_summary(row) for row in rows[:100]],
        "limit": 100,
        "has_more": len(rows) > 100,
    }


@router.post("/{webhook_id}/adopt")
def adopt_team_integration(
    team_id: uuid.UUID,
    webhook_id: uuid.UUID,
    payload: AdoptIntegration,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    user, access = actor
    row = db.scalar(
        select(NotificationWebhook)
        .where(NotificationWebhook.id == webhook_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or (row.team_id is None and row.user_id != user.id)
        or (row.team_id and row.team_id != team_id)
    ):
        raise HTTPException(404, "Integration not found")
    if (
        row.team_id is None
        and db.scalar(
            select(func.count())
            .select_from(NotificationWebhook)
            .where(NotificationWebhook.team_id == team_id)
        )
        >= 100
    ):
        raise HTTPException(409, "This team already has 100 integrations")
    feed_ids = set(
        db.scalars(
            select(Feed.id).where(
                handling_label_access_predicate(Feed.handling_label_id, access)
            )
        )
    )
    adopt_destination(
        db,
        row=row,
        team_id=team_id,
        actor=user,
        available_feed_ids=feed_ids,
        expected_revision=payload.expected_revision,
    )
    record_audit(
        db,
        actor_user_id=user.id,
        action="team.integration.adopt",
        resource_type="notification_webhook",
        resource_id=row.id,
        metadata={
            "team_id": str(team_id),
            "ownership_revision": row.ownership_revision,
        },
    )
    result = destination_summary(row)
    db.commit()
    return result


@router.patch("/{webhook_id}/enabled")
def set_team_integration_enabled(
    team_id: uuid.UUID,
    webhook_id: uuid.UUID,
    payload: IntegrationEnabled,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    user, _access = actor
    row = team_destination(db, team_id, webhook_id, lock=True)
    assert_current_team_access(db, team_id=team_id, user_id=user.id, manage=True)
    if row.ownership_revision != payload.expected_revision:
        raise HTTPException(409, "Integration changed; reload before saving")
    if payload.enabled and row.user_id != user.id:
        raise HTTPException(
            409, "Adopt this integration under your current access before enabling it"
        )
    row.enabled = payload.enabled
    row.ownership_revision += 1
    ensure_webhook_integration(db, row)
    record_audit(
        db,
        actor_user_id=user.id,
        action="team.integration.enabled",
        resource_type="notification_webhook",
        resource_id=row.id,
        metadata={"enabled": row.enabled},
    )
    result = destination_summary(row)
    db.commit()
    return result


@router.get("/{webhook_id}/receiver-credentials")
def list_receiver_credentials(
    team_id: uuid.UUID,
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    rows = db.scalars(
        select(AutomationReceiverCredential)
        .where(
            AutomationReceiverCredential.webhook_id == webhook_id,
            AutomationReceiverCredential.team_id == team_id,
        )
        .order_by(
            (
                AutomationReceiverCredential.revoked_at.is_(None)
                & (AutomationReceiverCredential.expires_at > datetime.now(timezone.utc))
            ).desc(),
            AutomationReceiverCredential.created_at.desc(),
        )
        .limit(100)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "expires_at": row.expires_at,
                "revoked_at": row.revoked_at,
            }
            for row in rows
        ]
    }


@router.post("/{webhook_id}/receiver-credentials")
def issue_receiver_credential(
    team_id: uuid.UUID,
    webhook_id: uuid.UUID,
    payload: ReceiverCredentialWrite,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    user, _access = actor
    team_destination(db, team_id, webhook_id, lock=True)
    assert_current_team_access(db, team_id=team_id, user_id=user.id, manage=True)
    now = datetime.now(timezone.utc)
    if (
        payload.expires_at.tzinfo is None
        or not now < payload.expires_at <= now + timedelta(days=366)
    ):
        raise HTTPException(
            422, "Credential expiry must include a timezone and be within one year"
        )
    count = db.scalar(
        select(func.count())
        .select_from(AutomationReceiverCredential)
        .where(
            AutomationReceiverCredential.webhook_id == webhook_id,
            AutomationReceiverCredential.revoked_at.is_(None),
            AutomationReceiverCredential.expires_at > now,
        )
    )
    if count >= 5:
        raise HTTPException(
            409,
            "Revoke an existing receiver credential before creating another (maximum five active)",
        )
    identity, secret = uuid.uuid4(), secrets.token_urlsafe(32)
    token = f"tlrecv_{identity.hex}_{secret}"
    row = AutomationReceiverCredential(
        id=identity,
        webhook_id=webhook_id,
        team_id=team_id,
        name=payload.name,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=payload.expires_at,
    )
    db.add(row)
    record_audit(
        db,
        actor_user_id=user.id,
        action="team.integration.receiver.issue",
        resource_type="notification_webhook",
        resource_id=webhook_id,
        metadata={
            "credential_id": str(identity),
            "expires_at": payload.expires_at.isoformat(),
        },
    )
    db.commit()
    return {
        "id": identity,
        "token": token,
        "expires_at": payload.expires_at,
        "scope": "destination:callbacks destination:withdrawals",
    }


@router.delete("/{webhook_id}/receiver-credentials/{credential_id}")
def revoke_receiver_credential(
    team_id: uuid.UUID,
    webhook_id: uuid.UUID,
    credential_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    row = db.scalar(
        select(AutomationReceiverCredential)
        .where(
            AutomationReceiverCredential.id == credential_id,
            AutomationReceiverCredential.webhook_id == webhook_id,
            AutomationReceiverCredential.team_id == team_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Receiver credential not found")
    assert_current_team_access(db, team_id=team_id, user_id=actor[0].id, manage=True)
    row.revoked_at = row.revoked_at or datetime.now(timezone.utc)
    record_audit(
        db,
        actor_user_id=actor[0].id,
        action="team.integration.receiver.revoke",
        resource_type="notification_webhook",
        resource_id=webhook_id,
        metadata={"credential_id": str(credential_id)},
    )
    db.commit()
    return {"revoked": True}


@router.get("/{webhook_id}/configuration")
def get_team_integration_configuration(
    team_id: uuid.UUID,
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    user, access = actor
    row = team_destination(db, team_id, webhook_id)
    visible = set(
        db.scalars(
            select(Feed.id).where(
                handling_label_access_predicate(Feed.handling_label_id, access)
            )
        )
    )
    return notification_webhook_response_from_model(
        row, accessible_feed_ids=visible, redact_secrets=row.user_id != user.id
    )


@router.put("/{webhook_id}/configuration")
def update_team_integration_configuration(
    team_id: uuid.UUID,
    webhook_id: uuid.UUID,
    payload: NotificationWebhookWrite,
    expected_revision: int,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    user, access = actor
    row = team_destination(db, team_id, webhook_id, lock=True)
    assert_current_team_access(db, team_id=team_id, user_id=user.id, manage=True)
    if row.ownership_revision != expected_revision or row.user_id != user.id:
        raise HTTPException(
            409, "Reload and adopt this destination before editing its configuration"
        )
    retained = {
        "include_article_text": row.include_article_text,
        "payload_mode": row.payload_mode,
        "conditions": row.conditions_json,
        "credential_profile_id": row.credential_profile_id,
    }
    try:
        payload = NotificationWebhookWrite.model_validate(
            {
                **payload.model_dump(),
                **{
                    key: value
                    for key, value in retained.items()
                    if key not in payload.model_fields_set
                },
            }
        )
    except ValidationError as exc:
        raise HTTPException(
            422,
            "The update conflicts with retained automation settings; reload the destination",
        ) from exc
    visible = set(
        db.scalars(
            select(Feed.id).where(
                handling_label_access_predicate(Feed.handling_label_id, access)
            )
        )
    )
    try:
        validate_notification_webhook_payload_for_actor(
            payload, visible, actor_user=user
        )
        if payload.credential_profile_id:
            load_credential(
                db,
                profile_id=payload.credential_profile_id,
                user_id=user.id,
                lock=True,
                require_enabled=False,
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    apply_notification_webhook_updates(row, payload)
    row.ownership_revision += 1
    ensure_webhook_integration(db, row)
    record_audit(
        db,
        actor_user_id=user.id,
        action="team.integration.configure",
        resource_type="notification_webhook",
        resource_id=row.id,
        metadata={
            "team_id": str(team_id),
            "ownership_revision": row.ownership_revision,
        },
    )
    result = notification_webhook_response_from_model(row, accessible_feed_ids=visible)
    db.commit()
    return result
