from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.integration import IntegrationDelivery, IntegrationInstance
from app.services.integration_delivery_data_policy import (
    IntegrationDeliveryDataPolicyDenied,
    IntegrationDeliveryDataPolicyUnavailable,
    IntegrationDeliveryPolicyFence,
    enforce_integration_delivery_data_policy,
    lock_integration_delivery_policy_fence,
)
from app.services.smtp_delivery_errors import (
    SMTPDeliveryIneligibleError,
    SMTPDeliveryTemporarilyIneligibleError,
)


def enforce_smtp_delivery_data_policy(
    db: Session,
    *,
    instance: IntegrationInstance,
    delivery: IntegrationDelivery,
    policy_fence: IntegrationDeliveryPolicyFence,
) -> None:
    try:
        enforce_integration_delivery_data_policy(
            db,
            instance=instance,
            delivery=delivery,
            surface="smtp.external_io",
            policy_fence=policy_fence,
        )
    except IntegrationDeliveryDataPolicyDenied as exc:
        raise SMTPDeliveryIneligibleError(
            "smtp_data_policy_denied",
            str(exc),
            data_policy_audit=exc.audit,
        ) from exc
    except IntegrationDeliveryDataPolicyUnavailable as exc:
        raise SMTPDeliveryTemporarilyIneligibleError(
            "smtp_data_policy_unavailable",
            str(exc),
        ) from exc


def lock_smtp_delivery_data_policy_fence(
    db: Session,
) -> IntegrationDeliveryPolicyFence:
    try:
        return lock_integration_delivery_policy_fence(db)
    except IntegrationDeliveryDataPolicyUnavailable as exc:
        raise SMTPDeliveryTemporarilyIneligibleError(
            "smtp_data_policy_unavailable",
            str(exc),
        ) from exc


__all__ = [
    "enforce_smtp_delivery_data_policy",
    "lock_smtp_delivery_data_policy_fence",
]
