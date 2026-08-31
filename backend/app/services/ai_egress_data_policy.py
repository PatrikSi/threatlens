from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.audit_log import AuditLog
from app.models.data_policy import DataPolicyState
from app.models.feed import Feed
from app.models.iam import IAMPolicyState
from app.models.item import Item
from app.models.report import Report
from app.models.user import User
from app.services.ai_provider_client import (
    AI_PROVIDER_IO_NOT_SENT,
    AIIntegrationError,
)
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    authorization_context_for_user,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    DATA_ACCESS_RESOURCE_REPORT,
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
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation
from app.services.data_policy_audit import (
    DataPolicyAccessDecision,
    record_data_policy_decision,
)


AI_WORKER_PRINCIPAL_ID = uuid.UUID("00000000-0000-4000-8000-000000000301")

_FEATURE_CONNECTION_TEST = "connection_test"
_FEATURE_DAILY_BRIEF = "daily_brief"
_FEATURE_ITEM_ENRICHMENT = "item_enrichment"
_FEATURE_REPORT = "report"
_ITEM_RESOURCE_TYPE = "item"
_AI_EGRESS_SURFACE = "ai_provider.external_io"

_DENIED_MESSAGE = "AI provider request is blocked by the current data access policy."
_LINEAGE_MESSAGE = (
    "AI provider request is paused because governed data lineage is missing or "
    "ambiguous. Repair the resource lineage and retry."
)
_POLICY_UNAVAILABLE_MESSAGE = (
    "AI provider request is paused because data-policy authorization is unavailable. "
    "Retry the request."
)


@dataclass(frozen=True, slots=True)
class AIEgressPolicyFence:
    iam_revision: int
    data_policy_revision: int
    data_policy_mode: str


@dataclass(frozen=True, slots=True)
class AIEgressAuthorization:
    request_fingerprint: str
    audit_log_id: uuid.UUID | None
    iam_revision: int
    data_policy_revision: int
    data_policy_mode: str


class AIEgressPolicyError(AIIntegrationError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(
            message,
            retryable=retryable,
            provider_io_outcome=AI_PROVIDER_IO_NOT_SENT,
        )


def enforce_ai_egress_data_policy(
    db: Session,
    *,
    feature_type: str,
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    request_fingerprint: str = "",
) -> AIEgressAuthorization:
    """Fence governed AI input immediately before an external provider call."""

    try:
        fence, audit_log = _enforce_ai_egress_data_policy(
            db,
            feature_type=feature_type,
            item_id=item_id,
            daily_brief_id=daily_brief_id,
            report_id=report_id,
            request_fingerprint=request_fingerprint,
        )
    except SQLAlchemyError as exc:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        raise _policy_unavailable() from exc
    return AIEgressAuthorization(
        request_fingerprint=request_fingerprint,
        audit_log_id=audit_log.id if audit_log is not None else None,
        iam_revision=fence.iam_revision,
        data_policy_revision=fence.data_policy_revision,
        data_policy_mode=fence.data_policy_mode,
    )


def _enforce_ai_egress_data_policy(
    db: Session,
    *,
    feature_type: str,
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    request_fingerprint: str,
) -> tuple[AIEgressPolicyFence, AuditLog | None]:

    fence = lock_ai_egress_policy_fence(db)
    if fence.data_policy_mode == "disabled":
        return fence, None

    if feature_type == _FEATURE_CONNECTION_TEST:
        if any(value is not None for value in (item_id, daily_brief_id, report_id)):
            raise _lineage_unavailable()
        return fence, None

    _require_unambiguous_lineage(
        feature_type=feature_type,
        item_id=item_id,
        daily_brief_id=daily_brief_id,
        report_id=report_id,
    )
    if feature_type == _FEATURE_REPORT:
        assert report_id is not None
        context = _report_owner_data_access_context(
            db,
            report_id=report_id,
            fence=fence,
        )
        audit_log = _enforce_envelope(
            db,
            context=context,
            iam_revision=fence.iam_revision,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=report_id,
            request_fingerprint=request_fingerprint,
        )
        return fence, audit_log

    context = _ai_worker_data_access_context(db, fence=fence)
    if feature_type == _FEATURE_DAILY_BRIEF:
        assert daily_brief_id is not None
        _lock_daily_brief(db, daily_brief_id=daily_brief_id)
        audit_log = _enforce_envelope(
            db,
            context=context,
            iam_revision=fence.iam_revision,
            resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
            resource_id=daily_brief_id,
            request_fingerprint=request_fingerprint,
        )
        return fence, audit_log

    assert feature_type == _FEATURE_ITEM_ENRICHMENT
    assert item_id is not None
    label_id = _lock_item_feed_label(db, item_id=item_id)
    inaccessible = (
        not context.principal_eligible or label_id not in context.allowed_label_ids
    )
    decision = DataAccessDecision(
        allowed=context.principal_eligible
        and (not context.enforced or not inaccessible),
        would_deny=context.auditing and inaccessible,
        envelope_missing=False,
        label_ids=frozenset({label_id}),
        policy_revision=context.policy_revision,
    )
    audit_log = _apply_decision(
        db,
        context=context,
        iam_revision=fence.iam_revision,
        decision=decision,
        resource_type=_ITEM_RESOURCE_TYPE,
        resource_id=item_id,
        request_fingerprint=request_fingerprint,
    )
    return fence, audit_log


def lock_ai_egress_policy_fence(db: Session) -> AIEgressPolicyFence:
    """Acquire transaction-scoped policy locks in canonical IAM-then-data order."""

    iam_revision = db.scalar(
        select(IAMPolicyState.revision)
        .where(IAMPolicyState.id == 1)
        .with_for_update(read=True)
    )
    if iam_revision is None:
        raise _policy_unavailable()
    try:
        data_policy_revision = lock_data_policy_revision_for_derivation(db)
    except DataPolicyUnavailable as exc:
        raise _policy_unavailable() from exc
    data_policy_mode = db.scalar(
        select(DataPolicyState.mode)
        .where(DataPolicyState.id == 1)
        .execution_options(populate_existing=True)
    )
    if data_policy_mode is None:
        raise _policy_unavailable()
    return AIEgressPolicyFence(
        iam_revision=int(iam_revision),
        data_policy_revision=data_policy_revision,
        data_policy_mode=str(data_policy_mode),
    )


def _require_unambiguous_lineage(
    *,
    feature_type: str,
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
) -> None:
    lineage = {
        _FEATURE_ITEM_ENRICHMENT: (item_id, daily_brief_id, report_id),
        _FEATURE_DAILY_BRIEF: (daily_brief_id, item_id, report_id),
        _FEATURE_REPORT: (report_id, item_id, daily_brief_id),
    }.get(feature_type)
    if (
        lineage is None
        or lineage[0] is None
        or any(value is not None for value in lineage[1:])
    ):
        raise _lineage_unavailable()


def _report_owner_data_access_context(
    db: Session,
    *,
    report_id: uuid.UUID,
    fence: AIEgressPolicyFence,
) -> DataAccessContext:
    report = db.scalar(
        select(Report)
        .where(Report.id == report_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if report is None or report.owner_user_id is None:
        raise _lineage_unavailable()
    owner = db.scalar(
        select(User)
        .where(User.id == report.owner_user_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if owner is None:
        raise _lineage_unavailable()
    try:
        authorization = authorization_context_for_user(db, owner)
    except AuthorizationStateUnavailable as exc:
        raise _policy_unavailable() from exc
    return _fenced_data_access_context(db, authorization=authorization, fence=fence)


def _ai_worker_data_access_context(
    db: Session,
    *,
    fence: AIEgressPolicyFence,
) -> DataAccessContext:
    authorization = AuthorizationContext(
        principal_type="ai_worker",
        principal_id=AI_WORKER_PRINCIPAL_ID,
        legacy_role=None,
        account_eligible=True,
        roles=(),
        groups=(),
        grants=frozenset(),
        credential_grants=None,
        permissions=frozenset(),
        provenance={},
        policy_revision=fence.iam_revision,
    )
    return _fenced_data_access_context(db, authorization=authorization, fence=fence)


def _fenced_data_access_context(
    db: Session,
    *,
    authorization: AuthorizationContext,
    fence: AIEgressPolicyFence,
) -> DataAccessContext:
    if authorization.policy_revision != fence.iam_revision:
        raise _policy_unavailable()
    try:
        context = data_access_context_for_authorization(db, authorization)
    except DataPolicyUnavailable as exc:
        raise _policy_unavailable() from exc
    if (
        context.policy_revision != fence.data_policy_revision
        or context.mode != fence.data_policy_mode
    ):
        raise _policy_unavailable()
    return context


def _lock_daily_brief(db: Session, *, daily_brief_id: uuid.UUID) -> None:
    current_id = db.scalar(
        select(AIDailyBrief.id)
        .where(AIDailyBrief.id == daily_brief_id)
        .with_for_update()
    )
    if current_id is None:
        raise _lineage_unavailable()


def _lock_item_feed_label(db: Session, *, item_id: uuid.UUID) -> uuid.UUID:
    row = db.execute(
        select(Item, Feed)
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.id == item_id)
        .with_for_update(of=Item)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise _lineage_unavailable()
    _item, feed = row
    return feed.handling_label_id


def _enforce_envelope(
    db: Session,
    *,
    context: DataAccessContext,
    iam_revision: int,
    resource_type: str,
    resource_id: uuid.UUID,
    request_fingerprint: str,
) -> AuditLog | None:
    try:
        decision = require_data_access_for_egress(
            db,
            resource_type=resource_type,
            resource_id=resource_id,
            context=context,
        )
    except DataPolicyEgressDenied as exc:
        try:
            decision = evaluate_data_access_envelope(
                db,
                resource_type=resource_type,
                resource_id=resource_id,
                context=context,
            )
        except DataPolicyUnavailable as unavailable:
            raise _lineage_unavailable() from unavailable
        audit_decision: DataPolicyAccessDecision
        if not context.principal_eligible:
            audit_decision = "egress_not_served"
        elif context.enforced:
            audit_decision = "egress_denied"
        else:
            audit_decision = "egress_would_deny"
        _record_ai_egress_decision(
            db,
            context=context,
            iam_revision=iam_revision,
            decision=audit_decision,
            resource_type=resource_type,
            resource_id=resource_id,
            handling_label_ids=decision.label_ids,
            request_fingerprint=request_fingerprint,
        )
        raise AIEgressPolicyError(_DENIED_MESSAGE, retryable=False) from exc
    except DataPolicyUnavailable as exc:
        raise _lineage_unavailable() from exc
    return _apply_decision(
        db,
        context=context,
        iam_revision=iam_revision,
        decision=decision,
        resource_type=resource_type,
        resource_id=resource_id,
        request_fingerprint=request_fingerprint,
    )


def _apply_decision(
    db: Session,
    *,
    context: DataAccessContext,
    iam_revision: int,
    decision: DataAccessDecision,
    resource_type: str,
    resource_id: uuid.UUID,
    request_fingerprint: str,
) -> AuditLog | None:
    audit_log = None
    if decision.would_deny and decision.allowed:
        audit_log = _record_ai_egress_decision(
            db,
            context=context,
            iam_revision=iam_revision,
            decision="egress_would_deny",
            resource_type=resource_type,
            resource_id=resource_id,
            handling_label_ids=decision.label_ids,
            request_fingerprint=request_fingerprint,
        )
    if decision.allowed:
        return audit_log
    _record_ai_egress_decision(
        db,
        context=context,
        iam_revision=iam_revision,
        decision=("egress_denied" if context.enforced else "egress_not_served"),
        resource_type=resource_type,
        resource_id=resource_id,
        handling_label_ids=decision.label_ids,
        request_fingerprint=request_fingerprint,
    )
    raise AIEgressPolicyError(_DENIED_MESSAGE, retryable=False)


def _record_ai_egress_decision(
    db: Session,
    *,
    context: DataAccessContext,
    iam_revision: int,
    decision: DataPolicyAccessDecision,
    resource_type: str,
    resource_id: uuid.UUID,
    handling_label_ids: frozenset[uuid.UUID],
    request_fingerprint: str,
) -> AuditLog:
    action = {
        "egress_denied": "data_policy.egress.denied",
        "egress_not_served": "data_policy.egress.not_served",
        "egress_would_deny": "data_policy.egress.would_deny",
    }[decision]
    existing = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.action == action,
            AuditLog.resource_type == resource_type,
            AuditLog.resource_id == str(resource_id),
            AuditLog.actor_principal_type == context.principal_type,
            AuditLog.actor_principal_id == context.principal_id,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    ).all()
    for row in existing:
        metadata = row.metadata_json or {}
        if (
            metadata.get("surface") == _AI_EGRESS_SURFACE
            and metadata.get("iam_revision") == iam_revision
            and metadata.get("data_policy_revision") == context.policy_revision
            and (
                not request_fingerprint
                or metadata.get("request_fingerprint") == request_fingerprint
            )
        ):
            return row
    return record_data_policy_decision(
        db,
        context=context,
        decision=decision,
        resource_type=resource_type,
        resource_id=resource_id,
        surface=_AI_EGRESS_SURFACE,
        handling_label_ids=handling_label_ids,
        request_served_known=decision != "egress_would_deny",
        metadata_extra={
            "iam_revision": iam_revision,
            "request_fingerprint": request_fingerprint,
            "provider_io_state": "not_sent",
            "provider_attempt_count_reserved": 0,
        }
        if request_fingerprint
        else {
            "iam_revision": iam_revision,
            "provider_io_state": "not_sent",
            "provider_attempt_count_reserved": 0,
        },
    )


def mark_ai_egress_provider_io_state(
    db: Session,
    *,
    authorization: AIEgressAuthorization | None,
    state: str,
    attempt_count: int,
) -> None:
    if state not in {"reserved", "not_sent", "sent", "ambiguous"}:
        raise ValueError("Unsupported AI provider I/O state.")
    if authorization is None or authorization.audit_log_id is None:
        return
    audit_log = db.scalar(
        select(AuditLog)
        .where(AuditLog.id == authorization.audit_log_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if audit_log is None:
        raise _policy_unavailable()
    metadata = dict(audit_log.metadata_json or {})
    if metadata.get("request_fingerprint") != authorization.request_fingerprint:
        raise _policy_unavailable()
    existing_state = str(metadata.get("provider_io_state") or "not_sent")
    if metadata.get("request_served") is True or state == "sent":
        effective_state = "sent"
    elif existing_state == "ambiguous" or state == "ambiguous":
        effective_state = "ambiguous"
    else:
        effective_state = state
    metadata["provider_io_state"] = effective_state
    metadata["provider_attempt_count_reserved"] = max(
        int(metadata.get("provider_attempt_count_reserved") or 0),
        max(1, int(attempt_count)),
    )
    if effective_state == "not_sent":
        metadata["request_served"] = False
    elif effective_state == "sent":
        metadata["request_served"] = True
    else:
        metadata.pop("request_served", None)
    audit_log.metadata_json = metadata
    db.add(audit_log)


def _lineage_unavailable() -> AIEgressPolicyError:
    return AIEgressPolicyError(_LINEAGE_MESSAGE, retryable=True)


def _policy_unavailable() -> AIEgressPolicyError:
    return AIEgressPolicyError(_POLICY_UNAVAILABLE_MESSAGE, retryable=True)


__all__ = [
    "AI_WORKER_PRINCIPAL_ID",
    "AIEgressAuthorization",
    "AIEgressPolicyError",
    "AIEgressPolicyFence",
    "enforce_ai_egress_data_policy",
    "lock_ai_egress_policy_fence",
    "mark_ai_egress_provider_io_state",
]
