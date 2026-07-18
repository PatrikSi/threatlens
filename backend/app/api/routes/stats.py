import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_token_scopes
from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_STATS
from app.db.session import get_db
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.user import User
from app.services.classification import CLASSIFICATION_CATEGORIES
from app.schemas.stats import (
    ActivityHeatmapDayRow,
    ActivityHeatmapResponse,
    ActivitySummary,
    DailyVolumePoint,
    DerivedSummary,
    DomainPoint,
    FeedTimeSeriesPoint,
    FeedTimeSeriesResponse,
    FeedTimeSeriesSeries,
    FeedStats,
    SignalRadarAxisPoint,
    SignalRadarResponse,
    StatsOverviewResponse,
    StatusPoint,
    TotalsSummary,
)

router = APIRouter(prefix="/stats", tags=["stats"])


@dataclass(frozen=True)
class StatsWindow:
    generated_at: datetime
    start_at: datetime
    start_date: date


def _build_stats_window(days: int) -> StatsWindow:
    generated_at = datetime.now(timezone.utc)
    window_start_date = (generated_at - timedelta(days=days - 1)).date()
    start_at = datetime.combine(window_start_date, datetime.min.time(), tzinfo=timezone.utc)
    return StatsWindow(generated_at=generated_at, start_at=start_at, start_date=window_start_date)


def _parse_feed_ids(feed_ids: str | None) -> list[uuid.UUID]:
    if not feed_ids:
        return []

    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in feed_ids.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            feed_uuid = uuid.UUID(candidate)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid feed id: {candidate}",
            ) from exc
        if feed_uuid not in seen:
            parsed.append(feed_uuid)
            seen.add(feed_uuid)
    return parsed


@router.get("/overview", response_model=StatsOverviewResponse)
def get_stats_overview(
    days: int = Query(default=30, ge=7, le=365),
    feed_ids: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_STATS)),
):
    settings = get_settings()
    selected_feed_ids = _parse_feed_ids(feed_ids)
    window = _build_stats_window(days)
    timeline_at = func.coalesce(Item.published_at, Item.first_seen_at)

    feed_filters = [Feed.id.in_(selected_feed_ids)] if selected_feed_ids else []
    item_filters = [Item.feed_id.in_(selected_feed_ids)] if selected_feed_ids else []

    feed_counts_query = select(
        func.count(Feed.id).label("feeds_total"),
        func.sum(case((Feed.enabled.is_(True), 1), else_=0)).label("feeds_enabled"),
    )
    if feed_filters:
        feed_counts_query = feed_counts_query.where(*feed_filters)

    feed_counts = db.execute(feed_counts_query).one()
    feeds_total = int(feed_counts.feeds_total or 0)
    feeds_enabled = int(feed_counts.feeds_enabled or 0)

    item_counts_query = select(
        func.count(Item.id).label("items_total"),
        func.sum(case((Item.status == "new", 1), else_=0)).label("items_new"),
        func.sum(case((Item.status == "content_fetched", 1), else_=0)).label("items_content_fetched"),
        func.sum(case((Item.status == "error", 1), else_=0)).label("items_error"),
        func.sum(case((Item.first_seen_at >= window.generated_at - timedelta(hours=24), 1), else_=0)).label("items_last_24h"),
        func.sum(case((Item.first_seen_at >= window.generated_at - timedelta(days=7), 1), else_=0)).label("items_last_7d"),
        func.sum(case((Item.first_seen_at >= window.generated_at - timedelta(days=30), 1), else_=0)).label("items_last_30d"),
    )
    if item_filters:
        item_counts_query = item_counts_query.where(*item_filters)

    item_counts = db.execute(item_counts_query).one()
    items_total = int(item_counts.items_total or 0)
    items_new = int(item_counts.items_new or 0)
    items_content_fetched = int(item_counts.items_content_fetched or 0)
    items_error = int(item_counts.items_error or 0)
    items_last_24h = int(item_counts.items_last_24h or 0)
    items_last_7d = int(item_counts.items_last_7d or 0)
    items_last_30d = int(item_counts.items_last_30d or 0)

    articles_query = select(func.count()).select_from(Article)
    if item_filters:
        articles_query = articles_query.join(Item, Item.id == Article.item_id).where(*item_filters)
    articles_total = db.scalar(articles_query) or 0

    status_query = select(Item.status, func.count())
    if item_filters:
        status_query = status_query.where(*item_filters)
    status_rows = db.execute(status_query.group_by(Item.status).order_by(func.count().desc())).all()
    status_breakdown = [StatusPoint(status=status, count=int(count)) for status, count in status_rows]

    volume_query = (
        select(func.date(timeline_at).label("date_key"), func.count(Item.id).label("count"))
        .where(timeline_at >= window.start_at)
        .group_by(func.date(timeline_at))
        .order_by(func.date(timeline_at).asc())
    )
    if item_filters:
        volume_query = volume_query.where(*item_filters)
    volume_rows = db.execute(volume_query).all()
    daily_volume = [DailyVolumePoint(date=str(date_key), count=int(count or 0)) for date_key, count in volume_rows if date_key]

    feed_rows_query = (
        select(
            Feed.id,
            Feed.name,
            func.count(Item.id).label("total_items"),
            func.sum(case((timeline_at >= window.start_at, 1), else_=0)).label("items_in_window"),
            func.sum(case((Item.status == "error", 1), else_=0)).label("error_items"),
            func.sum(case((Item.status == "content_fetched", 1), else_=0)).label("content_fetched_items"),
            func.max(Item.published_at).label("last_published_at"),
            func.max(Item.first_seen_at).label("last_seen_at"),
        )
        .outerjoin(Item, Item.feed_id == Feed.id)
        .group_by(Feed.id, Feed.name)
        .order_by(func.count(Item.id).desc(), Feed.name.asc())
    )
    if feed_filters:
        feed_rows_query = feed_rows_query.where(*feed_filters)
    feed_rows = db.execute(feed_rows_query).all()

    feed_breakdown = [
        FeedStats(
            feed_id=str(feed_id),
            feed_name=feed_name,
            total_items=int(total_items or 0),
            items_in_window=int(items_in_window or 0),
            error_items=int(error_items or 0),
            content_fetched_items=int(content_fetched_items or 0),
            last_published_at=last_published_at,
            last_seen_at=last_seen_at,
        )
        for (
            feed_id,
            feed_name,
            total_items,
            items_in_window,
            error_items,
            content_fetched_items,
            last_published_at,
            last_seen_at,
        ) in feed_rows
    ]

    top_domains = _load_top_domains(
        db,
        window_start=window.start_at,
        timeline_at=timeline_at,
        item_filters=item_filters,
        limit=settings.stats_top_domains_limit,
    )

    extraction_success_rate = (items_content_fetched / items_total * 100.0) if items_total else 0.0
    error_rate = (items_error / items_total * 100.0) if items_total else 0.0
    avg_items_per_day_window = (sum(point.count for point in daily_volume) / days) if days else 0.0

    return StatsOverviewResponse(
        generated_at=window.generated_at,
        window_days=days,
        window_start_at=window.start_at,
        window_end_at=window.generated_at,
        totals=TotalsSummary(
            feeds_total=int(feeds_total),
            feeds_enabled=int(feeds_enabled),
            feeds_disabled=int(feeds_total - feeds_enabled),
            items_total=int(items_total),
            items_new=int(items_new),
            items_content_fetched=int(items_content_fetched),
            items_error=int(items_error),
            articles_total=int(articles_total),
        ),
        activity=ActivitySummary(
            items_last_24h=int(items_last_24h),
            items_last_7d=int(items_last_7d),
            items_last_30d=int(items_last_30d),
        ),
        derived=DerivedSummary(
            extraction_success_rate_pct=round(extraction_success_rate, 2),
            error_rate_pct=round(error_rate, 2),
            avg_items_per_day_window=round(avg_items_per_day_window, 2),
        ),
        status_breakdown=status_breakdown,
        daily_volume=daily_volume,
        feed_breakdown=feed_breakdown,
        top_domains=top_domains,
    )


@router.get("/feed-timeseries", response_model=FeedTimeSeriesResponse)
def get_feed_timeseries(
    days: int = Query(default=30, ge=7, le=365),
    feed_ids: str | None = Query(default=None),
    top_feeds: int | None = Query(default=None, ge=1, le=500),
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_STATS)),
):
    selected_feed_ids = _parse_feed_ids(feed_ids)
    window = _build_stats_window(days)
    timeline_at = func.coalesce(Item.published_at, Item.first_seen_at)

    target_feed_ids = selected_feed_ids
    if not target_feed_ids:
        target_feed_rows_query = (
            select(Item.feed_id, func.count(Item.id).label("count"))
            .where(timeline_at >= window.start_at)
            .group_by(Item.feed_id)
            .order_by(func.count(Item.id).desc())
        )
        if top_feeds is not None:
            target_feed_rows_query = target_feed_rows_query.limit(top_feeds)
        target_feed_rows = db.execute(target_feed_rows_query).all()
        target_feed_ids = [feed_id for feed_id, _count in target_feed_rows]

    if not target_feed_ids:
        return FeedTimeSeriesResponse(
            generated_at=window.generated_at,
            window_days=days,
            window_start_at=window.start_at,
            window_end_at=window.generated_at,
            series=[],
        )

    feed_rows = db.execute(select(Feed.id, Feed.name).where(Feed.id.in_(target_feed_ids))).all()
    feed_name_by_id = {feed_id: feed_name for feed_id, feed_name in feed_rows}

    time_rows = db.execute(
        select(
            Item.feed_id,
            func.date(timeline_at).label("date_key"),
            func.count(Item.id).label("count"),
        )
        .where(Item.feed_id.in_(target_feed_ids), timeline_at >= window.start_at)
        .group_by(Item.feed_id, func.date(timeline_at))
        .order_by(func.date(timeline_at).asc())
    ).all()

    counts_by_feed_and_date: dict[uuid.UUID, dict[str, int]] = {feed_id: {} for feed_id in target_feed_ids}
    for feed_id, date_key, count in time_rows:
        if date_key is None:
            continue
        counts_by_feed_and_date.setdefault(feed_id, {})[str(date_key)] = int(count or 0)

    date_axis = [(window.start_date + timedelta(days=offset)).isoformat() for offset in range(days)]
    series = [
        FeedTimeSeriesSeries(
            feed_id=str(feed_id),
            feed_name=feed_name_by_id.get(feed_id, str(feed_id)),
            points=[
                FeedTimeSeriesPoint(date=date_key, count=counts_by_feed_and_date.get(feed_id, {}).get(date_key, 0))
                for date_key in date_axis
            ],
        )
        for feed_id in target_feed_ids
    ]

    return FeedTimeSeriesResponse(
        generated_at=window.generated_at,
        window_days=days,
        window_start_at=window.start_at,
        window_end_at=window.generated_at,
        series=series,
    )


@router.get("/activity-heatmap", response_model=ActivityHeatmapResponse)
def get_activity_heatmap(
    days: int = Query(default=30, ge=7, le=365),
    feed_ids: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_STATS)),
):
    selected_feed_ids = _parse_feed_ids(feed_ids)
    window = _build_stats_window(days)
    timeline_at = func.coalesce(Item.published_at, Item.first_seen_at)

    day_axis = [(window.start_date + timedelta(days=offset)).isoformat() for offset in range(days)]
    bucket_unit = "hour" if days <= 7 else "day"
    bucket_labels = [f"{hour:02d}:00" for hour in range(24)] if bucket_unit == "hour" else ["Daily"]
    bucket_size = len(bucket_labels)
    counts_by_day = {day_key: [0] * bucket_size for day_key in day_axis}

    if bucket_unit == "hour":
        heatmap_query = (
            select(
                func.date(timeline_at).label("day_key"),
                cast(func.extract("hour", timeline_at), Integer).label("hour_key"),
                func.count(Item.id).label("count"),
            )
            .where(timeline_at >= window.start_at)
            .group_by(func.date(timeline_at), cast(func.extract("hour", timeline_at), Integer))
        )
        if selected_feed_ids:
            heatmap_query = heatmap_query.where(Item.feed_id.in_(selected_feed_ids))

        for day_key, hour_key, count in db.execute(heatmap_query):
            if day_key is None or hour_key is None:
                continue
            day_key_str = str(day_key)
            hour_index = int(hour_key)
            if day_key_str in counts_by_day and 0 <= hour_index < 24:
                counts_by_day[day_key_str][hour_index] = int(count or 0)
    else:
        heatmap_query = (
            select(func.date(timeline_at).label("day_key"), func.count(Item.id).label("count"))
            .where(timeline_at >= window.start_at)
            .group_by(func.date(timeline_at))
        )
        if selected_feed_ids:
            heatmap_query = heatmap_query.where(Item.feed_id.in_(selected_feed_ids))

        for day_key, count in db.execute(heatmap_query):
            if day_key is None:
                continue
            day_key_str = str(day_key)
            if day_key_str in counts_by_day:
                counts_by_day[day_key_str][0] = int(count or 0)

    rows = [ActivityHeatmapDayRow(day=day_key, counts=counts_by_day[day_key]) for day_key in day_axis]
    max_count = max((count for row in rows for count in row.counts), default=0)

    return ActivityHeatmapResponse(
        generated_at=window.generated_at,
        window_days=days,
        window_start_at=window.start_at,
        window_end_at=window.generated_at,
        bucket_unit=bucket_unit,
        bucket_labels=bucket_labels,
        rows=rows,
        max_count=max_count,
    )


@router.get("/signal-radar", response_model=SignalRadarResponse)
def get_signal_radar(
    days: int = Query(default=30, ge=7, le=365),
    feed_ids: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_STATS)),
):
    selected_feed_ids = _parse_feed_ids(feed_ids)
    window = _build_stats_window(days)

    query = (
        select(ItemClassification.primary_category, func.count(ItemClassification.item_id))
        .join(Item, Item.id == ItemClassification.item_id)
        .where(func.coalesce(Item.published_at, Item.first_seen_at) >= window.start_at)
    )
    if selected_feed_ids:
        query = query.where(Item.feed_id.in_(selected_feed_ids))

    rows = db.execute(query.group_by(ItemClassification.primary_category)).all()
    raw_counts = {category: int(count or 0) for category, count in rows}

    categories = [category for category in CLASSIFICATION_CATEGORIES if category != "multi"] + ["multi"]
    counts_by_category = {category: raw_counts.get(category, 0) for category in categories}
    total = sum(counts_by_category.values())
    max_count = max(counts_by_category.values(), default=0)

    axes = [
        SignalRadarAxisPoint(
            category=category,
            count=count,
            pct=round((count / total) * 100.0, 2) if total else 0.0,
        )
        for category, count in counts_by_category.items()
    ]

    return SignalRadarResponse(
        generated_at=window.generated_at,
        window_days=days,
        window_start_at=window.start_at,
        window_end_at=window.generated_at,
        total=total,
        max_count=max_count,
        axes=axes,
    )


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _load_top_domains(
    db: Session,
    *,
    window_start: datetime,
    timeline_at,
    item_filters: list,
    limit: int,
) -> list[DomainPoint]:
    if limit <= 0:
        return []

    query = (
        select(Item.url_domain.label("domain"), func.count(Item.id).label("count"))
        .where(
            timeline_at >= window_start,
            Item.status == "content_fetched",
            Item.url_domain.is_not(None),
            Item.url_domain != "",
        )
        .group_by(Item.url_domain)
        .order_by(func.count(Item.id).desc())
        .limit(limit)
    )
    if item_filters:
        query = query.where(*item_filters)
    return [DomainPoint(domain=domain, count=int(count or 0)) for domain, count in db.execute(query) if domain]
