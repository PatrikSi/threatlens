from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    HandlingLabel,
)
from app.models.integration import IntegrationDelivery
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import (
    DataAccessContext,
    fence_data_access_context,
)


@dataclass(frozen=True, slots=True)
class IntegrationDeliveryWouldDenySummary:
    affected_count: int
    handling_label_ids: frozenset[uuid.UUID]


def integration_delivery_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
    connector_type: str | None = None,
    integration_id: uuid.UUID | None = None,
    delivery_id: uuid.UUID | None = None,
) -> IntegrationDeliveryWouldDenySummary:
    """Summarize rows an audit-mode history read would hide if enforced."""

    if not data_access.auditing or not data_access.principal_eligible:
        return IntegrationDeliveryWouldDenySummary(0, frozenset())

    fence_data_access_context(db, data_access)
    enforced_context = replace(data_access, mode="enforced")
    filters = []
    if connector_type is not None:
        filters.append(IntegrationDelivery.connector_type == connector_type)
    if integration_id is not None:
        filters.append(IntegrationDelivery.integration_id == integration_id)
    if delivery_id is not None:
        filters.append(IntegrationDelivery.id == delivery_id)

    denied = ~data_access_envelope_predicate(
        DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
        IntegrationDelivery.id,
        enforced_context,
    )
    affected_count = int(
        db.scalar(
            select(func.count())
            .select_from(IntegrationDelivery)
            .where(*filters, denied)
        )
        or 0
    )
    if not affected_count:
        return IntegrationDeliveryWouldDenySummary(0, frozenset())

    label_ids = frozenset(
        db.scalars(
            select(DataAccessEnvelopeLabel.label_id)
            .select_from(IntegrationDelivery)
            .join(
                DataAccessEnvelope,
                and_(
                    DataAccessEnvelope.resource_type
                    == DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
                    DataAccessEnvelope.resource_id == IntegrationDelivery.id,
                ),
            )
            .join(
                DataAccessEnvelopeLabel,
                DataAccessEnvelopeLabel.envelope_id == DataAccessEnvelope.id,
            )
            .join(
                HandlingLabel,
                HandlingLabel.id == DataAccessEnvelopeLabel.label_id,
            )
            .where(
                *filters,
                denied,
                or_(
                    DataAccessEnvelopeLabel.label_id.not_in(
                        enforced_context.allowed_label_ids
                    ),
                    HandlingLabel.is_active.is_(False),
                ),
            )
            .distinct()
        ).all()
    )
    return IntegrationDeliveryWouldDenySummary(affected_count, label_ids)


__all__ = [
    "IntegrationDeliveryWouldDenySummary",
    "integration_delivery_would_deny_summary",
]
