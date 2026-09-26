"""Exact-match team suppression rules with manager-only versioned changes."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.models.intel_assessment import (
    IndicatorAssessment,
    IndicatorAssessmentHistory,
    IndicatorSuppression,
    IndicatorSuppressionHistory,
)
from app.schemas.intel_assessments import (
    IndicatorHistoryEntry,
    IndicatorHistoryPage,
    SuppressionCreate,
    SuppressionPage,
    SuppressionResponse,
    SuppressionUpdate,
)
from app.services.data_access_policy import handling_label_access_predicate
from app.services.indicator_assessments import (
    _conflict,
    expired,
    fence_indicator_request,
    load_indicator_item,
)
from app.services.indicator_evidence import canonical_indicator
from app.services.team_access import assert_current_team_access, team_access_predicate
from app.services.team_assessment_access import AssessmentRequest


def suppression_response(row: IndicatorSuppression) -> SuppressionResponse:
    return SuppressionResponse(
        id=row.id,
        team_id=row.team_id,
        ioc_type=row.ioc_type,
        value=row.value_norm,
        version=row.version,
        reason=row.reason,
        active=row.active,
        expires_at=row.expires_at,
        expired=expired(row.expires_at),
        updated_at=row.updated_at,
    )


def list_suppressions(
    db: Session,
    *,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    page: int,
    page_size: int,
) -> SuppressionPage:
    fence_indicator_request(db, actor, team_id=team_id)
    query = select(IndicatorSuppression).where(IndicatorSuppression.team_id == team_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(
            IndicatorSuppression.ioc_type,
            IndicatorSuppression.value_norm,
            IndicatorSuppression.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    assert_current_team_access(db, team_id=team_id, user_id=actor.user.id)
    return SuppressionPage(
        items=[suppression_response(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        can_manage=actor.authorization.has("write:teams")
        and bool(
            db.scalar(
                select(team_access_predicate(team_id, actor.user.id, manage=True))
            )
        ),
    )


def save_suppression(
    db: Session,
    *,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    payload: SuppressionCreate | SuppressionUpdate,
    suppression_id: uuid.UUID | None = None,
) -> SuppressionResponse:
    fence_indicator_request(db, actor, team_id=team_id, write=True, manage=True)
    if isinstance(payload, SuppressionCreate):
        value = canonical_indicator(payload.ioc_type, payload.value.strip())
        if value is None:
            raise ApiHTTPException(
                status_code=422,
                error_code="indicator_value_invalid",
                detail="Enter exactly one valid indicator matching the selected type.",
            )
        existing = db.scalar(
            select(IndicatorSuppression.id).where(
                IndicatorSuppression.team_id == team_id,
                IndicatorSuppression.ioc_type == payload.ioc_type,
                IndicatorSuppression.value_norm == value,
            )
        )
        if existing is not None:
            raise _conflict(
                "An exclusion for this indicator already exists. Update the existing rule."
            )
        row = IndicatorSuppression(
            team_id=team_id, ioc_type=payload.ioc_type, value_norm=value, version=1
        )
        db.add(row)
    else:
        row = db.scalar(
            select(IndicatorSuppression)
            .where(
                IndicatorSuppression.id == suppression_id,
                IndicatorSuppression.team_id == team_id,
            )
            .with_for_update()
        )
        if row is None:
            raise ApiHTTPException(
                status_code=404,
                error_code="indicator_suppression_not_found",
                detail="Suppression rule not found.",
            )
        if row.version != payload.expected_version:
            raise _conflict(
                "Another manager changed this rule. Refresh it before saving."
            )
        row.version += 1
    row.reason, row.active, row.expires_at = (
        payload.reason,
        payload.active,
        payload.expires_at,
    )
    row.updated_at, row.updated_by_user_id = datetime.now(timezone.utc), actor.user.id
    db.flush()
    response = suppression_response(row)
    db.add(
        IndicatorSuppressionHistory(
            suppression_id=row.id,
            version=row.version,
            snapshot_json=response.model_dump(mode="json"),
            actor_user_id=actor.user.id,
        )
    )
    assert_current_team_access(db, team_id=team_id, user_id=actor.user.id, manage=True)
    fence_indicator_request(db, actor, team_id=team_id, write=True, manage=True)
    return response


def history_page(
    db: Session,
    *,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    page: int,
    page_size: int,
    item_id: uuid.UUID | None = None,
    ioc_id: uuid.UUID | None = None,
    suppression_id: uuid.UUID | None = None,
) -> IndicatorHistoryPage:
    fence_indicator_request(db, actor, team_id=team_id)
    if item_id is not None:
        load_indicator_item(db, actor, item_id)
        parent_id = db.scalar(
            select(IndicatorAssessment.id).where(
                IndicatorAssessment.team_id == team_id,
                IndicatorAssessment.item_id == item_id,
                IndicatorAssessment.ioc_id == ioc_id,
                handling_label_access_predicate(
                    IndicatorAssessment.handling_label_id, actor.access
                ),
            )
        )
        model, foreign = (
            IndicatorAssessmentHistory,
            IndicatorAssessmentHistory.assessment_id,
        )
    else:
        parent_id = db.scalar(
            select(IndicatorSuppression.id).where(
                IndicatorSuppression.id == suppression_id,
                IndicatorSuppression.team_id == team_id,
            )
        )
        model, foreign = (
            IndicatorSuppressionHistory,
            IndicatorSuppressionHistory.suppression_id,
        )
    if parent_id is None:
        return IndicatorHistoryPage(items=[], total=0, page=page, page_size=page_size)
    query = select(model).where(foreign == parent_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(model.version.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = IndicatorHistoryPage(
        items=[
            IndicatorHistoryEntry(
                version=row.version,
                snapshot=row.snapshot_json,
                actor_user_id=row.actor_user_id,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
    assert_current_team_access(db, team_id=team_id, user_id=actor.user.id)
    return result
