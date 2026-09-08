"""Replace one locked item's IOC snapshot with bounded, conflict-safe SQL batches."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.ioc import IOC, ItemIOC
from app.services.ioc_extraction import ExtractedIOC

IOC_WRITE_BATCH_SIZE = 500


@dataclass
class _Aggregate:
    value_raw: str
    sections: set[str] = field(default_factory=set)
    occurrences: int = 0
    confidence: float = 0.0


@dataclass(frozen=True)
class StoredIOCs:
    count: int
    values_by_type: dict[str, list[str]]


def replace_item_iocs(
    db: Session,
    *,
    item_id: uuid.UUID,
    extracted: list[ExtractedIOC],
    now: datetime | None = None,
) -> StoredIOCs:
    """Caller holds the Item row lock and commits source state in this transaction.

    Sort global IOC keys before upserting so workers sharing indicators acquire
    uniqueness locks in the same order. PostgreSQL settles concurrent creation;
    existing raw spellings and first-seen timestamps remain unchanged. Replacing
    the association snapshot avoids an unbounded NOT IN parameter list and keeps
    deletion, occurrence counts and source-section changes atomic on rollback.
    """
    aggregates: dict[tuple[str, str], _Aggregate] = {}
    values_by_type: dict[str, list[str]] = {}
    for match in extracted:
        key = (match.type, match.value_norm)
        if key not in aggregates:
            aggregates[key] = _Aggregate(match.value_raw)
            values_by_type.setdefault(match.type, []).append(match.value_norm)
        aggregate = aggregates[key]
        aggregate.sections.add(match.source_section)
        aggregate.occurrences += 1
        aggregate.confidence = max(aggregate.confidence, match.confidence)

    keys = sorted(aggregates)
    observed_at = now or datetime.now(timezone.utc)
    db.execute(delete(ItemIOC).where(ItemIOC.item_id == item_id))
    for offset in range(0, len(keys), IOC_WRITE_BATCH_SIZE):
        batch = keys[offset:offset + IOC_WRITE_BATCH_SIZE]
        statement = insert(IOC).values([
            {"type": kind, "value_norm": value, "value_raw": aggregates[kind, value].value_raw,
             "first_seen_at": observed_at, "last_seen_at": observed_at}
            for kind, value in batch
        ])
        statement = statement.on_conflict_do_update(
            constraint="uq_iocs_type_value_norm",
            set_={"last_seen_at": func.greatest(IOC.last_seen_at, statement.excluded.last_seen_at)},
        ).returning(IOC.id, IOC.type, IOC.value_norm)
        rows = db.execute(statement).all()
        db.execute(insert(ItemIOC).values([
            {"item_id": item_id, "ioc_id": row.id,
             "source_section": ",".join(sorted(aggregates[row.type, row.value_norm].sections)),
             "occurrences": aggregates[row.type, row.value_norm].occurrences,
             "confidence": aggregates[row.type, row.value_norm].confidence}
            for row in rows
        ]))
    return StoredIOCs(len(keys), values_by_type)
