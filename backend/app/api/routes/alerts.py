import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_token_scopes
from app.core.config import get_settings
from app.core.token_scopes import (
    SCOPE_READ_ALERTS,
    SCOPE_READ_ITEMS,
    SCOPE_WRITE_ALERTS,
)
from app.db.session import get_db
from app.models.alert_interest import AlertInterest
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.item_state import ItemState
from app.models.tag import ItemTag, Tag
from app.models.user import User
from app.schemas.alert import (
    AlertInterestCreate,
    AlertInterestResponse,
    AlertInterestUpdate,
    AlertMatchEntry,
    AlertMatchListResponse,
    AlertMatchReference,
)
from app.schemas.item import ItemListEntry
from app.services.audit import record_audit

router = APIRouter(prefix="/alerts", tags=["alerts"])

ALERT_SORT_OPTIONS = {
    "published_at_desc": Item.published_at.desc().nullslast(),
    "published_at_asc": Item.published_at.asc().nullsfirst(),
    "first_seen_desc": Item.first_seen_at.desc(),
    "first_seen_asc": Item.first_seen_at.asc(),
}


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Alert name cannot be empty")
    return normalized


def _normalize_category(value: str) -> str:
    normalized = "_".join(value.strip().lower().split())
    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Alert category cannot be empty")
    return normalized


def _normalize_keywords(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw in values:
        keyword = " ".join(raw.strip().lower().split())
        if not keyword or keyword in seen:
            continue
        normalized.append(keyword)
        seen.add(keyword)

    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one keyword is required")

    return normalized


def _parse_uuid_csv(raw_value: str | None, detail: str) -> list[uuid.UUID]:
    if not raw_value:
        return []

    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in raw_value.split(","):
        candidate = value.strip()
        if not candidate:
            continue
        try:
            parsed_uuid = uuid.UUID(candidate)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail) from exc
        if parsed_uuid in seen:
            continue
        seen.add(parsed_uuid)
        parsed.append(parsed_uuid)

    return parsed


def _parse_category_csv(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []

    categories: list[str] = []
    seen: set[str] = set()
    for value in raw_value.split(","):
        normalized = _normalize_category(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        categories.append(normalized)
    return categories


def _build_keyword_condition(keyword: str):
    pattern = f"%{_escape_like(keyword)}%"
    return or_(
        func.lower(Item.title).like(pattern, escape="\\"),
        func.lower(func.coalesce(Item.summary, "")).like(pattern, escape="\\"),
        func.lower(cast(Item.url, String)).like(pattern, escape="\\"),
        func.lower(cast(func.coalesce(Item.canonical_url, ""), String)).like(pattern, escape="\\"),
        func.lower(func.coalesce(ItemClassification.primary_category, "")).like(pattern, escape="\\"),
    )


def _build_item_haystack(
    *,
    title: str,
    summary: str | None,
    url: str,
    canonical_url: str | None,
    classification: str | None,
) -> str:
    return " ".join(
        [
            title,
            summary or "",
            url,
            canonical_url or "",
            classification or "",
        ]
    ).lower()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=list[AlertInterestResponse])
def list_alert_interests(
    include_disabled: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS)),
):
    query = select(AlertInterest).where(AlertInterest.user_id == user.id)
    if not include_disabled:
        query = query.where(AlertInterest.enabled.is_(True))

    rows = db.scalars(query.order_by(AlertInterest.created_at.desc())).all()
    return list(rows)


@router.post("", response_model=AlertInterestResponse, status_code=status.HTTP_201_CREATED)
def create_alert_interest(
    payload: AlertInterestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS)),
):
    alert = AlertInterest(
        user_id=user.id,
        name=_normalize_name(payload.name),
        category=_normalize_category(payload.category),
        keywords=_normalize_keywords(payload.keywords),
        enabled=payload.enabled,
    )
    db.add(alert)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="alerts.create",
        resource_type="alert_interest",
        resource_id=str(alert.id),
        metadata={
            "name": alert.name,
            "category": alert.category,
            "keyword_count": len(alert.keywords),
            "enabled": alert.enabled,
        },
    )
    db.commit()
    db.refresh(alert)
    return alert


@router.patch("/{alert_id}", response_model=AlertInterestResponse)
def update_alert_interest(
    alert_id: uuid.UUID,
    payload: AlertInterestUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS)),
):
    alert = db.scalar(select(AlertInterest).where(AlertInterest.id == alert_id, AlertInterest.user_id == user.id))
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert interest not found")

    if payload.name is not None:
        alert.name = _normalize_name(payload.name)
    if payload.category is not None:
        alert.category = _normalize_category(payload.category)
    if payload.keywords is not None:
        alert.keywords = _normalize_keywords(payload.keywords)
    if payload.enabled is not None:
        alert.enabled = payload.enabled

    db.add(alert)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="alerts.update",
        resource_type="alert_interest",
        resource_id=str(alert.id),
        metadata={
            "name": alert.name,
            "category": alert.category,
            "keyword_count": len(alert.keywords),
            "enabled": alert.enabled,
        },
    )
    db.commit()
    db.refresh(alert)
    return alert


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert_interest(
    alert_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS)),
):
    alert = db.scalar(select(AlertInterest).where(AlertInterest.id == alert_id, AlertInterest.user_id == user.id))
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert interest not found")

    db.delete(alert)
    record_audit(
        db,
        actor_user_id=user.id,
        action="alerts.delete",
        resource_type="alert_interest",
        resource_id=str(alert_id),
    )
    db.commit()


@router.get("/matches", response_model=AlertMatchListResponse)
def list_alert_matches(
    q: str | None = None,
    alert_ids: str | None = Query(default=None),
    categories: str | None = Query(default=None),
    include_disabled: bool = Query(default=False),
    is_starred: bool | None = None,
    is_read: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    sort: str = Query(default="published_at_desc"),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS, SCOPE_READ_ITEMS)),
):
    settings = get_settings()
    selected_alert_ids = _parse_uuid_csv(alert_ids, "Invalid alert id in alert_ids")
    selected_categories = _parse_category_csv(categories)

    alerts_query = select(AlertInterest).where(AlertInterest.user_id == user.id)
    if not include_disabled:
        alerts_query = alerts_query.where(AlertInterest.enabled.is_(True))
    if selected_alert_ids:
        alerts_query = alerts_query.where(AlertInterest.id.in_(selected_alert_ids))
    if selected_categories:
        alerts_query = alerts_query.where(AlertInterest.category.in_(selected_categories))

    alerts = db.scalars(alerts_query.order_by(AlertInterest.created_at.desc())).all()
    if not alerts:
        return AlertMatchListResponse(items=[], total=0, page=page, page_size=page_size)

    all_keywords = sorted({keyword for alert in alerts for keyword in alert.keywords if keyword})
    if not all_keywords:
        return AlertMatchListResponse(items=[], total=0, page=page, page_size=page_size)
    if len(all_keywords) > settings.alert_matches_keyword_cap:
        all_keywords = all_keywords[: settings.alert_matches_keyword_cap]

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
        pattern = f"%{_escape_like(q.strip().lower())}%"
        filters.append(
            or_(
                func.lower(Item.title).like(pattern, escape="\\"),
                func.lower(func.coalesce(Item.summary, "")).like(pattern, escape="\\"),
                func.lower(cast(Item.url, String)).like(pattern, escape="\\"),
                func.lower(cast(func.coalesce(Item.canonical_url, ""), String)).like(pattern, escape="\\"),
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

    keyword_conditions = [_build_keyword_condition(keyword) for keyword in all_keywords]
    if keyword_conditions:
        filters.append(or_(*keyword_conditions))

    if filters:
        items_query = items_query.where(and_(*filters))

    total = db.scalar(select(func.count()).select_from(items_query.subquery())) or 0
    order_by = ALERT_SORT_OPTIONS.get(sort, ALERT_SORT_OPTIONS["published_at_desc"])

    rows = db.execute(items_query.order_by(order_by).offset((page - 1) * page_size).limit(page_size)).all()
    item_ids = [row.Item.id for row in rows]

    tags_by_item: dict[uuid.UUID, list[str]] = {item_id: [] for item_id in item_ids}
    if item_ids:
        tag_rows = db.execute(
            select(ItemTag.item_id, Tag.name)
            .join(Tag, Tag.id == ItemTag.tag_id)
            .where(ItemTag.item_id.in_(item_ids))
            .order_by(Tag.name.asc())
        ).all()
        for item_id_value, tag_name in tag_rows:
            tags_by_item[item_id_value].append(tag_name)

    items: list[AlertMatchEntry] = []
    for row in rows:
        haystack = _build_item_haystack(
            title=row.Item.title,
            summary=row.Item.summary,
            url=row.Item.url,
            canonical_url=row.Item.canonical_url,
            classification=row.primary_category,
        )
        matches: list[AlertMatchReference] = []
        for alert in alerts:
            matched_keywords = [keyword for keyword in alert.keywords if keyword and keyword in haystack]
            if not matched_keywords:
                continue
            matches.append(
                AlertMatchReference(
                    alert_id=alert.id,
                    alert_name=alert.name,
                    category=alert.category,
                    matched_keywords=matched_keywords,
                )
            )

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

    return AlertMatchListResponse(items=items, total=total, page=page, page_size=page_size)
