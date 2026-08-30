from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from app.models.access_review import (
    ACCESS_REVIEW_APPLY_OUTCOMES,
    ACCESS_REVIEW_ITEM_TYPES,
    ACCESS_REVIEW_STATUSES,
    ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES,
    AccessReviewApplyReceipt,
    AccessReviewCampaign,
    AccessReviewDecision,
    AccessReviewItem,
)
from app.schemas.access_review import (
    AccessReviewApplyReceiptResponse,
    AccessReviewCampaignListResponse,
    AccessReviewCampaignResponse,
    AccessReviewDecisionResponse,
    AccessReviewItemListResponse,
    AccessReviewItemResponse,
)
from app.services.access_reviews import AccessReviewError, AccessReviewNotFound
from app.services.authorization import database_clock


MAX_ACCESS_REVIEW_QUERY_PAGE_SIZE = 100
_PRINCIPAL_TYPES = frozenset({"user", "service_account", "oidc_provider"})
_DECISION_FILTERS = frozenset({"retain", "revoke", "undecided"})
_APPLY_OUTCOME_FILTERS = ACCESS_REVIEW_APPLY_OUTCOMES | {"not_applied"}


class AccessReviewQueryInvalid(AccessReviewError):
    code = "access_review_query_invalid"


def list_access_review_campaigns(
    db: Session,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
) -> AccessReviewCampaignListResponse:
    _validate_page(page=page, page_size=page_size)
    _validate_filter("status", status, ACCESS_REVIEW_STATUSES)

    filters = [AccessReviewCampaign.status == status] if status is not None else []
    total = int(
        db.scalar(select(func.count(AccessReviewCampaign.id)).where(*filters)) or 0
    )
    campaign_ids = tuple(
        db.scalars(
            select(AccessReviewCampaign.id)
            .where(*filters)
            .order_by(
                AccessReviewCampaign.created_at.desc(),
                AccessReviewCampaign.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    if not campaign_ids:
        return AccessReviewCampaignListResponse(
            campaigns=[],
            total=total,
            page=page,
            page_size=page_size,
        )
    rows = db.execute(
        _campaign_projection_statement(campaign_ids=campaign_ids)
        .where(AccessReviewCampaign.id.in_(campaign_ids))
        .order_by(
            AccessReviewCampaign.created_at.desc(), AccessReviewCampaign.id.desc()
        )
    ).all()
    now = _database_now(db)
    return AccessReviewCampaignListResponse(
        campaigns=[_campaign_response(row, now=now) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_access_review_campaign(
    db: Session,
    campaign_id: uuid.UUID,
) -> AccessReviewCampaignResponse:
    row = db.execute(
        _campaign_projection_statement(campaign_ids=(campaign_id,)).where(
            AccessReviewCampaign.id == campaign_id
        )
    ).one_or_none()
    if row is None:
        raise AccessReviewNotFound("Access-review campaign not found.")
    return _campaign_response(row, now=_database_now(db))


def list_access_review_items(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    page: int,
    page_size: int,
    item_type: str | None = None,
    principal_type: str | None = None,
    decision: str | None = None,
    apply_outcome: str | None = None,
) -> AccessReviewItemListResponse:
    _validate_page(page=page, page_size=page_size)
    _validate_filter("item_type", item_type, ACCESS_REVIEW_ITEM_TYPES)
    _validate_filter("principal_type", principal_type, _PRINCIPAL_TYPES)
    _validate_filter("decision", decision, _DECISION_FILTERS)
    _validate_filter("apply_outcome", apply_outcome, _APPLY_OUTCOME_FILTERS)
    _require_campaign(db, campaign_id)

    decision_ranked = _latest_decision_rows(campaign_ids=(campaign_id,))
    receipt_ranked = _latest_receipt_rows(campaign_ids=(campaign_id,))
    latest_decision = aliased(AccessReviewDecision, decision_ranked)
    latest_receipt = aliased(AccessReviewApplyReceipt, receipt_ranked)
    join_decision = and_(
        latest_decision.item_id == AccessReviewItem.id,
        decision_ranked.c.latest_rank == 1,
    )
    join_receipt = and_(
        latest_receipt.item_id == AccessReviewItem.id,
        receipt_ranked.c.latest_rank == 1,
    )
    filters = _item_filters(
        campaign_id=campaign_id,
        latest_decision=latest_decision,
        latest_receipt=latest_receipt,
        item_type=item_type,
        principal_type=principal_type,
        decision=decision,
        apply_outcome=apply_outcome,
    )

    total = int(
        db.scalar(
            select(func.count(AccessReviewItem.id))
            .select_from(AccessReviewItem)
            .outerjoin(latest_decision, join_decision)
            .outerjoin(latest_receipt, join_receipt)
            .where(*filters)
        )
        or 0
    )
    rows = db.execute(
        select(AccessReviewItem, latest_decision, latest_receipt)
        .outerjoin(latest_decision, join_decision)
        .outerjoin(latest_receipt, join_receipt)
        .where(*filters)
        .order_by(AccessReviewItem.ordinal.asc(), AccessReviewItem.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return AccessReviewItemListResponse(
        items=[
            _item_response(item, latest_decision_row, latest_receipt_row)
            for item, latest_decision_row, latest_receipt_row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def _campaign_projection_statement(*, campaign_ids: tuple[uuid.UUID, ...]):
    decision_ranked = _latest_decision_rows(campaign_ids=campaign_ids)
    latest_decisions = (
        select(
            decision_ranked.c.campaign_id,
            decision_ranked.c.item_id,
            decision_ranked.c.decision,
        )
        .where(decision_ranked.c.latest_rank == 1)
        .subquery("access_review_latest_decisions")
    )
    decision_counts = (
        select(
            latest_decisions.c.campaign_id,
            func.count(latest_decisions.c.item_id).label("decided_item_count"),
            func.count(latest_decisions.c.item_id)
            .filter(latest_decisions.c.decision == "revoke")
            .label("revoke_item_count"),
        )
        .group_by(latest_decisions.c.campaign_id)
        .subquery("access_review_decision_counts")
    )

    receipt_ranked = _latest_receipt_rows(campaign_ids=campaign_ids)
    latest_receipts = (
        select(
            receipt_ranked.c.campaign_id,
            receipt_ranked.c.item_id,
            receipt_ranked.c.outcome,
        )
        .where(receipt_ranked.c.latest_rank == 1)
        .subquery("access_review_latest_receipts")
    )
    receipt_counts = (
        select(
            latest_receipts.c.campaign_id,
            func.count(latest_receipts.c.item_id)
            .filter(
                latest_receipts.c.outcome.in_(ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES)
            )
            .label("apply_terminal_item_count"),
        )
        .group_by(latest_receipts.c.campaign_id)
        .subquery("access_review_receipt_counts")
    )

    return (
        select(
            AccessReviewCampaign,
            func.coalesce(decision_counts.c.decided_item_count, 0).label(
                "decided_item_count"
            ),
            func.coalesce(decision_counts.c.revoke_item_count, 0).label(
                "revoke_item_count"
            ),
            func.coalesce(receipt_counts.c.apply_terminal_item_count, 0).label(
                "apply_terminal_item_count"
            ),
        )
        .outerjoin(
            decision_counts,
            decision_counts.c.campaign_id == AccessReviewCampaign.id,
        )
        .outerjoin(
            receipt_counts,
            receipt_counts.c.campaign_id == AccessReviewCampaign.id,
        )
    )


def _latest_decision_rows(*, campaign_ids: tuple[uuid.UUID, ...] | None = None):
    statement = select(
        AccessReviewDecision,
        func.row_number()
        .over(
            partition_by=AccessReviewDecision.item_id,
            order_by=AccessReviewDecision.sequence.desc(),
        )
        .label("latest_rank"),
    )
    if campaign_ids is not None:
        statement = statement.where(AccessReviewDecision.campaign_id.in_(campaign_ids))
    return statement.subquery("access_review_decisions_ranked")


def _latest_receipt_rows(*, campaign_ids: tuple[uuid.UUID, ...] | None = None):
    statement = select(
        AccessReviewApplyReceipt,
        func.row_number()
        .over(
            partition_by=AccessReviewApplyReceipt.item_id,
            order_by=AccessReviewApplyReceipt.attempt.desc(),
        )
        .label("latest_rank"),
    )
    if campaign_ids is not None:
        statement = statement.where(
            AccessReviewApplyReceipt.campaign_id.in_(campaign_ids)
        )
    return statement.subquery("access_review_receipts_ranked")


def _item_filters(
    *,
    campaign_id: uuid.UUID,
    latest_decision,
    latest_receipt,
    item_type: str | None,
    principal_type: str | None,
    decision: str | None,
    apply_outcome: str | None,
) -> list[object]:
    filters: list[object] = [AccessReviewItem.campaign_id == campaign_id]
    if item_type is not None:
        filters.append(AccessReviewItem.item_type == item_type)
    if principal_type is not None:
        filters.append(AccessReviewItem.principal_type == principal_type)
    if decision == "undecided":
        filters.append(latest_decision.id.is_(None))
    elif decision is not None:
        filters.append(latest_decision.decision == decision)
    if apply_outcome == "not_applied":
        filters.append(latest_receipt.id.is_(None))
    elif apply_outcome is not None:
        filters.append(latest_receipt.outcome == apply_outcome)
    return filters


def _campaign_response(row, *, now: datetime) -> AccessReviewCampaignResponse:
    campaign = row[0]
    return AccessReviewCampaignResponse(
        id=campaign.id,
        name=campaign.name,
        description=campaign.description,
        scope_snapshot=dict(campaign.scope_snapshot or {}),
        scope_digest=campaign.scope_digest,
        snapshot_at=campaign.snapshot_at,
        review_due_at=campaign.review_due_at,
        is_overdue=(
            campaign.status == "open"
            and _as_utc(campaign.review_due_at) <= _as_utc(now)
        ),
        item_count=campaign.item_count,
        decided_item_count=int(row.decided_item_count),
        revoke_item_count=int(row.revoke_item_count),
        apply_terminal_item_count=int(row.apply_terminal_item_count),
        created_by_user_id=campaign.created_by_user_id,
        created_by_email_snapshot=campaign.created_by_email_snapshot,
        status=campaign.status,
        revision=campaign.revision,
        closed_by_user_id=campaign.closed_by_user_id,
        closed_by_email_snapshot=campaign.closed_by_email_snapshot,
        closed_at=campaign.closed_at,
        close_reason=campaign.close_reason,
        apply_started_by_user_id=campaign.apply_started_by_user_id,
        apply_started_by_email_snapshot=campaign.apply_started_by_email_snapshot,
        apply_started_at=campaign.apply_started_at,
        apply_run_id=campaign.apply_run_id,
        applied_by_user_id=campaign.applied_by_user_id,
        applied_by_email_snapshot=campaign.applied_by_email_snapshot,
        applied_at=campaign.applied_at,
        cancelled_by_user_id=campaign.cancelled_by_user_id,
        cancelled_by_principal_type=campaign.cancelled_by_principal_type,
        cancelled_by_email_snapshot=campaign.cancelled_by_email_snapshot,
        cancelled_at=campaign.cancelled_at,
        cancel_reason=campaign.cancel_reason,
        quarantined_by_user_id=campaign.quarantined_by_user_id,
        quarantined_by_principal_type=campaign.quarantined_by_principal_type,
        quarantined_by_email_snapshot=campaign.quarantined_by_email_snapshot,
        quarantined_at=campaign.quarantined_at,
        quarantine_reason=campaign.quarantine_reason,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


def _item_response(
    item: AccessReviewItem,
    decision: AccessReviewDecision | None,
    receipt: AccessReviewApplyReceipt | None,
) -> AccessReviewItemResponse:
    return AccessReviewItemResponse(
        id=item.id,
        campaign_id=item.campaign_id,
        ordinal=item.ordinal,
        item_type=item.item_type,
        assignment_id=item.assignment_id,
        assignment_source=item.assignment_source,
        assignment_revision_snapshot=item.assignment_revision_snapshot,
        assignment_fingerprint=item.assignment_fingerprint,
        principal_type=item.principal_type,
        principal_id_snapshot=item.principal_id_snapshot,
        principal_label_snapshot=item.principal_label_snapshot,
        target_type=item.target_type,
        target_id_snapshot=item.target_id_snapshot,
        target_key_snapshot=item.target_key_snapshot,
        target_label_snapshot=item.target_label_snapshot,
        target_revision_snapshot=item.target_revision_snapshot,
        permissions_snapshot=list(item.permissions_snapshot or []),
        provenance_snapshot=dict(item.provenance_snapshot or {}),
        assignment_created_at_snapshot=item.assignment_created_at_snapshot,
        access_expires_at_snapshot=item.access_expires_at_snapshot,
        created_at=item.created_at,
        latest_decision=(
            AccessReviewDecisionResponse.model_validate(decision)
            if decision is not None
            else None
        ),
        latest_apply_receipt=(
            AccessReviewApplyReceiptResponse.model_validate(receipt)
            if receipt is not None
            else None
        ),
    )


def _require_campaign(db: Session, campaign_id: uuid.UUID) -> None:
    exists = db.scalar(
        select(AccessReviewCampaign.id).where(AccessReviewCampaign.id == campaign_id)
    )
    if exists is None:
        raise AccessReviewNotFound("Access-review campaign not found.")


def _validate_page(*, page: int, page_size: int) -> None:
    if page < 1:
        raise AccessReviewQueryInvalid("Access-review page must be at least 1.")
    if page_size < 1 or page_size > MAX_ACCESS_REVIEW_QUERY_PAGE_SIZE:
        raise AccessReviewQueryInvalid(
            "Access-review page size must be between 1 and "
            f"{MAX_ACCESS_REVIEW_QUERY_PAGE_SIZE}."
        )


def _validate_filter(
    name: str,
    value: str | None,
    allowed: frozenset[str],
) -> None:
    if value is None or value in allowed:
        return
    expected = ", ".join(sorted(allowed))
    raise AccessReviewQueryInvalid(
        f"Unsupported access-review {name} {value!r}; expected one of: {expected}."
    )


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(database_clock(db)))
    if not isinstance(value, datetime):
        raise AccessReviewQueryInvalid(
            "The database clock could not be read; access-review timing is unavailable."
        )
    return _as_utc(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "MAX_ACCESS_REVIEW_QUERY_PAGE_SIZE",
    "AccessReviewQueryInvalid",
    "get_access_review_campaign",
    "list_access_review_campaigns",
    "list_access_review_items",
]
