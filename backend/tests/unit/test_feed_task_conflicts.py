import uuid
from datetime import datetime, timezone

from app.models.feed import Feed
from app.models.item import Item
from app.services.feed_pipeline import _insert_item_with_conflict_retry


def test_insert_item_with_conflict_retry_handles_duplicate_dedupe_key(db_session):
    feed = Feed(
        id=uuid.uuid4(),
        name="Conflict Feed",
        url="https://example.com/conflict.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.flush()

    first = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="first",
        url="https://example.com/first",
        canonical_url="https://example.com/first",
        title="First",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:shared",
        content_hash="a" * 64,
        status="new",
    )
    assert _insert_item_with_conflict_retry(db_session, first)
    db_session.commit()

    duplicate = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="second",
        url="https://example.com/second",
        canonical_url="https://example.com/second",
        title="Second",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:shared",
        content_hash="b" * 64,
        status="new",
    )
    assert not _insert_item_with_conflict_retry(db_session, duplicate)
