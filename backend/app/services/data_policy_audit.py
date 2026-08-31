from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Literal

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.services.audit import record_audit
from app.services.data_access_policy import DataAccessContext


DataPolicyAccessDecision = Literal[
    "not_served",
    "would_deny",
    "egress_denied",
    "egress_not_served",
    "egress_would_deny",
]

_ACTION_BY_DECISION: dict[DataPolicyAccessDecision, str] = {
    "not_served": "data_policy.access.not_served",
    "would_deny": "data_policy.access.would_deny",
    "egress_denied": "data_policy.egress.denied",
    "egress_not_served": "data_policy.egress.not_served",
    "egress_would_deny": "data_policy.egress.would_deny",
}

_RESERVED_METADATA_KEYS = frozenset(
    {
        "decision",
        "surface",
        "data_policy_mode",
        "data_policy_revision",
        "data_policy_coverage_version",
        "request_served",
        "handling_label_count",
        "handling_label_ids",
        "envelope_id",
        "affected_count",
    }
)


def record_data_policy_decision(
    db: Session,
    *,
    context: DataAccessContext,
    decision: DataPolicyAccessDecision,
    resource_type: str,
    resource_id: uuid.UUID | str | None = None,
    surface: str,
    handling_label_ids: Iterable[uuid.UUID] = (),
    envelope_id: uuid.UUID | None = None,
    affected_count: int | None = None,
    request_served_known: bool = True,
    metadata_extra: Mapping[str, object] | None = None,
) -> AuditLog:
    _validate_decision_mode(context, decision)
    label_ids = sorted({str(value) for value in handling_label_ids})
    metadata: dict[str, object] = {
        "decision": decision,
        "surface": surface,
        "data_policy_mode": context.mode,
        "data_policy_revision": context.policy_revision,
        "data_policy_coverage_version": context.coverage_version,
        "handling_label_count": len(label_ids),
    }
    if request_served_known:
        metadata["request_served"] = decision in {
            "would_deny",
            "egress_would_deny",
        }
    if label_ids:
        metadata["handling_label_ids"] = label_ids
    if envelope_id is not None:
        metadata["envelope_id"] = str(envelope_id)
    if affected_count is not None:
        metadata["affected_count"] = max(0, int(affected_count))
    if metadata_extra:
        reserved_keys = _RESERVED_METADATA_KEYS.intersection(metadata_extra)
        if reserved_keys:
            raise ValueError(
                "Additional data-policy audit metadata cannot replace reserved keys."
            )
        metadata.update(metadata_extra)

    return record_audit(
        db,
        actor_user_id=(
            context.principal_id if context.principal_type == "user" else None
        ),
        actor_principal_type=context.principal_type,
        actor_principal_id=context.principal_id,
        action=_ACTION_BY_DECISION[decision],
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        success=decision in {"would_deny", "egress_would_deny"},
        metadata=metadata,
    )


def _validate_decision_mode(
    context: DataAccessContext,
    decision: DataPolicyAccessDecision,
) -> None:
    if decision in {"would_deny", "egress_would_deny"}:
        if not context.auditing:
            raise ValueError(
                "would-deny audit decisions require data-policy audit mode"
            )
        return
    if decision == "egress_not_served":
        if context.mode == "disabled":
            raise ValueError(
                "not-served egress audit decisions require an active data-policy mode"
            )
        return
    if not context.enforced:
        raise ValueError("denied audit decisions require enforced data-policy mode")


__all__ = ["DataPolicyAccessDecision", "record_data_policy_decision"]
