from __future__ import annotations

import uuid
from collections.abc import Collection

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_data_access_context, require_permissions
from app.core.token_scopes import SCOPE_READ_INTEGRATIONS, SCOPE_WRITE_INTEGRATIONS
from app.db.session import get_db
from app.models.feed import Feed
from app.models.integration import IntegrationDelivery, IntegrationInstance
from app.models.user import User
from app.schemas.integration import (
    IntegrationConnectorResponse,
    IntegrationDeliveryReplayResponse,
    IntegrationSummaryResponse,
    SMTPAnalyticsResponse,
    SMTPDeliveryListResponse,
    SMTPHookResponse,
    SMTPHookTestRequest,
    SMTPHookWrite,
    SMTPSettingsResponse,
    SMTPSettingsUpdate,
    SMTPTemplateDefaultResponse,
    SMTPTestRequest,
    SMTPTestResponse,
    SMTPTestRunListResponse,
)
from app.services.audit import record_audit
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import (
    DataAccessContext,
    fence_data_access_context,
)
from app.services.data_policy_audit import record_data_policy_decision
from app.services.integration_delivery_policy_history import (
    integration_delivery_would_deny_summary,
)
from app.services.integration_metric_data_policy import (
    integration_metric_would_deny_summary,
)
from app.services.integration_registry import list_integration_connectors
from app.services.integration_delivery import replay_dead_letter_delivery
from app.services.integration_smtp_hooks import (
    SMTPHookConflictError,
    SMTPHookNotFoundError,
    SMTPHookValidationError,
    archive_smtp_hook,
    create_smtp_hook,
    get_smtp_analytics,
    get_smtp_hook,
    list_smtp_deliveries,
    list_smtp_hooks,
    list_smtp_test_runs,
    list_smtp_template_defaults,
    smtp_hook_response,
    update_smtp_hook,
    validate_smtp_hook_credential_selection,
)
from app.services.integration_storage import (
    SMTPSecretError,
    apply_smtp_settings_update,
    build_active_smtp_settings,
    get_smtp_credential_source,
    get_or_create_smtp_integration,
    get_or_create_persisted_smtp_integration,
    list_integration_summaries,
    lock_smtp_configuration,
    record_smtp_test_result,
    smtp_settings_response_from_model,
    sync_smtp_subscriptions,
)
from app.services.notification_webhooks import find_unknown_template_variables_in_texts
from app.services.smtp_integration import test_smtp_integration
from app.tasks.feed_tasks import enqueue_integration_delivery_processing

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/connectors", response_model=list[IntegrationConnectorResponse])
def get_integration_connectors(
    _reader: User = Depends(require_permissions(SCOPE_READ_INTEGRATIONS)),
):
    return list_integration_connectors()


@router.get("", response_model=list[IntegrationSummaryResponse])
def list_integrations(
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_INTEGRATIONS)),
):
    get_or_create_persisted_smtp_integration(db)
    return list_integration_summaries(db)


@router.get("/smtp/settings", response_model=SMTPSettingsResponse)
def get_smtp_settings(
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    instance = get_or_create_smtp_integration(db)
    response = smtp_settings_response_from_model(
        instance,
        accessible_feed_ids=_smtp_response_feed_ids(db, data_access=data_access),
    )
    _record_smtp_selected_feed_would_deny(
        db,
        data_access=data_access,
        feed_ids=response.feed_ids,
        surface="integrations.smtp.settings.read",
        resource_id=instance.id,
    )
    # Persist first-run initialization only after every policy-sensitive read
    # and the response snapshot have completed under the request fence.
    db.commit()
    fence_data_access_context(db, data_access)
    return response


@router.get("/smtp/hooks", response_model=list[SMTPHookResponse])
def get_smtp_hooks(
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    get_or_create_smtp_integration(db)
    response = list_smtp_hooks(
        db,
        accessible_feed_ids=_smtp_response_feed_ids(db, data_access=data_access),
    )
    _record_smtp_selected_feed_would_deny(
        db,
        data_access=data_access,
        feed_ids={feed_id for hook in response for feed_id in hook.feed_ids},
        surface="integrations.smtp.hooks.read",
    )
    db.commit()
    fence_data_access_context(db, data_access)
    return response


@router.post(
    "/smtp/hooks", response_model=SMTPHookResponse, status_code=status.HTTP_201_CREATED
)
def create_smtp_hook_route(
    payload: SMTPHookWrite,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    _fence_smtp_selected_feed_context(
        db, payload=payload.settings, data_access=data_access
    )
    lock_smtp_configuration(db)
    _validate_smtp_notification_settings(
        db,
        payload.settings,
        require_recipients=payload.settings.enabled,
        allow_shared_host=payload.credential_source_id is not None,
        data_access=data_access,
        audit_surface="integrations.smtp.hook.create",
    )
    try:
        instance = create_smtp_hook(db, payload)
    except (
        SMTPHookConflictError,
        SMTPHookNotFoundError,
        SMTPHookValidationError,
    ) as exc:
        raise _smtp_hook_http_error(exc) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="integrations.smtp.hook.create",
        resource_type="integration_instance",
        resource_id=str(instance.id),
        metadata=_smtp_hook_audit_metadata(payload),
    )
    db.flush()
    response = smtp_hook_response(
        db,
        instance,
        accessible_feed_ids=_smtp_response_feed_ids(db, data_access=data_access),
    )
    db.commit()
    fence_data_access_context(db, data_access)
    return response


@router.patch("/smtp/hooks/{hook_id}", response_model=SMTPHookResponse)
def update_smtp_hook_route(
    hook_id: uuid.UUID,
    payload: SMTPHookWrite,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    _fence_smtp_selected_feed_context(
        db, payload=payload.settings, data_access=data_access
    )
    lock_smtp_configuration(db)
    try:
        instance = get_smtp_hook(db, hook_id, for_update=True)
    except SMTPHookNotFoundError as exc:
        raise _smtp_hook_http_error(exc) from exc
    _validate_smtp_notification_settings(
        db,
        payload.settings,
        require_recipients=payload.settings.enabled,
        allow_shared_host=payload.credential_source_id is not None,
        data_access=data_access,
        audit_surface="integrations.smtp.hook.update",
        audit_resource_id=instance.id,
    )
    try:
        update_smtp_hook(db, instance, payload)
    except (
        SMTPHookConflictError,
        SMTPHookNotFoundError,
        SMTPHookValidationError,
    ) as exc:
        raise _smtp_hook_http_error(exc) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="integrations.smtp.hook.update",
        resource_type="integration_instance",
        resource_id=str(instance.id),
        metadata=_smtp_hook_audit_metadata(payload),
    )
    db.flush()
    response = smtp_hook_response(
        db,
        instance,
        accessible_feed_ids=_smtp_response_feed_ids(db, data_access=data_access),
    )
    db.commit()
    fence_data_access_context(db, data_access)
    return response


@router.delete("/smtp/hooks/{hook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_smtp_hook_route(
    hook_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_INTEGRATIONS)),
):
    lock_smtp_configuration(db)
    try:
        instance = get_smtp_hook(db, hook_id, for_update=True)
        archive_smtp_hook(db, instance)
    except (SMTPHookConflictError, SMTPHookNotFoundError) as exc:
        raise _smtp_hook_http_error(exc) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="integrations.smtp.hook.delete",
        resource_type="integration_instance",
        resource_id=str(instance.id),
        metadata={"name": instance.name},
    )
    db.commit()


@router.get("/smtp/template-defaults", response_model=list[SMTPTemplateDefaultResponse])
def get_smtp_template_defaults(
    _reader: User = Depends(require_permissions(SCOPE_READ_INTEGRATIONS)),
):
    return list_smtp_template_defaults()


@router.get("/smtp/analytics", response_model=SMTPAnalyticsResponse)
def get_smtp_analytics_route(
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    fence_data_access_context(db, data_access)
    get_or_create_smtp_integration(db)
    response = get_smtp_analytics(db, data_access=data_access)
    _record_integration_delivery_history_would_deny(
        db,
        data_access=data_access,
        connector_type="smtp",
        surface="integrations.smtp.analytics.read",
        resource_type="integration_delivery",
    )
    _record_smtp_metric_history_would_deny(db, data_access=data_access)
    db.commit()
    fence_data_access_context(db, data_access)
    return response


@router.get("/smtp/hooks/{hook_id}/deliveries", response_model=SMTPDeliveryListResponse)
def get_smtp_hook_deliveries(
    hook_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    fence_data_access_context(db, data_access)
    try:
        instance = get_smtp_hook(db, hook_id)
    except SMTPHookNotFoundError as exc:
        raise _smtp_hook_http_error(exc) from exc
    response = list_smtp_deliveries(
        db,
        instance=instance,
        page=page,
        page_size=page_size,
        data_access=data_access,
    )
    _record_integration_delivery_history_would_deny(
        db,
        data_access=data_access,
        connector_type="smtp",
        integration_id=instance.id,
        surface="integrations.smtp.deliveries.read",
        resource_type="integration_instance",
        resource_id=instance.id,
    )
    db.commit()
    fence_data_access_context(db, data_access)
    return response


@router.get("/smtp/hooks/{hook_id}/test-runs", response_model=SMTPTestRunListResponse)
def get_smtp_hook_test_runs(
    hook_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_INTEGRATIONS)),
):
    try:
        instance = get_smtp_hook(db, hook_id)
    except SMTPHookNotFoundError as exc:
        raise _smtp_hook_http_error(exc) from exc
    return list_smtp_test_runs(db, instance=instance, page=page, page_size=page_size)


@router.post(
    "/smtp/hooks/{hook_id}/deliveries/{delivery_id}/replay",
    response_model=IntegrationDeliveryReplayResponse,
)
def replay_smtp_hook_delivery(
    hook_id: uuid.UUID,
    delivery_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    fence_data_access_context(db, data_access)
    try:
        get_smtp_hook(db, hook_id)
    except SMTPHookNotFoundError as exc:
        raise _smtp_hook_http_error(exc) from exc
    delivery = db.scalar(
        select(IntegrationDelivery).where(
            IntegrationDelivery.id == delivery_id,
            IntegrationDelivery.integration_id == hook_id,
            IntegrationDelivery.connector_type == "smtp",
            data_access_envelope_predicate(
                DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
                IntegrationDelivery.id,
                data_access,
            ),
        )
    )
    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="SMTP delivery not found"
        )
    _record_integration_delivery_history_would_deny(
        db,
        data_access=data_access,
        connector_type="smtp",
        delivery_id=delivery.id,
        surface="integrations.smtp.delivery.replay",
        resource_type="integration_delivery",
        resource_id=delivery.id,
    )
    try:
        replay = replay_dead_letter_delivery(db, delivery_id=delivery.id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="integrations.smtp.delivery.replay",
        resource_type="integration_delivery",
        resource_id=str(replay.id),
        metadata={"source_delivery_id": str(delivery.id), "hook_id": str(hook_id)},
    )
    db.commit()
    queued = enqueue_integration_delivery_processing([replay.id])
    return IntegrationDeliveryReplayResponse(
        source_delivery_id=delivery.id,
        delivery_id=replay.id,
        state="pending",
        queued=queued,
    )


@router.post("/smtp/hooks/test", response_model=SMTPTestResponse)
def test_smtp_hook(
    payload: SMTPHookTestRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    instance = None
    if payload.hook_id is not None:
        try:
            instance = get_smtp_hook(db, payload.hook_id)
        except SMTPHookNotFoundError as exc:
            raise _smtp_hook_http_error(exc) from exc
    used_unsaved_settings = payload.hook is not None
    try:
        if payload.hook is None:
            if instance is None:
                raise SMTPHookNotFoundError("SMTP hook not found")
            credential_source = get_smtp_credential_source(db, instance)
            active_settings = build_active_smtp_settings(
                instance, credential_source=credential_source
            )
        else:
            _validate_smtp_notification_settings(
                db,
                payload.hook.settings,
                require_recipients=False,
                allow_shared_host=payload.hook.credential_source_id is not None,
                data_access=data_access,
                audit_surface="integrations.smtp.hook.test_unsaved",
                audit_resource_id=instance.id if instance is not None else None,
            )
            credential_source = validate_smtp_hook_credential_selection(
                db,
                payload=payload.hook,
                target=instance,
            )
            runtime_instance = instance or IntegrationInstance(
                id=uuid.uuid4(),
                name=payload.hook.name,
                integration_type="smtp",
                direction="destination",
                enabled=False,
                config_json={},
            )
            active_settings = build_active_smtp_settings(
                runtime_instance,
                override=payload.hook.settings,
                credential_source=credential_source,
            )
    except (SMTPHookConflictError, SMTPHookNotFoundError) as exc:
        raise _smtp_hook_http_error(exc) from exc
    except SMTPSecretError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    result = test_smtp_integration(
        active_settings,
        recipient_email=str(payload.recipient_email)
        if payload.send_email and payload.recipient_email
        else None,
    ).model_copy(update={"used_unsaved_settings": used_unsaved_settings})
    run = None
    if instance is not None:
        run = record_smtp_test_result(
            db,
            instance=instance,
            result=result,
            used_unsaved_settings=used_unsaved_settings,
        )
    audit_metadata = _smtp_test_audit_metadata(
        result,
        run_id=run.id if run is not None else None,
        recipient_provided=bool(payload.send_email and payload.recipient_email),
        used_unsaved_settings=used_unsaved_settings,
    )
    audit_metadata["used_shared_credentials"] = credential_source is not None
    record_audit(
        db,
        actor_user_id=actor.id,
        action="integrations.smtp.hook.test",
        resource_type="integration_instance",
        resource_id=str(instance.id) if instance is not None else "unsaved",
        success=result.success,
        metadata=audit_metadata,
    )
    db.commit()
    return result


@router.put("/smtp/settings", response_model=SMTPSettingsResponse)
def update_smtp_settings(
    payload: SMTPSettingsUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    _fence_smtp_selected_feed_context(db, payload=payload, data_access=data_access)
    instance = lock_smtp_configuration(db)
    _validate_smtp_notification_settings(
        db,
        payload,
        require_recipients=payload.enabled,
        data_access=data_access,
        audit_surface="integrations.smtp.settings.update",
        audit_resource_id=instance.id,
    )
    apply_smtp_settings_update(instance, payload)
    db.add(instance)
    sync_smtp_subscriptions(db, instance)
    record_audit(
        db,
        actor_user_id=actor.id,
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
    db.flush()
    response = smtp_settings_response_from_model(
        instance,
        accessible_feed_ids=_smtp_response_feed_ids(db, data_access=data_access),
    )
    db.commit()
    fence_data_access_context(db, data_access)
    return response


@router.post("/smtp/test", response_model=SMTPTestResponse)
def test_smtp_settings(
    payload: SMTPTestRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    instance = get_or_create_smtp_integration(db)
    used_unsaved_settings = payload.settings is not None
    if payload.settings is not None:
        _validate_smtp_notification_settings(
            db,
            payload.settings,
            require_recipients=False,
            data_access=data_access,
            audit_surface="integrations.smtp.settings.test_unsaved",
            audit_resource_id=instance.id,
        )
    try:
        active_settings = build_active_smtp_settings(
            instance, override=payload.settings
        )
    except SMTPSecretError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    result = test_smtp_integration(
        active_settings,
        recipient_email=str(payload.recipient_email)
        if payload.send_email and payload.recipient_email
        else None,
    ).model_copy(update={"used_unsaved_settings": used_unsaved_settings})
    run = record_smtp_test_result(
        db,
        instance=instance,
        result=result,
        used_unsaved_settings=used_unsaved_settings,
    )
    record_audit(
        db,
        actor_user_id=actor.id,
        action="integrations.smtp.test",
        resource_type="integration_instance",
        resource_id=str(instance.id),
        success=result.success,
        metadata=_smtp_test_audit_metadata(
            result,
            run_id=run.id,
            recipient_provided=bool(payload.send_email and payload.recipient_email),
            used_unsaved_settings=used_unsaved_settings,
        ),
    )
    db.commit()
    return result


@router.post(
    "/deliveries/{delivery_id}/replay",
    response_model=IntegrationDeliveryReplayResponse,
)
def replay_integration_delivery(
    delivery_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_INTEGRATIONS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    fence_data_access_context(db, data_access)
    visible_delivery_id = db.scalar(
        select(IntegrationDelivery.id).where(
            IntegrationDelivery.id == delivery_id,
            data_access_envelope_predicate(
                DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
                IntegrationDelivery.id,
                data_access,
            ),
        )
    )
    if visible_delivery_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration delivery not found",
        )
    _record_integration_delivery_history_would_deny(
        db,
        data_access=data_access,
        delivery_id=visible_delivery_id,
        surface="integrations.delivery.replay",
        resource_type="integration_delivery",
        resource_id=visible_delivery_id,
    )
    try:
        replay = replay_dead_letter_delivery(db, delivery_id=delivery_id)
    except ValueError as exc:
        message = str(exc)
        status_code = (
            status.HTTP_404_NOT_FOUND
            if message == "Integration delivery not found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=status_code, detail=message) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="integrations.delivery.replay",
        resource_type="integration_delivery",
        resource_id=str(replay.id),
        metadata={
            "source_delivery_id": str(delivery_id),
            "connector_type": replay.connector_type,
        },
    )
    db.commit()
    queued = enqueue_integration_delivery_processing([replay.id])
    return IntegrationDeliveryReplayResponse(
        source_delivery_id=delivery_id,
        delivery_id=replay.id,
        state="pending",
        queued=queued,
    )


def _password_audit_action(payload: SMTPSettingsUpdate) -> str:
    if payload.password is not None:
        return "updated"
    if payload.clear_password:
        return "cleared"
    return "preserved"


def _smtp_test_audit_metadata(
    result: SMTPTestResponse,
    *,
    run_id: uuid.UUID | None,
    recipient_provided: bool,
    used_unsaved_settings: bool,
) -> dict:
    return {
        "run_id": str(run_id) if run_id is not None else None,
        "action": result.action,
        "duration_ms": result.duration_ms,
        "error_code": result.error_code,
        "error_message": result.error,
        "server_message": result.server_message[:4000]
        if result.server_message
        else None,
        "recipient_provided": recipient_provided,
        "used_unsaved_settings": used_unsaved_settings,
    }


def _smtp_hook_http_error(
    exc: SMTPHookConflictError | SMTPHookNotFoundError | SMTPHookValidationError,
) -> HTTPException:
    if isinstance(exc, SMTPHookNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, SMTPHookValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(status_code=status_code, detail=str(exc))


def _smtp_hook_audit_metadata(payload: SMTPHookWrite) -> dict:
    settings = payload.settings
    return {
        "name": payload.name,
        "enabled": settings.enabled,
        "host": settings.host if payload.credential_source_id is None else None,
        "port": settings.port if payload.credential_source_id is None else None,
        "security": settings.security if payload.credential_source_id is None else None,
        "username_configured": bool(settings.username)
        if payload.credential_source_id is None
        else None,
        "from_email": str(settings.from_email) if settings.from_email else None,
        "recipient_count": len(settings.to_emails),
        "event_types": settings.event_types,
        "feed_scope": settings.feed_scope,
        "credential_source_id": str(payload.credential_source_id)
        if payload.credential_source_id
        else None,
        "password_action": "shared"
        if payload.credential_source_id
        else _password_audit_action(settings),
    }


def _record_integration_delivery_history_would_deny(
    db: Session,
    *,
    data_access: DataAccessContext,
    surface: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    connector_type: str | None = None,
    integration_id: uuid.UUID | None = None,
    delivery_id: uuid.UUID | None = None,
) -> None:
    summary = integration_delivery_would_deny_summary(
        db,
        data_access=data_access,
        connector_type=connector_type,
        integration_id=integration_id,
        delivery_id=delivery_id,
    )
    if not summary.affected_count:
        return
    record_data_policy_decision(
        db,
        context=data_access,
        decision="would_deny",
        resource_type=resource_type,
        resource_id=resource_id,
        surface=surface,
        handling_label_ids=summary.handling_label_ids,
        affected_count=summary.affected_count,
        metadata_extra={
            "connector_type": connector_type or "all",
            "history_scope": "delivery_id"
            if delivery_id is not None
            else "integration_id"
            if integration_id is not None
            else "connector",
        },
    )


def _record_smtp_metric_history_would_deny(
    db: Session,
    *,
    data_access: DataAccessContext,
) -> None:
    summary = integration_metric_would_deny_summary(
        db,
        data_access=data_access,
        connector_type="smtp",
    )
    if not summary.affected_count:
        return
    record_data_policy_decision(
        db,
        context=data_access,
        decision="would_deny",
        resource_type="integration_delivery_metric",
        surface="integrations.smtp.analytics.read",
        handling_label_ids=summary.handling_label_ids,
        affected_count=summary.affected_count,
        metadata_extra={
            "connector_type": "smtp",
            "history_scope": "metric_cohort",
        },
    )


def _validate_smtp_notification_settings(
    db: Session,
    payload: SMTPSettingsUpdate,
    *,
    require_recipients: bool,
    allow_shared_host: bool = False,
    data_access: DataAccessContext,
    audit_surface: str,
    audit_resource_id: uuid.UUID | None = None,
) -> None:
    if payload.enabled and not payload.host and not allow_shared_host:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="SMTP host is required when SMTP is enabled",
        )
    if require_recipients and not payload.to_emails:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least one recipient email is required when SMTP is enabled",
        )

    if payload.feed_scope == "selected":
        fence_data_access_context(db, data_access)
        feed_rows = db.execute(
            select(Feed.id, Feed.handling_label_id).where(
                Feed.id.in_(payload.feed_ids)
            )
        ).all()
        labels_by_feed = {row.id: row.handling_label_id for row in feed_rows}
        missing_feed_ids = [
            feed_id for feed_id in payload.feed_ids if feed_id not in labels_by_feed
        ]
        restricted_feed_ids = [
            feed_id
            for feed_id, label_id in labels_by_feed.items()
            if not data_access.principal_eligible
            or label_id not in data_access.allowed_label_ids
        ]
        if data_access.mode == "disabled" and missing_feed_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Unknown feed ids: "
                    + ", ".join(sorted(str(feed_id) for feed_id in missing_feed_ids))
                ),
            )
        if data_access.mode in {"audit", "enforced"} and (
            missing_feed_ids
            or (
                (data_access.enforced or not data_access.principal_eligible)
                and restricted_feed_ids
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="One or more selected feeds are unavailable",
            )
        _record_smtp_selected_feed_would_deny(
            db,
            data_access=data_access,
            feed_ids=restricted_feed_ids,
            surface=audit_surface,
            resource_id=audit_resource_id,
        )

    unknown_variables = sorted(
        find_unknown_template_variables_in_texts(
            [payload.subject_template, payload.html_template]
        )
    )
    if unknown_variables:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown template variable(s): {', '.join(unknown_variables)}",
        )


def _smtp_response_feed_ids(
    db: Session,
    *,
    data_access: DataAccessContext,
) -> set[uuid.UUID] | None:
    fence_data_access_context(db, data_access)
    if data_access.mode == "disabled":
        return None
    if data_access.auditing and data_access.principal_eligible:
        return None
    if not data_access.principal_eligible:
        return set()
    return set(
        db.scalars(
            select(Feed.id).where(
                Feed.handling_label_id.in_(data_access.allowed_label_ids)
            )
        ).all()
    )


def _fence_smtp_selected_feed_context(
    db: Session,
    *,
    payload: SMTPSettingsUpdate,
    data_access: DataAccessContext,
) -> None:
    if payload.feed_scope == "selected":
        fence_data_access_context(db, data_access)


def _record_smtp_selected_feed_would_deny(
    db: Session,
    *,
    data_access: DataAccessContext,
    feed_ids: Collection[uuid.UUID],
    surface: str,
    resource_id: uuid.UUID | None = None,
) -> None:
    if not data_access.auditing:
        return
    selected_feed_ids = set(feed_ids)
    if not selected_feed_ids:
        return
    rows = db.execute(
        select(Feed.id, Feed.handling_label_id).where(Feed.id.in_(selected_feed_ids))
    ).all()
    known_feed_ids = {row.id for row in rows}
    missing_count = len(selected_feed_ids - known_feed_ids)
    denied_rows = [
        row
        for row in rows
        if not data_access.principal_eligible
        or row.handling_label_id not in data_access.allowed_label_ids
    ]
    affected_count = len({row.id for row in denied_rows}) + missing_count
    if not affected_count:
        return
    record_data_policy_decision(
        db,
        context=data_access,
        decision="would_deny",
        resource_type="integration_instance",
        resource_id=resource_id,
        surface=surface,
        handling_label_ids={row.handling_label_id for row in denied_rows},
        affected_count=affected_count,
        metadata_extra={
            "configuration_kind": "smtp_selected_feeds",
            "unresolved_reference_count": missing_count,
        },
    )
