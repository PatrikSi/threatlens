from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.iam import IAMPolicyState
from app.models.integration import IntegrationDelivery, IntegrationInstance
from app.models.user import User
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    authorization_context_for_user,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DataAccessDecision,
    DataPolicyEgressDenied,
    evaluate_data_access_envelope,
    require_data_access_for_egress,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyUnavailable,
    data_access_context_for_authorization,
)
from app.services.data_policy_audit import (
    DataPolicyAccessDecision,
    record_data_policy_decision,
)
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation


@dataclass(frozen=True, slots=True)
class IntegrationDeliveryPolicyAudit:
    context: DataAccessContext
    decision: DataPolicyAccessDecision
    delivery_id: uuid.UUID
    surface: str
    handling_label_ids: frozenset[uuid.UUID]


@dataclass(frozen=True, slots=True)
class IntegrationDeliveryPolicyFence:
    iam_revision: int
    data_policy_revision: int


class IntegrationDeliveryDataPolicyError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        audit: IntegrationDeliveryPolicyAudit | None = None,
    ) -> None:
        super().__init__(message)
        self.audit = audit


class IntegrationDeliveryDataPolicyDenied(IntegrationDeliveryDataPolicyError):
    pass


class IntegrationDeliveryDataPolicyUnavailable(IntegrationDeliveryDataPolicyError):
    pass


def enforce_integration_delivery_data_policy(
    db: Session,
    *,
    instance: IntegrationInstance,
    delivery: IntegrationDelivery,
    surface: str,
    policy_fence: IntegrationDeliveryPolicyFence | None = None,
) -> DataAccessDecision:
    """Fence the final external-I/O boundary with the delivery's principal."""

    fence = policy_fence or lock_integration_delivery_policy_fence(db)
    context = _delivery_data_access_context(
        db,
        instance=instance,
        delivery=delivery,
        iam_revision=fence.iam_revision,
    )
    if context.policy_revision != fence.data_policy_revision:
        raise IntegrationDeliveryDataPolicyUnavailable(
            "Outbound delivery is paused because data access policy changed during evaluation. Retry the delivery."
        )
    try:
        decision = require_data_access_for_egress(
            db,
            resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            resource_id=delivery.id,
            context=context,
        )
    except DataPolicyEgressDenied as exc:
        denied = evaluate_data_access_envelope(
            db,
            resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            resource_id=delivery.id,
            context=context,
        )
        audit = IntegrationDeliveryPolicyAudit(
            context=context,
            decision="egress_denied",
            delivery_id=delivery.id,
            surface=surface,
            handling_label_ids=denied.label_ids,
        )
        record_integration_delivery_policy_audit(db, audit=audit)
        raise IntegrationDeliveryDataPolicyDenied(str(exc), audit=audit) from exc
    except DataPolicyUnavailable as exc:
        raise IntegrationDeliveryDataPolicyUnavailable(str(exc)) from exc

    if decision.would_deny:
        audit = IntegrationDeliveryPolicyAudit(
            context=context,
            decision="egress_would_deny",
            delivery_id=delivery.id,
            surface=surface,
            handling_label_ids=decision.label_ids,
        )
        record_integration_delivery_policy_audit(db, audit=audit)
    return decision


def record_integration_delivery_policy_audit(
    db: Session,
    *,
    audit: IntegrationDeliveryPolicyAudit,
) -> AuditLog:
    """Record one decision per delivery, surface, principal, and policy revision."""

    action = {
        "egress_denied": "data_policy.egress.denied",
        "egress_would_deny": "data_policy.egress.would_deny",
    }[audit.decision]
    existing = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.action == action,
            AuditLog.resource_type == DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            AuditLog.resource_id == str(audit.delivery_id),
            AuditLog.actor_principal_type == audit.context.principal_type,
            AuditLog.actor_principal_id == audit.context.principal_id,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    ).all()
    for row in existing:
        metadata = row.metadata_json or {}
        if (
            metadata.get("surface") == audit.surface
            and metadata.get("data_policy_revision") == audit.context.policy_revision
        ):
            return row

    return record_data_policy_decision(
        db,
        context=audit.context,
        decision=audit.decision,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
        resource_id=audit.delivery_id,
        surface=audit.surface,
        handling_label_ids=audit.handling_label_ids,
    )


def _delivery_data_access_context(
    db: Session,
    *,
    instance: IntegrationInstance,
    delivery: IntegrationDelivery,
    iam_revision: int,
) -> DataAccessContext:
    try:
        if delivery.owner_user_id is not None:
            owner = db.get(User, delivery.owner_user_id)
            if owner is None:
                raise IntegrationDeliveryDataPolicyUnavailable(
                    "Outbound delivery is paused because its policy principal no longer exists."
                )
            authorization = authorization_context_for_user(db, owner)
        else:
            authorization = _system_integration_authorization(
                instance,
                iam_revision=iam_revision,
            )
        if authorization.policy_revision != iam_revision:
            raise IntegrationDeliveryDataPolicyUnavailable(
                "Outbound delivery is paused because IAM policy changed during evaluation. Retry the delivery."
            )
        return data_access_context_for_authorization(db, authorization)
    except AuthorizationStateUnavailable as exc:
        raise IntegrationDeliveryDataPolicyUnavailable(
            "Outbound delivery is paused because effective access could not be evaluated. Retry the delivery."
        ) from exc
    except DataPolicyUnavailable as exc:
        raise IntegrationDeliveryDataPolicyUnavailable(str(exc)) from exc


def _system_integration_authorization(
    instance: IntegrationInstance,
    *,
    iam_revision: int,
) -> AuthorizationContext:
    return AuthorizationContext(
        principal_type="integration_instance",
        principal_id=instance.id,
        legacy_role=None,
        account_eligible=True,
        roles=(),
        groups=(),
        grants=frozenset(),
        credential_grants=None,
        permissions=frozenset(),
        provenance={},
        policy_revision=iam_revision,
    )


def lock_integration_delivery_policy_fence(
    db: Session,
) -> IntegrationDeliveryPolicyFence:
    iam_revision = db.scalar(
        select(IAMPolicyState.revision)
        .where(IAMPolicyState.id == 1)
        .with_for_update(read=True)
    )
    if iam_revision is None:
        raise IntegrationDeliveryDataPolicyUnavailable(
            "Outbound delivery is paused because IAM policy state is missing."
        )
    try:
        data_policy_revision = lock_data_policy_revision_for_derivation(db)
    except DataPolicyUnavailable as exc:
        raise IntegrationDeliveryDataPolicyUnavailable(str(exc)) from exc
    return IntegrationDeliveryPolicyFence(
        iam_revision=int(iam_revision),
        data_policy_revision=data_policy_revision,
    )


__all__ = [
    "IntegrationDeliveryDataPolicyDenied",
    "IntegrationDeliveryDataPolicyUnavailable",
    "IntegrationDeliveryPolicyAudit",
    "IntegrationDeliveryPolicyFence",
    "enforce_integration_delivery_data_policy",
    "lock_integration_delivery_policy_fence",
    "record_integration_delivery_policy_audit",
]
