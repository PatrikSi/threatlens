"""Capture bounded routing facts once, with the domain event transaction."""

from __future__ import annotations

import uuid
from sqlalchemy import select
from app.models.tag import ItemTag, Tag
from app.models.alert_occurrence import AlertOccurrence


def add_routing_metadata(db, *, event_type: str, payload: dict) -> None:
    from app.services.webhook_ai_events import capture_relevance_metadata

    capture_relevance_metadata(db, event_type=event_type, payload=payload)
    if event_type not in {"rss_item_new", "alert_match", "article.ai.ready"}:
        return
    try:
        item_id = uuid.UUID(str(payload.get("item_id")))
    except (ValueError, TypeError):
        return
    rows = db.execute(
        select(Tag.id, Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id == item_id)
        .order_by(Tag.id)
        .limit(251)
    ).all()
    metadata = {
        "tags": [row.name for row in rows[:250]],
        "tag_ids": [str(row.id) for row in rows[:250]],
        "tags_complete": len(rows) <= 250,
    }
    if event_type == "alert_match" and payload.get("owner_user_id"):
        try:
            owner_id = uuid.UUID(str(payload["owner_user_id"]))
            ids = [
                uuid.UUID(value)
                for value in (payload.get("occurrence_ids") or [])[:500]
            ]
        except (ValueError, TypeError):
            ids = []
        if ids:
            rules = db.scalars(
                select(AlertOccurrence.rule_id_snapshot)
                .where(
                    AlertOccurrence.id.in_(ids),
                    AlertOccurrence.owner_user_id == owner_id,
                )
                .distinct()
                .limit(251)
            ).all()
            metadata["alert_rule_ids"] = [str(value) for value in rules[:250]]
            metadata["alert_rules_complete"] = len(rules) <= 250 and not payload.get(
                "occurrence_ids_truncated", False
            )
    payload["filter_metadata"] = metadata
