from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import IntegrationDelivery, IntegrationEvent
from app.services.report_event_compatibility import (
    validate_report_ready_delivery_owner,
)
from app.services.smtp_delivery_errors import SMTPDeliveryIneligibleError


def smtp_report_ready_delivery_owner_id(
    db: Session,
    *,
    delivery: IntegrationDelivery,
) -> uuid.UUID:
    if delivery.event_id is None:
        raise SMTPDeliveryIneligibleError(
            "smtp_report_event_missing",
            "SMTP report delivery is missing its source event.",
        )
    event = db.scalar(
        select(IntegrationEvent)
        .where(IntegrationEvent.id == delivery.event_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if event is None or event.event_type != "report_ready":
        raise SMTPDeliveryIneligibleError(
            "smtp_report_event_missing",
            "SMTP report delivery source event no longer exists.",
        )
    try:
        return validate_report_ready_delivery_owner(
            db,
            event=event,
            delivery_payload=delivery.payload_json,
            delivery_owner_user_id=delivery.owner_user_id,
            require_eligible=False,
        )
    except ValueError as exc:
        raise SMTPDeliveryIneligibleError(
            "smtp_report_owner_context_invalid",
            "SMTP report delivery has invalid owner context.",
        ) from exc


__all__ = ["smtp_report_ready_delivery_owner_id"]
