"""Authorized team review of evidence; historical verdicts never imply freshness."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session, load_only

from app.core.api_errors import ApiHTTPException
from app.models.article import Article
from app.models.feed import Feed
from app.models.intel_assessment import (
    IndicatorAssessment,
    IndicatorAssessmentHistory,
    IndicatorSuppression,
    ItemIntelState,
)
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.schemas.intel_assessments import (
    AssessmentCommand,
    AssessmentResponse,
    IndicatorPage,
    IndicatorResponse,
)
from app.services.data_access_policy import handling_label_access_predicate
from app.services.export_job_access import (
    ExportJobAccessDenied,
    fence_export_authorization,
)
from app.services.indicator_evidence import (
    current_ai_indicator_links,
    indicator_exclusion,
)
from app.services.indicator_lineage import (
    assessment_access_predicate,
    capture_assessment_labels,
)
from app.services.team_access import (
    assert_current_team_access,
    lock_team_for_current_access,
    team_access_predicate,
)
from app.services.team_assessment_access import (
    AssessmentRequest,
    RequestPrincipal,
    credential_error,
)


def expired(value: datetime | None) -> bool:
    if value is None:
        return False
    return (
        value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    ) <= datetime.now(timezone.utc)


def fence_indicator_request(
    db: Session,
    actor: AssessmentRequest,
    *,
    team_id: uuid.UUID | None,
    write: bool = False,
    manage: bool = False,
) -> None:
    permissions = (
        "read:items",
        *(("read:teams",) if team_id else ()),
        *(("write:teams",) if write else ()),
    )
    try:
        fence_export_authorization(
            db,
            RequestPrincipal(actor.user.id),
            actor.authorization,
            actor.access,
            snapshot=actor.snapshot,
            required_permissions=permissions,
        )
    except ExportJobAccessDenied as exc:
        raise credential_error() from exc
    if team_id is not None:
        team = lock_team_for_current_access(
            db, team_id=team_id, user_id=actor.user.id, for_update=write, manage=manage
        )
        if team is None:
            raise ApiHTTPException(
                status_code=404,
                error_code="team_not_found",
                detail="Team not found or your current membership does not permit this action.",
            )


def load_indicator_item(
    db: Session, actor: AssessmentRequest, item_id: uuid.UUID, *, write: bool = False
) -> tuple[Item, Feed, int]:
    query = (
        select(Item, Feed)
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            Item.id == item_id,
            handling_label_access_predicate(Feed.handling_label_id, actor.access),
        )
    )
    query = query.options(
        load_only(Item.id, Item.feed_id, Item.classification_required_version),
        load_only(Feed.id, Feed.handling_label_id),
    )
    if write:
        query = query.with_for_update(read=True, of=Item)
    row = db.execute(query.execution_options(populate_existing=True)).one_or_none()
    if row is None:
        raise ApiHTTPException(
            status_code=404, error_code="item_not_found", detail="Article not found."
        )
    revision_query = select(ItemIntelState).where(ItemIntelState.item_id == item_id)
    if write:
        revision_query = revision_query.with_for_update(read=True)
    state = db.scalar(revision_query)
    if (
        state is not None
        and state.handling_label_id is not None
        and not db.scalar(
            select(
                handling_label_access_predicate(
                    literal(state.handling_label_id), actor.access
                )
            )
        )
    ):
        raise ApiHTTPException(
            status_code=404,
            error_code="item_not_found",
            detail="Article evidence not found.",
        )
    return row.Item, row.Feed, state.revision if state else 0


def extraction_is_current(db: Session, item: Item) -> bool:
    from app.services.intel_events import source_fingerprint

    state = db.get(ItemIntelState, item.id)
    article = db.execute(
        select(Article.id, Article.retrieved_at, Article.content_purged_at).where(
            Article.item_id == item.id
        )
    ).one_or_none()
    return state is not None and state.source_fingerprint == source_fingerprint(
        item, article
    )


def assessment_response(
    row: IndicatorAssessment, *, source_revision: int, extraction_revision: int
) -> AssessmentResponse:
    is_expired = expired(row.expires_at)
    return AssessmentResponse(
        version=row.version,
        verdict=row.verdict,
        reason=row.reason,
        source_revision=row.source_revision,
        extraction_revision=row.extraction_revision,
        expires_at=row.expires_at,
        expired=is_expired,
        current=not is_expired
        and row.source_revision == source_revision
        and row.extraction_revision == extraction_revision,
        updated_at=row.updated_at,
    )


def list_indicators(
    db: Session,
    *,
    actor: AssessmentRequest,
    item_id: uuid.UUID,
    team_id: uuid.UUID | None,
    page: int,
    page_size: int,
) -> IndicatorPage:
    fence_indicator_request(db, actor, team_id=team_id)
    item, _feed, revision = load_indicator_item(db, actor, item_id)
    extraction_current = extraction_is_current(db, item)
    total = (
        db.scalar(
            select(func.count()).select_from(ItemIOC).where(ItemIOC.item_id == item_id)
        )
        or 0
    )
    rows = db.execute(
        select(IOC, ItemIOC)
        .join(ItemIOC, ItemIOC.ioc_id == IOC.id)
        .where(ItemIOC.item_id == item_id)
        .order_by(IOC.type, IOC.value_norm, IOC.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    ids = [row.IOC.id for row in rows]
    assessments, suppressions = {}, set()
    if team_id is not None and ids:
        assessments = {
            row.ioc_id: row
            for row in db.scalars(
                select(IndicatorAssessment).where(
                    IndicatorAssessment.team_id == team_id,
                    IndicatorAssessment.item_id == item_id,
                    IndicatorAssessment.ioc_id.in_(ids),
                    assessment_access_predicate(actor.access),
                )
            )
        }
        suppression_rows = db.execute(
            select(
                IndicatorSuppression.ioc_type,
                IndicatorSuppression.value_norm,
                IndicatorSuppression.expires_at,
            )
            .join(
                IOC,
                (IOC.type == IndicatorSuppression.ioc_type)
                & (IOC.value_norm == IndicatorSuppression.value_norm),
            )
            .where(
                IndicatorSuppression.team_id == team_id,
                IndicatorSuppression.active.is_(True),
                IOC.id.in_(ids),
            )
        ).all()
        suppressions = {
            (row.ioc_type, row.value_norm)
            for row in suppression_rows
            if not expired(row.expires_at)
        }
    links = current_ai_indicator_links(db, item_id)
    results = []
    for ioc, occurrence in rows:
        ai = links.get((ioc.type, ioc.value_norm))
        reasons = indicator_exclusion(ioc.type, ioc.value_norm, ai)
        assessment = (
            assessment_response(
                assessments[ioc.id],
                source_revision=item.classification_required_version,
                extraction_revision=revision,
            )
            if ioc.id in assessments
            else None
        )
        if assessment is not None and not extraction_current:
            assessment.current = False
        if assessment and assessment.current:
            if assessment.verdict in {"benign", "reference", "example", "retracted"}:
                reasons.append(f"analyst_{assessment.verdict}")
            elif assessment.verdict == "malicious":
                reasons = [reason for reason in reasons if not reason.startswith("ai_")]
        suppressed = (ioc.type, ioc.value_norm) in suppressions
        if suppressed:
            reasons.append("team_suppression")
        evidence = occurrence.evidence_json or []
        results.append(
            IndicatorResponse(
                id=ioc.id,
                type=ioc.type,
                value=ioc.value_norm,
                raw=evidence[0]["raw"] if evidence else ioc.value_norm,
                extraction_confidence=occurrence.confidence,
                occurrences=occurrence.occurrences,
                evidence_truncated=occurrence.occurrences > len(evidence),
                evidence=evidence,
                ai=ai,
                ai_current=ai is not None,
                assessment=assessment,
                excluded=bool(reasons),
                exclusion_reasons=reasons,
                suppressed=suppressed,
            )
        )
    if team_id is not None:
        assert_current_team_access(db, team_id=team_id, user_id=actor.user.id)
    fence_indicator_request(db, actor, team_id=team_id)
    can_manage = bool(
        team_id
        and actor.authorization.has("write:teams")
        and db.scalar(
            select(team_access_predicate(team_id, actor.user.id, manage=True))
        )
    )
    return IndicatorPage(
        items=results,
        total=total,
        page=page,
        page_size=page_size,
        source_revision=item.classification_required_version,
        extraction_revision=revision,
        extraction_current=extraction_current,
        can_review=bool(
            extraction_current and team_id and actor.authorization.has("write:teams")
        ),
        can_manage_suppressions=can_manage,
    )


def _conflict(message: str) -> ApiHTTPException:
    return ApiHTTPException(
        status_code=409, error_code="indicator_revision_conflict", detail=message
    )


def update_assessment(
    db: Session,
    *,
    actor: AssessmentRequest,
    item_id: uuid.UUID,
    ioc_id: uuid.UUID,
    team_id: uuid.UUID,
    payload: AssessmentCommand,
) -> AssessmentResponse:
    fence_indicator_request(db, actor, team_id=team_id, write=True)
    item, feed, revision = load_indicator_item(db, actor, item_id, write=True)
    if (
        not extraction_is_current(db, item)
        or payload.source_revision != item.classification_required_version
        or payload.extraction_revision != revision
    ):
        raise _conflict(
            "The source or extracted evidence changed. Refresh the indicators before reviewing."
        )
    if db.get(ItemIOC, (item_id, ioc_id)) is None:
        raise ApiHTTPException(
            status_code=404,
            error_code="indicator_not_found",
            detail="This indicator is no longer present in the article.",
        )
    row = db.scalar(
        select(IndicatorAssessment)
        .where(
            IndicatorAssessment.team_id == team_id,
            IndicatorAssessment.item_id == item_id,
            IndicatorAssessment.ioc_id == ioc_id,
        )
        .with_for_update()
    )
    if row is not None and not db.scalar(
        select(IndicatorAssessment.id).where(
            IndicatorAssessment.id == row.id,
            assessment_access_predicate(actor.access),
        )
    ):
        raise ApiHTTPException(
            status_code=404,
            error_code="indicator_assessment_not_found",
            detail="Assessment not found.",
        )
    if (row.version if row else 0) != payload.expected_version:
        raise _conflict(
            "Another analyst changed this assessment. Refresh it before saving."
        )
    if row is None:
        row = IndicatorAssessment(
            team_id=team_id,
            item_id=item_id,
            ioc_id=ioc_id,
            handling_label_id=feed.handling_label_id,
            version=0,
        )
        db.add(row)
    row.version += 1
    row.source_revision, row.extraction_revision = payload.source_revision, revision
    row.verdict, row.reason, row.expires_at = (
        payload.verdict,
        payload.reason,
        payload.expires_at,
    )
    row.updated_by_user_id, row.updated_at = actor.user.id, datetime.now(timezone.utc)
    db.flush()
    capture_assessment_labels(db, row, current_label_id=feed.handling_label_id)
    result = assessment_response(
        row,
        source_revision=item.classification_required_version,
        extraction_revision=revision,
    )
    db.add(
        IndicatorAssessmentHistory(
            assessment_id=row.id,
            version=row.version,
            snapshot_json=result.model_dump(mode="json"),
            actor_user_id=actor.user.id,
        )
    )
    assert_current_team_access(db, team_id=team_id, user_id=actor.user.id)
    fence_indicator_request(db, actor, team_id=team_id, write=True)
    from app.services.intel_events import emit_team_indicator_change

    emit_team_indicator_change(
        db, team_id=team_id, item=item, actor_user_id=actor.user.id
    )
    return result
