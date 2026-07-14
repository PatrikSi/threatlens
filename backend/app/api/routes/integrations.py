from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, require_token_scopes
from app.core.token_scopes import SCOPE_READ_INTEGRATIONS, SCOPE_WRITE_INTEGRATIONS
from app.db.session import get_db
from app.models.feed import Feed
from app.models.user import User
from app.schemas.integration import (
    IntegrationConnectorResponse,
    IntegrationSummaryResponse,
    SMTPSettingsResponse,
    SMTPSettingsUpdate,
    SMTPTestRequest,
    SMTPTestResponse,
)
from app.services.audit import record_audit
from app.services.integration_registry import list_integration_connectors
from app.services.integration_storage import (
    SMTPSecretError,
    apply_smtp_settings_update,
    build_active_smtp_settings,
    get_or_create_smtp_integration,
    list_integration_summaries,
    record_smtp_test_result,
    smtp_settings_response_from_model,
)
from app.services.notification_webhooks import find_unknown_template_variables_in_texts
from app.services.smtp_integration import test_smtp_integration

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/connectors", response_model=list[IntegrationConnectorResponse])
def get_integration_connectors(
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_INTEGRATIONS)),
):
    return list_integration_connectors()


@router.get("", response_model=list[IntegrationSummaryResponse])
def list_integrations(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_INTEGRATIONS)),
):
    get_or_create_smtp_integration(db)
    return list_integration_summaries(db)


@router.get("/smtp/settings", response_model=SMTPSettingsResponse)
def get_smtp_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_INTEGRATIONS)),
):
    instance = get_or_create_smtp_integration(db)
    return smtp_settings_response_from_model(instance)


@router.put("/smtp/settings", response_model=SMTPSettingsResponse)
def update_smtp_settings(
    payload: SMTPSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_INTEGRATIONS)),
):
    instance = get_or_create_smtp_integration(db)
    _validate_smtp_notification_settings(db, payload, require_recipients=payload.enabled)
    apply_smtp_settings_update(instance, payload)
    db.add(instance)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="integrations.smtp.update",
        resource_type="integration_instance",
        resource_id=str(instance.id),
        metadata={
            "enabled": payload.enabled,
            "host": payload.host,
            "port": payload.port,
            "security": payload.security,
            "username_configured": bool(payload.username),
            "from_email": str(payload.from_email) if payload.from_email else None,
            "recipient_count": len(payload.to_emails),
            "event_types": payload.event_types,
            "feed_scope": payload.feed_scope,
            "password_action": _password_audit_action(payload),
        },
    )
    db.commit()
    db.refresh(instance)
    return smtp_settings_response_from_model(instance)


@router.post("/smtp/test", response_model=SMTPTestResponse)
def test_smtp_settings(
    payload: SMTPTestRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_INTEGRATIONS)),
):
    instance = get_or_create_smtp_integration(db)
    used_unsaved_settings = payload.settings is not None
    if payload.settings is not None:
        _validate_smtp_notification_settings(db, payload.settings, require_recipients=False)
    try:
        active_settings = build_active_smtp_settings(instance, override=payload.settings)
    except SMTPSecretError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    result = test_smtp_integration(
        active_settings,
        recipient_email=str(payload.recipient_email) if payload.send_email and payload.recipient_email else None,
    ).model_copy(update={"used_unsaved_settings": used_unsaved_settings})
    record_smtp_test_result(
        db,
        instance=instance,
        result=result,
        used_unsaved_settings=used_unsaved_settings,
    )
    record_audit(
        db,
        actor_user_id=admin.id,
        action="integrations.smtp.test",
        resource_type="integration_instance",
        resource_id=str(instance.id),
        success=result.success,
        metadata={
            "action": result.action,
            "error_code": result.error_code,
            "recipient_provided": bool(payload.send_email and payload.recipient_email),
            "used_unsaved_settings": used_unsaved_settings,
        },
    )
    db.commit()
    return result


def _password_audit_action(payload: SMTPSettingsUpdate) -> str:
    if payload.password is not None:
        return "updated"
    if payload.clear_password:
        return "cleared"
    return "preserved"


def _validate_smtp_notification_settings(
    db: Session,
    payload: SMTPSettingsUpdate,
    *,
    require_recipients: bool,
) -> None:
    if require_recipients and not payload.to_emails:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one recipient email is required when SMTP is enabled",
        )

    if payload.feed_scope == "selected":
        known_feed_ids = set(db.scalars(select(Feed.id).where(Feed.id.in_(payload.feed_ids))).all())
        invalid_feed_ids = sorted(str(feed_id) for feed_id in payload.feed_ids if feed_id not in known_feed_ids)
        if invalid_feed_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown feed ids: {', '.join(invalid_feed_ids)}",
            )

    unknown_variables = sorted(
        find_unknown_template_variables_in_texts([payload.subject_template, payload.html_template])
    )
    if unknown_variables:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown template variable(s): {', '.join(unknown_variables)}",
        )
