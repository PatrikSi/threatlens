from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_usage_event import AIUsageEvent
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.schemas.ai import (
    AIDailyBriefItemResponse,
    AIDailyBriefResponse,
    AIUsageFeatureSummary,
    AIUsageSummaryResponse,
)
from app.services.ai_normalization import normalize_string_list
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    data_access_envelope_predicate,
)
from app.services.data_access_retention import prune_deleted_resource_envelopes
from app.services.data_access_policy import DataAccessContext
from app.services.ai_telemetry_data_policy import ai_usage_event_access_predicate
from app.services.url_utils import normalize_url


def get_latest_daily_brief(
    db: Session, *, data_access: DataAccessContext | None = None
) -> AIDailyBrief | None:
    access_predicate = (
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_DAILY_BRIEF,
            AIDailyBrief.id,
            data_access,
        )
        if data_access is not None
        else True
    )
    return db.scalar(
        select(AIDailyBrief)
        .where(AIDailyBrief.status == "ready", access_predicate)
        .order_by(
            AIDailyBrief.brief_date.desc(), AIDailyBrief.generated_at.desc().nullslast()
        )
    )


def get_recent_daily_briefs(
    db: Session,
    *,
    limit: int,
    data_access: DataAccessContext | None = None,
) -> list[AIDailyBrief]:
    access_predicate = (
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_DAILY_BRIEF,
            AIDailyBrief.id,
            data_access,
        )
        if data_access is not None
        else True
    )
    return list(
        db.scalars(
            select(AIDailyBrief)
            .where(AIDailyBrief.status == "ready", access_predicate)
            .order_by(
                AIDailyBrief.brief_date.desc(),
                AIDailyBrief.generated_at.desc().nullslast(),
            )
            .limit(limit)
        )
    )


def prune_daily_brief_history(db: Session, *, keep_limit: int) -> int:
    if keep_limit < 1:
        return 0

    stale_ids = list(
        db.scalars(
            select(AIDailyBrief.id)
            .order_by(AIDailyBrief.brief_date.desc(), AIDailyBrief.created_at.desc())
            .offset(keep_limit)
        )
    )
    if not stale_ids:
        return 0

    db.execute(delete(AIDailyBrief).where(AIDailyBrief.id.in_(stale_ids)))
    prune_deleted_resource_envelopes(
        db,
        resources=(
            (DATA_ACCESS_RESOURCE_DAILY_BRIEF, brief_id) for brief_id in stale_ids
        ),
    )
    return len(stale_ids)


def daily_brief_response_from_model(
    db: Session, brief: AIDailyBrief
) -> AIDailyBriefResponse:
    item_ids = [uuid.UUID(value) for value in (brief.top_item_ids_json or []) if value]
    rows = (
        db.execute(
            select(
                Item.id,
                Item.title,
                Item.url,
                Item.published_at,
                Feed.name.label("feed_name"),
                ItemAIEnrichment.relevance_score.label("relevance_score"),
                ItemAIEnrichment.relevance_label.label("relevance_label"),
            )
            .join(Feed, Feed.id == Item.feed_id)
            .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
            .where(Item.id.in_(item_ids))
        ).all()
        if item_ids
        else []
    )
    row_by_id = {row.id: row for row in rows}
    items = [
        AIDailyBriefItemResponse(
            id=item_id,
            title=row_by_id[item_id].title,
            feed_name=row_by_id[item_id].feed_name,
            url=normalize_url(row_by_id[item_id].url),
            published_at=row_by_id[item_id].published_at,
            relevance_score=float(row_by_id[item_id].relevance_score)
            if row_by_id[item_id].relevance_score is not None
            else None,
            relevance_label=row_by_id[item_id].relevance_label,
        )
        for item_id in item_ids
        if item_id in row_by_id
    ]
    return AIDailyBriefResponse(
        id=brief.id,
        brief_date=brief.brief_date,
        status=brief.status,
        window_start=brief.window_start,
        window_end=brief.window_end,
        title=brief.title,
        brief_text=brief.brief_text,
        key_points=normalize_string_list(list(brief.key_points_json or [])),
        recommended_actions=normalize_string_list(
            list(brief.recommended_actions_json or [])
        ),
        item_count=int(brief.item_count or 0),
        items=items,
        model=brief.model,
        generated_at=brief.generated_at,
        error=brief.error,
    )


def get_ai_usage_summary(
    db: Session,
    *,
    data_access: DataAccessContext | None = None,
) -> AIUsageSummaryResponse:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=24)
    access_predicate = (
        ai_usage_event_access_predicate(data_access)
        if data_access is not None
        else True
    )

    totals_row = db.execute(
        select(
            func.count(AIUsageEvent.id).label("total_requests"),
            func.sum(case((AIUsageEvent.success.is_(True), 1), else_=0)).label(
                "successful_requests"
            ),
            func.sum(case((AIUsageEvent.success.is_(False), 1), else_=0)).label(
                "failed_requests"
            ),
            func.sum(func.coalesce(AIUsageEvent.prompt_tokens, 0)).label(
                "total_prompt_tokens"
            ),
            func.sum(func.coalesce(AIUsageEvent.completion_tokens, 0)).label(
                "total_completion_tokens"
            ),
            func.sum(func.coalesce(AIUsageEvent.total_tokens, 0)).label("total_tokens"),
            func.avg(AIUsageEvent.latency_ms).label("average_latency_ms"),
            func.max(AIUsageEvent.created_at).label("last_request_at"),
        ).where(access_predicate)
    ).one()
    requests_last_24h = (
        db.scalar(
            select(func.count(AIUsageEvent.id)).where(
                AIUsageEvent.created_at >= window_start,
                access_predicate,
            )
        )
        or 0
    )

    feature_rows = db.execute(
        select(
            AIUsageEvent.feature_type,
            func.count(AIUsageEvent.id).label("total_requests"),
            func.sum(case((AIUsageEvent.success.is_(True), 1), else_=0)).label(
                "successful_requests"
            ),
            func.sum(case((AIUsageEvent.success.is_(False), 1), else_=0)).label(
                "failed_requests"
            ),
            func.sum(func.coalesce(AIUsageEvent.total_tokens, 0)).label("total_tokens"),
            func.avg(AIUsageEvent.latency_ms).label("average_latency_ms"),
            func.max(AIUsageEvent.created_at).label("last_request_at"),
        )
        .where(access_predicate)
        .group_by(AIUsageEvent.feature_type)
        .order_by(AIUsageEvent.feature_type.asc())
    ).all()

    features = [
        AIUsageFeatureSummary(
            feature_type=row.feature_type,
            total_requests=int(row.total_requests or 0),
            successful_requests=int(row.successful_requests or 0),
            failed_requests=int(row.failed_requests or 0),
            total_tokens=int(row.total_tokens or 0),
            average_latency_ms=round(float(row.average_latency_ms or 0.0), 2),
            last_request_at=row.last_request_at,
        )
        for row in feature_rows
    ]

    total_requests = int(totals_row.total_requests or 0)
    successful_requests = int(totals_row.successful_requests or 0)
    failed_requests = int(totals_row.failed_requests or 0)
    success_rate = (
        (successful_requests / total_requests * 100.0) if total_requests else 0.0
    )
    return AIUsageSummaryResponse(
        total_requests=total_requests,
        successful_requests=successful_requests,
        failed_requests=failed_requests,
        success_rate_pct=round(success_rate, 2),
        requests_last_24h=int(requests_last_24h or 0),
        total_prompt_tokens=int(totals_row.total_prompt_tokens or 0),
        total_completion_tokens=int(totals_row.total_completion_tokens or 0),
        total_tokens=int(totals_row.total_tokens or 0),
        average_latency_ms=round(float(totals_row.average_latency_ms or 0.0), 2),
        last_request_at=totals_row.last_request_at,
        features=features,
    )
