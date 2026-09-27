"""Team ownership is independent from the current delivery policy custodian."""

import uuid
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from app.models.automation_execution import AutomationExecution
from app.models.notification_webhook import NotificationWebhook
from app.models.webhook_credential import WebhookCredentialProfile
from app.services.integration_compat import ensure_webhook_integration
from app.services.notification_webhook_storage import (
    notification_webhook_write_from_model,
)
from app.services.notification_webhook_validation import (
    validate_notification_webhook_payload_for_actor,
)
from app.services.team_access import assert_current_team_access


def team_destination(
    db: Session, team_id: uuid.UUID, webhook_id: uuid.UUID, *, lock: bool = False
) -> NotificationWebhook:
    query = select(NotificationWebhook).where(
        NotificationWebhook.id == webhook_id, NotificationWebhook.team_id == team_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Team integration not found")
    return row


def adopt_destination(
    db: Session,
    *,
    row: NotificationWebhook,
    team_id: uuid.UUID,
    actor,
    available_feed_ids: set[uuid.UUID],
    expected_revision: int,
) -> None:
    if row.ownership_revision != expected_revision:
        raise HTTPException(
            409, "Integration ownership changed; reload before adopting"
        )
    if row.team_id is None and row.user_id != actor.id:
        raise HTTPException(404, "Personal integration not found")
    if row.team_id is not None and row.team_id != team_id:
        raise HTTPException(
            409, "Move integrations between teams through a new reviewed destination"
        )
    assert_current_team_access(db, team_id=team_id, user_id=actor.id, manage=True)
    payload = notification_webhook_write_from_model(row)
    try:
        validate_notification_webhook_payload_for_actor(
            payload, available_feed_ids, actor_user=actor
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # A private clone prevents future personal-profile edits/deletion affecting
    # the team's secret and avoids transferring another personal destination.
    if row.credential_profile_id:
        profile = db.scalar(
            select(WebhookCredentialProfile)
            .where(WebhookCredentialProfile.id == row.credential_profile_id)
            .with_for_update(read=True)
        )
        if profile is None:
            raise HTTPException(409, "Integration credential profile is unavailable")
        clone = WebhookCredentialProfile(
            user_id=actor.id,
            name=f"Team destination: {row.name}"[:255],
            enabled=profile.enabled,
            auth_type=profile.auth_type,
            header_name=profile.header_name,
            auth_secret_encrypted=profile.auth_secret_encrypted,
            signing_secret_encrypted=profile.signing_secret_encrypted,
            revision=profile.revision,
        )
        db.add(clone)
        db.flush()
        row.credential_profile_id = clone.id
    if row.team_id is None:
        retained_count = db.scalar(
            select(func.count())
            .select_from(AutomationExecution)
            .where(AutomationExecution.webhook_id == row.id)
        )
        if retained_count > 5000:
            raise HTTPException(
                409,
                "This destination has more than 5,000 retained executions; create a new team destination and retain the old receiver for withdrawals",
            )
        db.execute(
            update(AutomationExecution)
            .where(AutomationExecution.webhook_id == row.id)
            .values(team_id=team_id)
        )
    row.team_id, row.user_id = team_id, actor.id
    row.ownership_revision += 1
    # Existing receipts retain their original evidence authority. Adoption does
    # not reauthorize old actions or resurrect withdrawn intelligence.
    ensure_webhook_integration(db, row)


def destination_summary(row: NotificationWebhook) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "team_id": row.team_id,
        "custodian_user_id": row.user_id,
        "enabled": row.enabled,
        "ownership_revision": row.ownership_revision,
        "event_type": row.event_type,
        "payload_mode": row.payload_mode,
        "updated_at": row.updated_at,
    }
