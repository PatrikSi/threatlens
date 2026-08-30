from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alert_occurrence import AlertOccurrence
from app.models.feed import Feed
from app.models.ioc import IOC
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.report import Report
from app.models.tag import ItemTag, Tag

MAX_SNAPSHOT_TITLE_CHARS = 512
MAX_SNAPSHOT_DESCRIPTION_CHARS = 2_000
MAX_SNAPSHOT_URL_CHARS = 4_096
MAX_SNAPSHOT_TAGS = 100


class EvidenceSourceError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceSnapshot:
    title: str
    description: str | None
    url: str | None
    metadata: dict


def build_evidence_snapshot(
    db: Session,
    *,
    source_type: str,
    source_id: uuid.UUID,
    requesting_user_id: uuid.UUID,
    requesting_user_is_admin: bool,
) -> EvidenceSnapshot:
    if source_type == "item":
        return _item_snapshot(db, source_id)
    if source_type == "ioc":
        return _ioc_snapshot(db, source_id)
    if source_type == "report":
        return _report_snapshot(
            db,
            source_id,
            requesting_user_id=requesting_user_id,
            requesting_user_is_admin=requesting_user_is_admin,
        )
    if source_type == "alert_occurrence":
        return _alert_occurrence_snapshot(
            db,
            source_id,
            requesting_user_id=requesting_user_id,
        )
    raise EvidenceSourceError("Unsupported investigation evidence type.")


def _item_snapshot(db: Session, source_id: uuid.UUID) -> EvidenceSnapshot:
    row = db.execute(
        select(
            Item,
            Feed.name.label("feed_name"),
            ItemClassification.primary_category.label("classification"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .where(Item.id == source_id)
        .with_for_update(read=True, of=(Item, Feed))
    ).first()
    if row is None:
        raise EvidenceSourceError("The selected article no longer exists.")
    tag_query = (
        select(Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id == source_id)
        .order_by(Tag.name.asc())
    )
    tag_count = int(
        db.scalar(select(func.count()).select_from(tag_query.subquery())) or 0
    )
    tags = list(db.scalars(tag_query.limit(MAX_SNAPSHOT_TAGS)).all())
    item = row.Item
    return EvidenceSnapshot(
        title=_bounded_text(item.title, MAX_SNAPSHOT_TITLE_CHARS) or "Untitled article",
        description=_bounded_text(item.summary, MAX_SNAPSHOT_DESCRIPTION_CHARS),
        url=_bounded_text(item.canonical_url or item.url, MAX_SNAPSHOT_URL_CHARS),
        metadata={
            "feed_id": str(item.feed_id),
            "feed_name": _bounded_text(row.feed_name, 255),
            "classification": row.classification,
            "published_at": _isoformat(item.published_at),
            "first_seen_at": _isoformat(item.first_seen_at),
            "content_hash": item.content_hash,
            "tags": tags,
            "tags_truncated": tag_count > len(tags),
        },
    )


def _ioc_snapshot(db: Session, source_id: uuid.UUID) -> EvidenceSnapshot:
    ioc = db.scalar(select(IOC).where(IOC.id == source_id).with_for_update(read=True))
    if ioc is None:
        raise EvidenceSourceError("The selected IOC no longer exists.")
    raw_value = _bounded_text(ioc.value_raw, 384) or "unknown"
    return EvidenceSnapshot(
        title=_bounded_text(f"{ioc.type}: {raw_value}", MAX_SNAPSHOT_TITLE_CHARS)
        or "IOC",
        description=None,
        url=None,
        metadata={
            "ioc_type": ioc.type,
            "value": raw_value,
            "first_seen_at": _isoformat(ioc.first_seen_at),
            "last_seen_at": _isoformat(ioc.last_seen_at),
        },
    )


def _report_snapshot(
    db: Session,
    source_id: uuid.UUID,
    *,
    requesting_user_id: uuid.UUID,
    requesting_user_is_admin: bool,
) -> EvidenceSnapshot:
    predicates = [Report.id == source_id]
    if not requesting_user_is_admin:
        predicates.append(Report.owner_user_id == requesting_user_id)
    report = db.scalar(select(Report).where(*predicates).with_for_update(read=True))
    if report is None:
        raise EvidenceSourceError(
            "The selected report does not exist or is not available to your account."
        )
    return EvidenceSnapshot(
        title=_bounded_text(report.title, MAX_SNAPSHOT_TITLE_CHARS)
        or "Untitled report",
        description=_bounded_text(report.summary_text, MAX_SNAPSHOT_DESCRIPTION_CHARS),
        url=None,
        metadata={
            "report_type": report.report_type,
            "status": report.status,
            "period_start": _isoformat(report.period_start),
            "period_end": _isoformat(report.period_end),
            "generated_at": _isoformat(report.generated_at),
        },
    )


def _alert_occurrence_snapshot(
    db: Session,
    source_id: uuid.UUID,
    *,
    requesting_user_id: uuid.UUID,
) -> EvidenceSnapshot:
    occurrence = db.scalar(
        select(AlertOccurrence)
        .where(
            AlertOccurrence.id == source_id,
            AlertOccurrence.owner_user_id == requesting_user_id,
        )
        .with_for_update(read=True)
    )
    if occurrence is None:
        raise EvidenceSourceError(
            "The selected alert occurrence does not exist or is not available to your account."
        )

    source = occurrence.source_snapshot_json
    source = source if isinstance(source, dict) else {}
    item = source.get("item")
    item = item if isinstance(item, dict) else {}
    feed = source.get("feed")
    feed = feed if isinstance(feed, dict) else {}
    classification = source.get("classification")
    classification = classification if isinstance(classification, dict) else {}
    item_title = _bounded_text(_string_value(item.get("title")), 255)
    title = _bounded_text(
        f"{occurrence.alert_name_snapshot}: {item_title}"
        if item_title
        else occurrence.alert_name_snapshot,
        MAX_SNAPSHOT_TITLE_CHARS,
    )
    return EvidenceSnapshot(
        title=title or "Alert occurrence",
        description=_bounded_text(
            _string_value(item.get("summary")), MAX_SNAPSHOT_DESCRIPTION_CHARS
        ),
        url=_bounded_text(
            _string_value(item.get("canonical_url") or item.get("url")),
            MAX_SNAPSHOT_URL_CHARS,
        ),
        metadata={
            "rule_id": str(occurrence.rule_id_snapshot),
            "rule_revision": occurrence.rule_revision,
            "alert_name": _bounded_text(occurrence.alert_name_snapshot, 255),
            "alert_category": _bounded_text(occurrence.alert_category_snapshot, 64),
            "alert_keywords": [
                value
                for value in (
                    _bounded_text(_string_value(keyword), 255)
                    for keyword in list(occurrence.alert_keywords_snapshot or [])[
                        :MAX_SNAPSHOT_TAGS
                    ]
                )
                if value is not None
            ],
            "matched_keywords": [
                value
                for value in (
                    _bounded_text(_string_value(keyword), 255)
                    for keyword in list(occurrence.matched_keywords or [])[
                        :MAX_SNAPSHOT_TAGS
                    ]
                )
                if value is not None
            ],
            "severity": occurrence.severity_snapshot,
            "item_id": str(occurrence.item_id_snapshot),
            "item_content_hash": occurrence.item_content_hash,
            "feed_id": _bounded_text(_string_value(feed.get("id")), 64),
            "feed_name": _bounded_text(_string_value(feed.get("name")), 255),
            "classification": _bounded_text(
                _string_value(classification.get("primary_category")), 64
            ),
            "published_at": _bounded_text(_string_value(item.get("published_at")), 64),
            "first_seen_at": _bounded_text(
                _string_value(item.get("first_seen_at")), 64
            ),
            "occurrence_created_at": _isoformat(occurrence.created_at),
            "lifecycle_state_at_attachment": occurrence.lifecycle_state,
            "suppressed_at_attachment": occurrence.suppressed_at is not None,
            "snoozed_until_at_attachment": _isoformat(occurrence.snoozed_until),
            "closure_disposition_at_attachment": occurrence.closure_disposition,
        },
    )


def _bounded_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _string_value(value: object | None) -> str | None:
    return value if isinstance(value, str) else None


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
