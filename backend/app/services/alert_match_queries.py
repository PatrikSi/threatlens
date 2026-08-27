from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.alert_interest import AlertInterest
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.item_state import ItemState
from app.models.tag import ItemTag, Tag
from app.models.user import User
from app.schemas.alert import (
    AlertMatchEntry,
    AlertMatchListResponse,
    AlertMatchReference,
)
from app.schemas.item import ItemListEntry
from app.services.alert_matching import (
    build_alert_keyword_condition,
    build_item_haystack,
    escape_like,
    match_alert_keywords,
)


ALERT_SORT_OPTIONS = {
    "published_at_desc": Item.published_at.desc().nullslast(),
    "published_at_asc": Item.published_at.asc().nullsfirst(),
    "first_seen_desc": Item.first_seen_at.desc(),
    "first_seen_asc": Item.first_seen_at.asc(),
}


@dataclass(frozen=True)
class AlertMatchDefinition:
    id: uuid.UUID
    name: str
    category: str
    keywords: list[str]


def list_matches_for_alerts(
    db: Session,
    *,
    user: User,
    alerts: list[AlertInterest | AlertMatchDefinition],
    q: str | None = None,
    is_starred: bool | None = None,
    is_read: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "published_at_desc",
    keyword_cap: int | None = None,
) -> AlertMatchListResponse:
    effective_keyword_cap = int(keyword_cap or get_settings().alert_matches_keyword_cap)
    if not alerts:
        return AlertMatchListResponse(items=[], total=0, page=page, page_size=page_size)

    all_keywords = sorted(
        {keyword for alert in alerts for keyword in alert.keywords if keyword}
    )
    if not all_keywords:
        return AlertMatchListResponse(items=[], total=0, page=page, page_size=page_size)
    if len(all_keywords) > effective_keyword_cap:
        alert_label = "alert" if len(alerts) == 1 else "alerts"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Alert matching selected {len(alerts)} {alert_label} with {len(all_keywords)} distinct keywords, "
                f"exceeding ALERT_MATCHES_KEYWORD_CAP={effective_keyword_cap}. "
                "Reduce keywords or disable unneeded alerts, narrow the request with alert_ids or categories, "
                "or increase ALERT_MATCHES_KEYWORD_CAP."
            ),
        )

    state_subquery = (
        select(
            ItemState.item_id.label("item_id"),
            ItemState.is_read.label("is_read"),
            ItemState.is_starred.label("is_starred"),
        )
        .where(ItemState.user_id == user.id)
        .subquery()
    )
    items_query = (
        select(
            Item,
            Feed.name.label("feed_name"),
            ItemClassification.primary_category.label("primary_category"),
            func.coalesce(state_subquery.c.is_read, False).label("is_read"),
            func.coalesce(state_subquery.c.is_starred, False).label("is_starred"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .outerjoin(state_subquery, state_subquery.c.item_id == Item.id)
    )

    filters = []
    if q:
        pattern = f"%{escape_like(q.strip().lower())}%"
        filters.append(
            or_(
                func.lower(Item.title).like(pattern, escape="\\"),
                func.lower(func.coalesce(Item.summary, "")).like(pattern, escape="\\"),
                func.lower(cast(Item.url, String)).like(pattern, escape="\\"),
                func.lower(cast(func.coalesce(Item.canonical_url, ""), String)).like(
                    pattern, escape="\\"
                ),
            )
        )
    if since is not None:
        filters.append(Item.first_seen_at >= since)
    if until is not None:
        filters.append(Item.first_seen_at <= until)
    if is_read is not None:
        filters.append(func.coalesce(state_subquery.c.is_read, False) == is_read)
    if is_starred is not None:
        filters.append(func.coalesce(state_subquery.c.is_starred, False) == is_starred)
    filters.append(
        or_(*(build_alert_keyword_condition(keyword) for keyword in all_keywords))
    )
    items_query = items_query.where(and_(*filters))

    total = db.scalar(select(func.count()).select_from(items_query.subquery())) or 0
    order_by = ALERT_SORT_OPTIONS.get(sort, ALERT_SORT_OPTIONS["published_at_desc"])
    rows = db.execute(
        items_query.order_by(order_by).offset((page - 1) * page_size).limit(page_size)
    ).all()
    item_ids = [row.Item.id for row in rows]

    tags_by_item: dict[uuid.UUID, list[str]] = {item_id: [] for item_id in item_ids}
    if item_ids:
        tag_rows = db.execute(
            select(ItemTag.item_id, Tag.name)
            .join(Tag, Tag.id == ItemTag.tag_id)
            .where(ItemTag.item_id.in_(item_ids))
            .order_by(Tag.name.asc())
        ).all()
        for item_id, tag_name in tag_rows:
            tags_by_item[item_id].append(tag_name)

    items: list[AlertMatchEntry] = []
    for row in rows:
        haystack = build_item_haystack(
            title=row.Item.title,
            summary=row.Item.summary,
            url=row.Item.url,
            canonical_url=row.Item.canonical_url,
            classification=row.primary_category,
        )
        matches = [
            AlertMatchReference(
                alert_id=alert.id,
                alert_name=alert.name,
                category=alert.category,
                matched_keywords=matched_keywords,
            )
            for alert in alerts
            if (matched_keywords := match_alert_keywords(alert.keywords, haystack))
        ]
        if not matches:
            continue
        base_entry = ItemListEntry(
            id=row.Item.id,
            feed_id=row.Item.feed_id,
            feed_name=row.feed_name,
            url=row.Item.url,
            canonical_url=row.Item.canonical_url,
            title=row.Item.title,
            summary=row.Item.summary,
            published_at=row.Item.published_at,
            first_seen_at=row.Item.first_seen_at,
            status=row.Item.status,
            classification=row.primary_category,
            is_read=row.is_read,
            is_starred=row.is_starred,
            tags=tags_by_item.get(row.Item.id, []),
        )
        items.append(AlertMatchEntry(**base_entry.model_dump(), matches=matches))

    return AlertMatchListResponse(
        items=items, total=total, page=page, page_size=page_size
    )
