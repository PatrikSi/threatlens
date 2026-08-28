from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
)
from app.services.integration_event_schemas import (
    max_supported_integration_event_schema,
)
from app.services.integration_registry_constants import SMTP_CONFIG_SCHEMA_VERSION
from app.services.smtp_delivery_errors import (
    SMTPDeliverySourceCompatibilityError,
    SMTPDeliverySourceContextError,
)


def ensure_smtp_delivery_schema_compatible(
    db: Session,
    *,
    delivery: IntegrationDelivery,
) -> None:
    alert_delivery = delivery.event_type == "alert_match"
    invalid_code = (
        "smtp_source_owner_context_invalid"
        if alert_delivery
        else "smtp_event_schema_invalid"
    )
    compatibility_code = (
        "smtp_source_owner_context_unsupported"
        if alert_delivery
        else "smtp_event_schema_unsupported"
    )
    event_schema_version = 1
    event: IntegrationEvent | None = None
    if delivery.event_id is not None:
        event = db.get(IntegrationEvent, delivery.event_id)
        if event is None:
            raise SMTPDeliverySourceContextError(
                (
                    "smtp_source_owner_context_missing"
                    if alert_delivery
                    else "smtp_event_missing"
                ),
                "SMTP delivery linked integration event no longer exists.",
            )
        if event.event_type != delivery.event_type:
            raise SMTPDeliverySourceContextError(
                (
                    "smtp_source_owner_context_mismatch"
                    if alert_delivery
                    else "smtp_event_type_mismatch"
                ),
                "SMTP delivery event type does not match its linked integration event.",
            )
        event_schema_version = _integration_event_schema_version(
            event.schema_version,
            label="event",
            error_code=invalid_code,
        )
        if not isinstance(event.payload_json, dict):
            raise SMTPDeliverySourceContextError(
                invalid_code,
                "SMTP delivery linked integration event payload must be an object.",
            )
        event_payload_schema_version = _integration_event_schema_version(
            event.payload_json.get("schema_version", 1),
            label="event payload",
            error_code=invalid_code,
        )
        if event_payload_schema_version != event_schema_version:
            raise SMTPDeliverySourceContextError(
                (
                    "smtp_source_owner_context_mismatch"
                    if alert_delivery
                    else "smtp_event_schema_mismatch"
                ),
                "SMTP delivery linked integration event has a mismatched payload schema.",
            )
    if not isinstance(delivery.payload_json, dict):
        raise SMTPDeliverySourceContextError(
            invalid_code,
            "SMTP delivery payload must be an object.",
        )
    delivery_schema_version = _integration_event_schema_version(
        delivery.payload_json.get("schema_version", 1),
        label="delivery",
        error_code=invalid_code,
    )
    if event is not None and (
        (not alert_delivery and delivery_schema_version != event_schema_version)
        or (
            alert_delivery
            and event_schema_version >= 2
            and delivery_schema_version != event_schema_version
        )
    ):
        raise SMTPDeliverySourceContextError(
            (
                "smtp_source_owner_context_mismatch"
                if alert_delivery
                else "smtp_event_schema_mismatch"
            ),
            "SMTP delivery schema does not match its linked integration event.",
        )
    instance = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == delivery.integration_id)
        .execution_options(populate_existing=True)
    )
    credential_source = None
    if instance is not None and instance.credential_source_integration_id is not None:
        credential_source = db.scalar(
            select(IntegrationInstance)
            .where(
                IntegrationInstance.id == instance.credential_source_integration_id
            )
            .execution_options(populate_existing=True)
        )
    ensure_smtp_config_schema_compatible(
        instance=instance,
        credential_source=credential_source,
    )
    supported_version = max_supported_integration_event_schema(delivery.event_type)
    if event_schema_version > supported_version:
        raise _schema_compatibility_error(
            event_type=delivery.event_type,
            schema_version=event_schema_version,
            supported_version=supported_version,
            label="event",
            error_code=compatibility_code,
        )
    if delivery_schema_version > supported_version:
        raise _schema_compatibility_error(
            event_type=delivery.event_type,
            schema_version=delivery_schema_version,
            supported_version=supported_version,
            label="delivery",
            error_code=compatibility_code,
        )


def ensure_smtp_config_schema_compatible(
    *,
    instance: IntegrationInstance | None,
    credential_source: IntegrationInstance | None = None,
) -> None:
    candidates = [
        ("integration configuration", instance),
        ("credential-source configuration", credential_source),
    ]
    for label, candidate in candidates:
        if candidate is None:
            continue
        schema_version = _integration_event_schema_version(
            candidate.schema_version,
            label=label,
            error_code="smtp_config_schema_invalid",
        )
        if schema_version > SMTP_CONFIG_SCHEMA_VERSION:
            raise SMTPDeliverySourceCompatibilityError(
                "smtp_config_schema_unsupported",
                f"SMTP {label} uses newer schema version {schema_version}; this worker "
                f"supports through version {SMTP_CONFIG_SCHEMA_VERSION}. Delivery will "
                "retry after the worker is upgraded.",
            )


def _integration_event_schema_version(
    value: object,
    *,
    label: str,
    error_code: str,
) -> int:
    if value is None:
        schema_version = 1
    elif type(value) is int:
        schema_version = value
    elif (
        isinstance(value, str)
        and len(value) <= 9
        and value.isascii()
        and value.isdigit()
        and value == str(int(value))
    ):
        schema_version = int(value)
    else:
        raise SMTPDeliverySourceContextError(
            error_code,
            f"SMTP {label} has an invalid schema version.",
        )
    if schema_version < 1:
        raise SMTPDeliverySourceContextError(
            error_code,
            f"SMTP {label} has an unsupported schema version.",
        )
    return schema_version


def _schema_compatibility_error(
    *,
    event_type: str,
    schema_version: int,
    supported_version: int,
    label: str,
    error_code: str,
) -> SMTPDeliverySourceCompatibilityError:
    return SMTPDeliverySourceCompatibilityError(
        error_code,
        f"SMTP {event_type} {label} uses newer schema version {schema_version}; "
        f"this worker supports through version {supported_version}. Delivery will "
        "retry after the worker is upgraded.",
    )
