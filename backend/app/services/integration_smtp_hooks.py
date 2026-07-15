from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationDeliveryMetric,
    IntegrationInstance,
)
from app.schemas.integration import (
    SMTPAnalyticsEventSummary,
    SMTPAnalyticsHookSummary,
    SMTPAnalyticsResponse,
    SMTPDeliveryAttemptResponse,
    SMTPDeliveryListResponse,
    SMTPDeliveryResponse,
    SMTPHookResponse,
    SMTPHookWrite,
    SMTPTemplateDefaultResponse,
    SMTP_TEMPLATE_DEFAULTS,
)
from app.services.integration_registry import SMTP_CONFIG_SCHEMA_VERSION
from app.services.integration_storage import (
    INTEGRATION_DIRECTION_DESTINATION,
    INTEGRATION_HEALTH_UNKNOWN,
    SMTP_INTEGRATION_TYPE,
    SMTP_SYSTEM_KEY,
    SMTPSecretError,
    apply_smtp_hook_settings_update,
    get_smtp_credential_source,
    read_smtp_secret_config,
    smtp_instance_is_archived,
    smtp_settings_response_from_model,
    sync_smtp_subscriptions,
)

SMTP_TERMINAL_STATES = ("succeeded", "failed", "dead_letter")
SMTP_FAILURE_STATES = ("failed", "dead_letter")


class SMTPHookNotFoundError(ValueError):
    pass


class SMTPHookConflictError(ValueError):
    pass


def list_smtp_hooks(db: Session) -> list[SMTPHookResponse]:
    instances = db.scalars(
        select(IntegrationInstance)
        .where(IntegrationInstance.integration_type == SMTP_INTEGRATION_TYPE)
        .order_by(IntegrationInstance.created_at.asc(), IntegrationInstance.id.asc())
    ).all()
    return [smtp_hook_response(db, instance) for instance in instances if not smtp_instance_is_archived(instance)]


def get_smtp_hook(db: Session, hook_id: uuid.UUID) -> IntegrationInstance:
    instance = db.scalar(
        select(IntegrationInstance).where(
            IntegrationInstance.id == hook_id,
            IntegrationInstance.integration_type == SMTP_INTEGRATION_TYPE,
        )
    )
    if instance is None or smtp_instance_is_archived(instance):
        raise SMTPHookNotFoundError("SMTP hook not found")
    return instance


def create_smtp_hook(db: Session, payload: SMTPHookWrite) -> IntegrationInstance:
    _validate_unique_name(db, payload.name)
    source = validate_smtp_hook_credential_selection(db, payload=payload)
    instance = IntegrationInstance(
        name=payload.name,
        integration_type=SMTP_INTEGRATION_TYPE,
        direction=INTEGRATION_DIRECTION_DESTINATION,
        enabled=False,
        schema_version=SMTP_CONFIG_SCHEMA_VERSION,
        config_json={},
        health_status=INTEGRATION_HEALTH_UNKNOWN,
    )
    db.add(instance)
    db.flush()
    apply_smtp_hook_settings_update(
        instance,
        payload.settings,
        name=payload.name,
        credential_source=source,
    )
    db.add(instance)
    sync_smtp_subscriptions(db, instance)
    return instance


def update_smtp_hook(db: Session, instance: IntegrationInstance, payload: SMTPHookWrite) -> None:
    _validate_unique_name(db, payload.name, excluding_id=instance.id)
    source = validate_smtp_hook_credential_selection(db, payload=payload, target=instance)
    if source is not None and _active_credential_dependents(db, instance.id):
        raise SMTPHookConflictError(
            "This SMTP hook supplies credentials to other hooks and cannot reuse another hook's credentials."
        )
    apply_smtp_hook_settings_update(
        instance,
        payload.settings,
        name=payload.name,
        credential_source=source,
    )
    db.add(instance)
    sync_smtp_subscriptions(db, instance)


def archive_smtp_hook(db: Session, instance: IntegrationInstance) -> None:
    if instance.system_key == SMTP_SYSTEM_KEY:
        raise SMTPHookConflictError("The default SMTP hook cannot be deleted. Disable it instead.")
    dependents = _active_credential_dependents(db, instance.id)
    if dependents:
        names = ", ".join(sorted(dependent.name for dependent in dependents)[:3])
        suffix = "" if len(dependents) <= 3 else f" and {len(dependents) - 3} more"
        raise SMTPHookConflictError(
            f"SMTP credentials are still used by {names}{suffix}. Choose new credentials for those hooks first."
        )
    config = dict(instance.config_json) if isinstance(instance.config_json, dict) else {}
    config["archived_at"] = datetime.now(timezone.utc).isoformat()
    instance.config_json = config
    instance.enabled = False
    instance.credential_source_integration_id = None
    instance.secret_json = None
    db.add(instance)
    sync_smtp_subscriptions(db, instance)


def validate_smtp_credential_source(
    db: Session,
    *,
    source_id: uuid.UUID | None,
    target: IntegrationInstance | None = None,
) -> IntegrationInstance | None:
    if source_id is None:
        return None
    if target is not None and source_id == target.id:
        raise SMTPHookConflictError("An SMTP hook cannot reuse its own credentials.")
    source = db.get(IntegrationInstance, source_id)
    if source is None or source.integration_type != SMTP_INTEGRATION_TYPE or smtp_instance_is_archived(source):
        raise SMTPHookNotFoundError("The selected SMTP credential source was not found.")
    if source.credential_source_integration_id is not None:
        raise SMTPHookConflictError(
            "The selected SMTP hook already uses shared credentials. Choose a hook with its own credentials."
        )
    source_config = source.config_json if isinstance(source.config_json, dict) else {}
    if not source_config.get("host"):
        raise SMTPHookConflictError("The selected SMTP credential source does not have a server host configured.")
    _, secret_error = read_smtp_secret_config(source)
    if secret_error:
        raise SMTPHookConflictError(secret_error)
    return source


def validate_smtp_hook_credential_selection(
    db: Session,
    *,
    payload: SMTPHookWrite,
    target: IntegrationInstance | None = None,
) -> IntegrationInstance | None:
    source = validate_smtp_credential_source(
        db,
        source_id=payload.credential_source_id,
        target=target,
    )
    _validate_shared_credential_payload(payload, source=source)
    return source


def smtp_hook_response(db: Session, instance: IntegrationInstance) -> SMTPHookResponse:
    source = None
    source_error = None
    try:
        source = get_smtp_credential_source(db, instance)
    except SMTPSecretError as exc:
        source_error = str(exc)
    settings = smtp_settings_response_from_model(instance, credential_source=source)
    values = settings.model_dump()
    if source_error:
        values.update(configured=False, health_status="error", last_error=source_error)
    source_id = instance.credential_source_integration_id
    unresolved_source = db.get(IntegrationInstance, source_id) if source_id and source is None else None
    return SMTPHookResponse(
        **values,
        is_default=instance.system_key == SMTP_SYSTEM_KEY,
        uses_shared_credentials=source_id is not None,
        credential_source_id=source_id,
        credential_source_name=(source or unresolved_source).name if (source or unresolved_source) is not None else None,
    )


def list_smtp_template_defaults() -> list[SMTPTemplateDefaultResponse]:
    order = ("rss_item_new", "alert_match", "feed_failing", "webhook_failed", "daily_digest", "all")
    return [
        SMTPTemplateDefaultResponse(
            send_for=send_for,
            event_types=SMTP_TEMPLATE_DEFAULTS[send_for][0],
            subject_template=SMTP_TEMPLATE_DEFAULTS[send_for][1],
            html_template=SMTP_TEMPLATE_DEFAULTS[send_for][2],
        )
        for send_for in order
    ]


def get_smtp_analytics(db: Session) -> SMTPAnalyticsResponse:
    active_hooks = [
        instance
        for instance in db.scalars(
            select(IntegrationInstance).where(IntegrationInstance.integration_type == SMTP_INTEGRATION_TYPE)
        ).all()
        if not smtp_instance_is_archived(instance)
    ]
    metric_rows = db.execute(
        select(
            IntegrationDeliveryMetric.event_type,
            func.sum(IntegrationDeliveryMetric.succeeded_count),
            func.sum(IntegrationDeliveryMetric.failed_count),
            func.sum(IntegrationDeliveryMetric.dead_letter_count),
        )
        .where(IntegrationDeliveryMetric.connector_type == SMTP_INTEGRATION_TYPE)
        .group_by(IntegrationDeliveryMetric.event_type)
    ).all()
    raw_rows = db.execute(
        select(IntegrationDelivery.event_type, IntegrationDelivery.state, func.count())
        .where(
            IntegrationDelivery.connector_type == SMTP_INTEGRATION_TYPE,
            IntegrationDelivery.metrics_aggregated_at.is_(None),
            IntegrationDelivery.state.in_(SMTP_TERMINAL_STATES),
        )
        .group_by(IntegrationDelivery.event_type, IntegrationDelivery.state)
    ).all()
    events: dict[str, dict[str, int]] = defaultdict(lambda: {"succeeded": 0, "failed": 0})
    for event_type, succeeded, failed, dead_letter in metric_rows:
        events[event_type]["succeeded"] += int(succeeded or 0)
        events[event_type]["failed"] += int(failed or 0) + int(dead_letter or 0)
    for event_type, state, count in raw_rows:
        bucket = "succeeded" if state == "succeeded" else "failed"
        events[event_type][bucket] += int(count or 0)

    successful = sum(values["succeeded"] for values in events.values())
    failed = sum(values["failed"] for values in events.values())
    total = successful + failed
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    failures_last_24h = int(
        db.scalar(
            select(func.count())
            .select_from(IntegrationDelivery)
            .where(
                IntegrationDelivery.connector_type == SMTP_INTEGRATION_TYPE,
                IntegrationDelivery.state.in_(SMTP_FAILURE_STATES),
                IntegrationDelivery.updated_at >= cutoff,
            )
        )
        or 0
    )
    pending = int(
        db.scalar(
            select(func.count())
            .select_from(IntegrationDelivery)
            .where(
                IntegrationDelivery.connector_type == SMTP_INTEGRATION_TYPE,
                IntegrationDelivery.state.in_(("pending", "sending")),
            )
        )
        or 0
    )
    retry_wait = int(
        db.scalar(
            select(func.count())
            .select_from(IntegrationDelivery)
            .where(
                IntegrationDelivery.connector_type == SMTP_INTEGRATION_TYPE,
                IntegrationDelivery.state == "retry_wait",
            )
        )
        or 0
    )
    failing_row = db.execute(
        select(
            IntegrationDelivery.integration_id,
            IntegrationInstance.name,
            func.count(),
            func.max(IntegrationDelivery.updated_at),
        )
        .join(IntegrationInstance, IntegrationInstance.id == IntegrationDelivery.integration_id)
        .where(
            IntegrationDelivery.connector_type == SMTP_INTEGRATION_TYPE,
            IntegrationDelivery.state.in_(SMTP_FAILURE_STATES),
        )
        .group_by(IntegrationDelivery.integration_id, IntegrationInstance.name)
        .order_by(func.count().desc(), func.max(IntegrationDelivery.updated_at).desc())
        .limit(1)
    ).first()
    most_failing = (
        SMTPAnalyticsHookSummary(
            hook_id=failing_row[0],
            hook_name=failing_row[1],
            failed_deliveries=int(failing_row[2] or 0),
            last_failure_at=failing_row[3],
        )
        if failing_row is not None
        else None
    )
    return SMTPAnalyticsResponse(
        hook_count=len(active_hooks),
        enabled_hook_count=sum(1 for hook in active_hooks if hook.enabled),
        total_deliveries=total,
        successful_deliveries=successful,
        failed_deliveries=failed,
        success_rate_pct=round((successful / total) * 100, 1) if total else 0.0,
        failures_last_24h=failures_last_24h,
        pending_deliveries=pending,
        retry_wait_deliveries=retry_wait,
        most_failing_hook=most_failing,
        events=[
            SMTPAnalyticsEventSummary(
                event_type=event_type,
                total_deliveries=values["succeeded"] + values["failed"],
                failed_deliveries=values["failed"],
            )
            for event_type, values in sorted(events.items())
        ],
    )


def list_smtp_deliveries(
    db: Session,
    *,
    instance: IntegrationInstance,
    page: int,
    page_size: int,
) -> SMTPDeliveryListResponse:
    query = select(IntegrationDelivery).where(
        IntegrationDelivery.integration_id == instance.id,
        IntegrationDelivery.connector_type == SMTP_INTEGRATION_TYPE,
    )
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    deliveries = db.scalars(
        query.order_by(IntegrationDelivery.created_at.desc(), IntegrationDelivery.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    delivery_ids = [delivery.id for delivery in deliveries]
    attempts_by_delivery: dict[uuid.UUID, list[IntegrationAttempt]] = defaultdict(list)
    if delivery_ids:
        for attempt in db.scalars(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.delivery_id.in_(delivery_ids))
            .order_by(IntegrationAttempt.delivery_id.asc(), IntegrationAttempt.attempt_number.asc())
        ).all():
            attempts_by_delivery[attempt.delivery_id].append(attempt)
    return SMTPDeliveryListResponse(
        deliveries=[
            _smtp_delivery_response(delivery, attempts=attempts_by_delivery.get(delivery.id, []))
            for delivery in deliveries
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def _smtp_delivery_response(
    delivery: IntegrationDelivery,
    *,
    attempts: list[IntegrationAttempt],
) -> SMTPDeliveryResponse:
    payload = delivery.payload_json if isinstance(delivery.payload_json, dict) else {}
    return SMTPDeliveryResponse(
        id=delivery.id,
        hook_id=delivery.integration_id,
        event_type=delivery.event_type,
        delivery_kind=delivery.delivery_kind,
        state=delivery.state,
        attempt_count=int(delivery.attempt_count or 0),
        max_attempts=int(delivery.max_attempts or 0),
        feed_id=_optional_uuid(payload.get("feed_id")),
        item_id=_optional_uuid(payload.get("item_id")),
        source_delivery_id=delivery.source_delivery_id,
        last_duration_ms=delivery.last_duration_ms,
        last_error_code=delivery.last_error_code,
        last_error_message=delivery.last_error_message,
        last_error_retryable=delivery.last_error_retryable,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
        completed_at=delivery.completed_at,
        dead_lettered_at=delivery.dead_lettered_at,
        attempts=[_smtp_attempt_response(attempt) for attempt in attempts],
    )


def _smtp_attempt_response(attempt: IntegrationAttempt) -> SMTPDeliveryAttemptResponse:
    response = attempt.response_json if isinstance(attempt.response_json, dict) else {}
    return SMTPDeliveryAttemptResponse(
        attempt_number=int(attempt.attempt_number),
        status=attempt.status,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
        duration_ms=attempt.duration_ms,
        error_code=attempt.error_code,
        error_message=attempt.error_message,
        retryable=attempt.retryable,
        recipient_count=_optional_int(response.get("recipient_count")),
        accepted_count=_optional_int(response.get("accepted_count")),
    )


def _validate_shared_credential_payload(payload: SMTPHookWrite, *, source: IntegrationInstance | None) -> None:
    if source is None:
        return
    if payload.settings.password is not None or payload.settings.clear_password:
        raise SMTPHookConflictError(
            "A password cannot be changed while reusing SMTP credentials. Edit the credential source instead."
        )


def _validate_unique_name(db: Session, name: str, *, excluding_id: uuid.UUID | None = None) -> None:
    normalized = name.casefold()
    instances = db.scalars(
        select(IntegrationInstance).where(IntegrationInstance.integration_type == SMTP_INTEGRATION_TYPE)
    ).all()
    if any(
        instance.id != excluding_id
        and not smtp_instance_is_archived(instance)
        and instance.name.casefold() == normalized
        for instance in instances
    ):
        raise SMTPHookConflictError("An SMTP hook with this name already exists.")


def _active_credential_dependents(db: Session, source_id: uuid.UUID) -> list[IntegrationInstance]:
    return [
        instance
        for instance in db.scalars(
            select(IntegrationInstance).where(
                IntegrationInstance.credential_source_integration_id == source_id,
                IntegrationInstance.integration_type == SMTP_INTEGRATION_TYPE,
            )
        ).all()
        if not smtp_instance_is_archived(instance)
    ]


def _optional_uuid(value) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _optional_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
