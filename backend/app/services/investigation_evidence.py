from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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


def build_evidence_snapshot(db: Session, *, source_type: str, source_id: uuid.UUID) -> EvidenceSnapshot:
    if source_type == "item":
        return _item_snapshot(db, source_id)
    if source_type == "ioc":
        return _ioc_snapshot(db, source_id)
    if source_type == "report":
        return _report_snapshot(db, source_id)
    if source_type == "alert_occurrence":
        raise EvidenceSourceError(
            "Alert occurrence evidence is unavailable until durable Alerting v2 is enabled."
        )
    raise EvidenceSourceError("Unsupported investigation evidence type.")


def _item_snapshot(db: Session, source_id: uuid.UUID) -> EvidenceSnapshot:
    row = db.execute(
        select(Item, Feed.name.label("feed_name"), ItemClassification.primary_category.label("classification"))
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .where(Item.id == source_id)
    ).first()
    if row is None:
        raise EvidenceSourceError("The selected article no longer exists.")
    tag_query = (
        select(Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id == source_id)
        .order_by(Tag.name.asc())
    )
    tag_count = int(db.scalar(select(func.count()).select_from(tag_query.subquery())) or 0)
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
    ioc = db.scalar(select(IOC).where(IOC.id == source_id))
    if ioc is None:
        raise EvidenceSourceError("The selected IOC no longer exists.")
    raw_value = _bounded_text(ioc.value_raw, 384) or "unknown"
    return EvidenceSnapshot(
        title=_bounded_text(f"{ioc.type}: {raw_value}", MAX_SNAPSHOT_TITLE_CHARS) or "IOC",
        description=None,
        url=None,
        metadata={
            "ioc_type": ioc.type,
            "value": raw_value,
            "first_seen_at": _isoformat(ioc.first_seen_at),
            "last_seen_at": _isoformat(ioc.last_seen_at),
        },
    )


def _report_snapshot(db: Session, source_id: uuid.UUID) -> EvidenceSnapshot:
    report = db.scalar(select(Report).where(Report.id == source_id))
    if report is None:
        raise EvidenceSourceError("The selected report no longer exists.")
    return EvidenceSnapshot(
        title=_bounded_text(report.title, MAX_SNAPSHOT_TITLE_CHARS) or "Untitled report",
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


def _bounded_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
