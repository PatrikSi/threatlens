from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, select
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


@router.get("/overview", response_model=StatsOverviewResponse)
def get_stats_overview(
    days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)

    feeds_total = db.scalar(select(func.count()).select_from(Feed)) or 0
    feeds_enabled = db.scalar(select(func.count()).select_from(Feed).where(Feed.enabled.is_(True))) or 0

    items_total = db.scalar(select(func.count()).select_from(Item)) or 0
    items_new = db.scalar(select(func.count()).select_from(Item).where(Item.status == "new")) or 0
    items_content_fetched = db.scalar(select(func.count()).select_from(Item).where(Item.status == "content_fetched")) or 0
    items_error = db.scalar(select(func.count()).select_from(Item).where(Item.status == "error")) or 0
    articles_total = db.scalar(select(func.count()).select_from(Article)) or 0

    items_last_24h = db.scalar(select(func.count()).select_from(Item).where(Item.first_seen_at >= now - timedelta(hours=24))) or 0
    items_last_7d = db.scalar(select(func.count()).select_from(Item).where(Item.first_seen_at >= now - timedelta(days=7))) or 0
    items_last_30d = db.scalar(select(func.count()).select_from(Item).where(Item.first_seen_at >= now - timedelta(days=30))) or 0

    status_rows = db.execute(select(Item.status, func.count()).group_by(Item.status).order_by(func.count().desc())).all()
    status_breakdown = [StatusPoint(status=status, count=int(count)) for status, count in status_rows]

    volume_rows = db.scalars(select(Item.first_seen_at).where(Item.first_seen_at >= window_start)).all()
    volume_counter: Counter[str] = Counter()
    for dt in volume_rows:
        normalized = _ensure_aware(dt)
        volume_counter[normalized.date().isoformat()] += 1

    daily_volume = [
        DailyVolumePoint(date=date_key, count=count)
        for date_key, count in sorted(volume_counter.items(), key=lambda kv: kv[0])
    ]

    feed_rows = db.execute(
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
    ).all()

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

    domain_rows = db.execute(
        select(Item.canonical_url, Item.url).where(
            and_(
                Item.first_seen_at >= window_start,
                Item.status == "content_fetched",
            )
        )
    ).all()

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
