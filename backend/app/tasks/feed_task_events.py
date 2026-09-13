"""Persist item integration events before publishing downstream task work."""

import uuid

from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.item import Item
from app.services.integration_events import emit_integration_event


def emit_item_integration_event(
    db: Session,
    *,
    event_type: str,
    item: Item,
    feed: Feed,
) -> uuid.UUID:
    event = emit_integration_event(
        db,
        event_type=event_type,
        source_type="item",
        source_id=item.id,
        idempotency_key=f"item:{item.id}:{event_type}:v1",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    return event.id
