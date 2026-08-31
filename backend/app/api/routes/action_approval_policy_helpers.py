from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.api.deps import get_authorization_context
from app.services.action_approval_data_policy import (
    ActionApprovalWouldDenySummary,
    action_approval_access_decision,
)
from app.services.authorization import (
    AuthorizationStateUnavailable,
    fence_authorization_context,
)
from app.services.data_access_envelopes import DataAccessDecision
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyUnavailable,
    fence_data_access_context,
)
from app.services.data_policy_audit import record_data_policy_decision


def authorize_action_approval_data_access(
    db: Session,
    *,
    approval_id: uuid.UUID,
    data_access: DataAccessContext,
    surface: str,
) -> DataAccessDecision:
    decision = action_approval_access_decision(
        db,
        approval_id=approval_id,
        data_access=data_access,
    )
    if decision.would_deny:
        record_data_policy_decision(
            db,
            context=data_access,
            decision="would_deny",
            resource_type="action_approval",
            surface=surface,
            handling_label_ids=decision.label_ids,
            affected_count=1,
            metadata_extra={"history_scope": "target_authorization"},
        )
    return decision


def record_action_approval_would_deny(
    db: Session,
    *,
    data_access: DataAccessContext,
    summary: ActionApprovalWouldDenySummary,
    surface: str,
    history_scope: str,
) -> bool:
    if not summary.affected_count:
        return False
    record_data_policy_decision(
        db,
        context=data_access,
        decision="would_deny",
        resource_type="action_approval",
        surface=surface,
        handling_label_ids=summary.handling_label_ids,
        affected_count=summary.affected_count,
        metadata_extra={"history_scope": history_scope},
    )
    return True


def commit_policy_evidence_and_refence(
    request: Request,
    db: Session,
    *,
    data_access: DataAccessContext,
) -> None:
    db.commit()
    refence_action_approval_context(request, db, data_access=data_access)


def refence_action_approval_context(
    request: Request,
    db: Session,
    *,
    data_access: DataAccessContext,
) -> None:
    authorization = get_authorization_context(request)
    if authorization is None:
        raise DataPolicyUnavailable(
            "Action approval authorization is unavailable. Retry the request."
        )
    try:
        fence_authorization_context(db, authorization)
    except AuthorizationStateUnavailable as exc:
        raise DataPolicyUnavailable(
            "Action approval authorization changed while the request was in progress. Retry the request."
        ) from exc
    fence_data_access_context(db, data_access)


__all__ = [
    "authorize_action_approval_data_access",
    "commit_policy_evidence_and_refence",
    "record_action_approval_would_deny",
    "refence_action_approval_context",
]
