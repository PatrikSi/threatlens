"""Transactionally advance bounded scans without weakening retention guards.

Each dataset has one row-locked anchor. End of scan resets it for the next run,
so newly eligible, previously locked and oversized records are revisited. A
budget-limited eligible parent stops the completed prefix: it stays in the next
window, even if cheaper parents after it were processed in this transaction.
"""

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement, Select

from app.models.lifecycle_scan import LifecycleScanCursor
from app.services.lifecycle_dependencies import LifecycleDependencySelection


@dataclass
class LifecycleScanStats:
    advanced: int = 0
    completed: int = 0

    def details(self) -> dict[str, int]:
        return {
            "scan_anchors_advanced": self.advanced,
            "scan_cycles_completed": self.completed,
        }


@dataclass
class LifecycleCandidateWindow:
    db: Session
    rows: list[tuple[uuid.UUID, datetime]]
    cursor: LifecycleScanCursor | None
    stats: LifecycleScanStats | None

    @property
    def ids(self) -> list[uuid.UUID]:
        return [row[0] for row in self.rows]

    def advance(self, selection: LifecycleDependencySelection) -> None:
        count = selection.completed_prefix_length
        if self.cursor is None or not count:
            return
        self.cursor.last_id, self.cursor.last_timestamp = self.rows[count - 1]
        self.db.flush([self.cursor])
        if self.stats is not None:
            self.stats.advanced += count


def lifecycle_candidate_window(
    db: Session,
    statement: Select,
    *,
    model: type,
    timestamp: ColumnElement[datetime],
    limit: int,
    durable: bool,
    stats: LifecycleScanStats | None = None,
) -> LifecycleCandidateWindow:
    cursor = None
    if durable:
        # Dataset names are internal model table names, never caller-supplied IDs.
        dataset = model.__table__.name
        db.execute(
            insert(LifecycleScanCursor).values(dataset=dataset).on_conflict_do_nothing()
        )
        cursor = db.scalar(
            select(LifecycleScanCursor)
            .where(LifecycleScanCursor.dataset == dataset)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if cursor.last_id is not None:
            statement = statement.where(
                tuple_(timestamp, model.id)
                > tuple_(cursor.last_timestamp, cursor.last_id)
            )
    rows = [
        (row[0], row[1])
        for row in db.execute(
            statement.with_only_columns(model.id, timestamp)
            .order_by(None)
            .order_by(timestamp.asc(), model.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ]
    if cursor is not None and not rows:
        cursor.last_id = cursor.last_timestamp = None
        db.flush([cursor])
        if stats is not None:
            stats.completed += 1
    return LifecycleCandidateWindow(db, rows, cursor, stats)
