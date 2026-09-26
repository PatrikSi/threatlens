"""Personal credential administration and non-sending subscription previews."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import String, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_authorization_context,
    get_current_user,
    get_data_access_context,
    get_operator_user,
    require_token_scopes,
)
from app.db.session import get_db
from app.models.integration import IntegrationDelivery, IntegrationEvent
from app.models.team import Team
from app.models.user import User
from app.models.webhook_credential import WebhookCredentialProfile
from app.schemas.notification import NotificationEventType, NotificationWebhookWrite
from app.schemas.webhook_automation import (
    WebhookCredentialResponse,
    WebhookCredentialWrite,
    WebhookPreviewResponse,
    WebhookConditionCheck,
)
from app.services.audit import record_audit
from app.services.authorization import AuthorizationContext
from app.services.data_access_policy import DataAccessContext
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
    data_access_envelope_predicate,
)
from app.services.team_access import team_access_predicate
from app.services.webhook_automation import (
    AUTOMATION_EVENTS,
    MAX_AUTOMATION_BYTES,
    automation_envelope,
)
from app.services.webhook_conditions import evaluate_conditions, event_condition_values
from app.services.webhook_credentials import credential_response, update_credential
from app.services.webhook_request_authority import fence_webhook_request

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/credential-profiles", response_model=list[WebhookCredentialResponse])
def list_credential_profiles(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _scope=Depends(require_token_scopes("read:notifications")),
):
    return [
        credential_response(profile)
        for profile in db.scalars(
            select(WebhookCredentialProfile)
            .where(WebhookCredentialProfile.user_id == user.id)
            .order_by(WebhookCredentialProfile.created_at, WebhookCredentialProfile.id)
            .limit(100)
        ).all()
    ]


@router.post(
    "/credential-profiles", response_model=WebhookCredentialResponse, status_code=201
)
def create_credential_profile(
    payload: WebhookCredentialWrite,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope=Depends(require_token_scopes("write:notifications")),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    fence_webhook_request(
        db,
        request=request,
        authorization=authorization,
        data_access=data_access,
        permission="write:notifications",
        exclusive_owner=True,
    )
    count = db.scalar(
        select(func.count())
        .select_from(WebhookCredentialProfile)
        .where(WebhookCredentialProfile.user_id == user.id)
    )
    if count >= 100:
        raise HTTPException(
            409,
            "A user may retain at most 100 credential profiles; reuse an existing profile",
        )
    profile = WebhookCredentialProfile(user_id=user.id, revision=1)
    update_credential(profile, payload)
    db.add(profile)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="notifications.credential.create",
        resource_type="webhook_credential_profile",
        resource_id=profile.id,
        metadata={"revision": 1},
    )
    db.commit()
    db.refresh(profile)
    return credential_response(profile)


@router.patch(
    "/credential-profiles/{profile_id}", response_model=WebhookCredentialResponse
)
def update_credential_profile(
    profile_id: uuid.UUID,
    payload: WebhookCredentialWrite,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope=Depends(require_token_scopes("write:notifications")),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    fence_webhook_request(
        db,
        request=request,
        authorization=authorization,
        data_access=data_access,
        permission="write:notifications",
    )
    profile = db.scalar(
        select(WebhookCredentialProfile)
        .where(
            WebhookCredentialProfile.id == profile_id,
            WebhookCredentialProfile.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if profile is None:
        raise HTTPException(404, "Credential profile not found")
    if payload.expected_revision != profile.revision:
        raise HTTPException(409, "Credential profile changed; reload before saving")
    update_credential(profile, payload)
    profile.revision += 1
    record_audit(
        db,
        actor_user_id=user.id,
        action="notifications.credential.update",
        resource_type="webhook_credential_profile",
        resource_id=profile.id,
        metadata={"revision": profile.revision, "enabled": profile.enabled},
    )
    db.commit()
    db.refresh(profile)
    return credential_response(profile)


class WebhookPreviewRequest(BaseModel):
    webhook: NotificationWebhookWrite
    event_id: uuid.UUID


def _require_event_permissions(
    authorization: AuthorizationContext, event_type: str
) -> None:
    permissions = ["read:notifications"]
    if event_type in {"rss_item_new", "alert_match", *AUTOMATION_EVENTS}:
        permissions.append("read:items")
    if event_type == "alert_match":
        permissions.append("read:alerts")
    if event_type == "hunt.approved":
        permissions.extend(("read:teams", "read:ai"))
    if event_type == "report_ready":
        permissions.append("read:reports")
    if event_type == "daily_digest":
        permissions.append("read:ai")
    if not all(authorization.has(permission) for permission in permissions):
        raise HTTPException(
            403, "Current permissions do not allow previewing this event type"
        )


def _event_query(
    *,
    user_id: uuid.UUID,
    event_type: str,
    data_access: DataAccessContext,
    authorization: AuthorizationContext,
):
    query = select(IntegrationEvent).where(
        IntegrationEvent.event_type == event_type,
        func.octet_length(cast(IntegrationEvent.payload_json, String))
        <= MAX_AUTOMATION_BYTES,
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_INTEGRATION_EVENT, IntegrationEvent.id, data_access
        ),
    )
    if event_type in {"alert_match", "report_ready", "webhook_failed"}:
        query = query.where(
            or_(
                IntegrationEvent.payload_json["owner_user_id"].as_string()
                == str(user_id),
                exists(
                    select(IntegrationDelivery.id).where(
                        IntegrationDelivery.event_id == IntegrationEvent.id,
                        IntegrationDelivery.owner_user_id == user_id,
                    )
                ),
            )
        )
    query = query.where(
        or_(
            IntegrationEvent.payload_json["team_id"].as_string().is_(None),
            exists(
                select(Team.id).where(
                    cast(Team.id, String)
                    == IntegrationEvent.payload_json["team_id"].as_string(),
                    team_access_predicate(Team.id, user_id),
                )
            ),
        )
    )
    if not authorization.has("read:teams") or not authorization.has("read:ai"):
        query = query.where(
            IntegrationEvent.payload_json["team_id"].as_string().is_(None)
        )
    return query


@router.get("/webhooks/events")
def list_webhook_events(
    event_type: NotificationEventType,
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    data_access: DataAccessContext = Depends(get_data_access_context),
    _scope=Depends(require_token_scopes("read:notifications")),
):
    _require_event_permissions(authorization, event_type)
    fence_webhook_request(
        db,
        request=request,
        authorization=authorization,
        data_access=data_access,
        permission="read:notifications",
    )
    rows = db.execute(
        _event_query(
            user_id=user.id,
            event_type=event_type,
            data_access=data_access,
            authorization=authorization,
        )
        .with_only_columns(
            IntegrationEvent.id,
            IntegrationEvent.event_type,
            IntegrationEvent.created_at,
        )
        .order_by(IntegrationEvent.created_at.desc(), IntegrationEvent.id.desc())
        .limit(limit)
    ).all()
    return {
        "events": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "created_at": row.created_at,
                "label": f"{row.event_type} · {row.created_at.isoformat()}",
            }
            for row in rows
        ]
    }


@router.post("/webhooks/preview", response_model=WebhookPreviewResponse)
def preview_webhook(
    payload: WebhookPreviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    data_access: DataAccessContext = Depends(get_data_access_context),
    _scope=Depends(require_token_scopes("read:notifications")),
):
    _require_event_permissions(authorization, payload.webhook.event_type)
    fence_webhook_request(
        db,
        request=request,
        authorization=authorization,
        data_access=data_access,
        permission="read:notifications",
    )
    event = db.scalar(
        _event_query(
            user_id=user.id,
            event_type=payload.webhook.event_type,
            data_access=data_access,
            authorization=authorization,
        ).where(IntegrationEvent.id == payload.event_id)
    )
    if event is None:
        raise HTTPException(404, "Event not found or unavailable")
    from app.services.integration_events import delivery_payload_for_owner
    from app.services.integration_connectors.base import IntegrationEventContextError

    try:
        data = delivery_payload_for_owner(event, owner_user_id=user.id)
    except IntegrationEventContextError as exc:
        raise HTTPException(404, "Event not found or unavailable") from exc
    matched, checks, missing = evaluate_conditions(
        payload.webhook.conditions,
        event_condition_values(
            data, created_at=event.created_at, event_type=event.event_type
        ),
    )
    feed_match = (
        payload.webhook.feed_scope == "all"
        or event.event_type in {"report_ready", "daily_digest"}
        or data.get("feed_id") in [str(value) for value in payload.webhook.feed_ids]
    )
    complete = data.get("indicators_complete") is not False
    current = True
    if event.event_type in AUTOMATION_EVENTS:
        from app.services.intel_event_eligibility import automation_event_current

        current = automation_event_current(db, data, event.event_type)
        checks.append(
            WebhookConditionCheck(
                field="current_revision",
                matched=current,
                reason="Event revision is current"
                if current
                else "The evidence or approval changed; this historical action will not be sent",
            )
        )
    checks.extend(
        (
            WebhookConditionCheck(
                field="feed_scope",
                matched=feed_match,
                reason="Feed scope matched"
                if feed_match
                else "Feed is outside selected scope",
            ),
            WebhookConditionCheck(
                field="indicators_complete",
                matched=complete,
                reason="Evidence snapshot complete"
                if complete
                else "Incomplete indicator sets are not delivered automatically",
            ),
        )
    )
    try:
        envelope = (
            automation_envelope(event, payload=data)
            if payload.webhook.payload_mode == "automation_v1"
            else None
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return WebhookPreviewResponse(
        matches=matched and feed_match and complete and current,
        checks=checks,
        missing_fields=missing,
        automation_payload=envelope,
    )
