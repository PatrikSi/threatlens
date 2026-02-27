from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tag import TagFeedbackEvent

SIGNAL_WEIGHTS: dict[str, float] = {
    "manual_add": 1.0,
    "manual_remove": -1.0,
    "star": 0.25,
    "unstar": -0.25,
    "read": 0.1,
    "unread": -0.1,
}


def record_feedback_events(
    db: Session,
    *,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    signal_type: str,
    tag_names: list[str],
) -> None:
    signal_value = SIGNAL_WEIGHTS.get(signal_type)
    if signal_value is None:
        return

    seen: set[str] = set()
    for raw_name in tag_names:
        tag_name = (raw_name or "").strip().lower()
        if not tag_name or tag_name in seen:
            continue
        seen.add(tag_name)
        db.add(
            TagFeedbackEvent(
                user_id=user_id,
                item_id=item_id,
                tag_name=tag_name,
                signal_type=signal_type,
                signal_value=signal_value,
            )
        )


def load_feedback_adjustments(
    db: Session,
    *,
    tag_names: list[str] | None = None,
    lookback_days: int = 120,
) -> dict[str, float]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    stmt = (
        select(
            TagFeedbackEvent.tag_name,
            func.sum(TagFeedbackEvent.signal_value).label("signal_sum"),
            func.count(TagFeedbackEvent.id).label("signal_count"),
        )
        .where(TagFeedbackEvent.created_at >= cutoff)
        .group_by(TagFeedbackEvent.tag_name)
    )
    if tag_names:
        normalized = sorted({(tag_name or "").strip().lower() for tag_name in tag_names if tag_name})
        if not normalized:
            return {}
        stmt = stmt.where(TagFeedbackEvent.tag_name.in_(normalized))

    adjustments: dict[str, float] = {}
    for tag_name, signal_sum, signal_count in db.execute(stmt):
        total = float(signal_sum or 0.0)
        count = max(1.0, float(signal_count or 0))
        raw_adjustment = total / max(12.0, count * 4.0)
        adjustments[str(tag_name)] = _clamp(raw_adjustment, -0.25, 0.25)
    return adjustments


def _clamp(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value
