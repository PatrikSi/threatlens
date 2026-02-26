import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.schemas.stats import (
    ActivitySummary,
    DailyVolumePoint,
    DerivedSummary,
    DomainPoint,
    FeedStats,
    StatsOverviewResponse,
    StatusPoint,
    TotalsSummary,
)

router = APIRouter(prefix="/stats", tags=["stats"])


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
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
    _user: User = Depends(get_current_user),
):
    selected_feed_ids = _parse_feed_ids(feed_ids)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)

    feed_filters = [Feed.id.in_(selected_feed_ids)] if selected_feed_ids else []
    item_filters = [Item.feed_id.in_(selected_feed_ids)] if selected_feed_ids else []

    feeds_total_query = select(func.count()).select_from(Feed)
    feeds_enabled_query = select(func.count()).select_from(Feed).where(Feed.enabled.is_(True))
    if feed_filters:
        feeds_total_query = feeds_total_query.where(*feed_filters)
        feeds_enabled_query = feeds_enabled_query.where(*feed_filters)

    feeds_total = db.scalar(feeds_total_query) or 0
    feeds_enabled = db.scalar(feeds_enabled_query) or 0

    def _count_items(*extra_filters):
        stmt = select(func.count()).select_from(Item)
        filters = [*item_filters, *extra_filters]
        if filters:
            stmt = stmt.where(*filters)
        return db.scalar(stmt) or 0

    items_total = _count_items()
    items_new = _count_items(Item.status == "new")
    items_content_fetched = _count_items(Item.status == "content_fetched")
    items_error = _count_items(Item.status == "error")

    articles_query = select(func.count()).select_from(Article)
    if item_filters:
        articles_query = articles_query.join(Item, Item.id == Article.item_id).where(*item_filters)
    articles_total = db.scalar(articles_query) or 0

    items_last_24h = _count_items(Item.first_seen_at >= now - timedelta(hours=24))
    items_last_7d = _count_items(Item.first_seen_at >= now - timedelta(days=7))
    items_last_30d = _count_items(Item.first_seen_at >= now - timedelta(days=30))

    status_query = select(Item.status, func.count())
    if item_filters:
        status_query = status_query.where(*item_filters)
    status_rows = db.execute(status_query.group_by(Item.status).order_by(func.count().desc())).all()
    status_breakdown = [StatusPoint(status=status, count=int(count)) for status, count in status_rows]

    volume_query = select(Item.first_seen_at).where(Item.first_seen_at >= window_start)
    if item_filters:
        volume_query = volume_query.where(*item_filters)
    volume_rows = db.scalars(volume_query).all()
    volume_counter: Counter[str] = Counter()
    for dt in volume_rows:
        normalized = _ensure_aware(dt)
        volume_counter[normalized.date().isoformat()] += 1

    daily_volume = [
        DailyVolumePoint(date=date_key, count=count)
        for date_key, count in sorted(volume_counter.items(), key=lambda kv: kv[0])
    ]

    feed_rows_query = (
        select(
            Feed.id,
            Feed.name,
            func.count(Item.id).label("total_items"),
            func.sum(case((Item.first_seen_at >= window_start, 1), else_=0)).label("items_in_window"),
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

    domain_query = select(Item.canonical_url, Item.url).where(
        Item.first_seen_at >= window_start,
        Item.status == "content_fetched",
    )
    if item_filters:
        domain_query = domain_query.where(*item_filters)
    domain_rows = db.execute(domain_query).all()

    domain_counter: Counter[str] = Counter()
    for canonical_url, item_url in domain_rows:
        domain = _extract_domain(canonical_url or item_url)
        if domain:
            domain_counter[domain] += 1

    top_domains = [DomainPoint(domain=domain, count=count) for domain, count in domain_counter.most_common(10)]

    extraction_success_rate = (items_content_fetched / items_total * 100.0) if items_total else 0.0
    error_rate = (items_error / items_total * 100.0) if items_total else 0.0
    avg_items_per_day_window = (sum(point.count for point in daily_volume) / days) if days else 0.0

    return StatsOverviewResponse(
        generated_at=now,
        window_days=days,
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


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None

    try:
        hostname = urlsplit(url).hostname
    except ValueError:
        return None

    return hostname.lower() if hostname else None
