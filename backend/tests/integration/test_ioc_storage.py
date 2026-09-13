from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import uuid

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.services.ioc_extraction import ExtractedIOC
from app.services.ioc_storage import IOC_WRITE_BATCH_SIZE, replace_item_iocs
from app.tasks.feed_task_runtime import claim_item_processing_target


def _item(db, feed):
    identity = uuid.uuid4()
    item = Item(id=identity, feed_id=feed.id, url=f"https://example.com/{identity}",
                title="IOC snapshot", dedupe_key=str(identity), content_hash="a" * 64)
    db.add(item)
    db.flush()
    return item


def _match(value, *, section="article", confidence=1.0, raw=None):
    return ExtractedIOC("hash_sha256", raw or value, value, section, confidence)


@pytest.mark.parametrize("count", [0, 100, IOC_WRITE_BATCH_SIZE * 2 + 1])
def test_ioc_snapshot_uses_bounded_sql_batches(db_session, count):
    feed = Feed(name="Batch", url="https://example.com/batch.xml")
    db_session.add(feed)
    db_session.flush()
    item = _item(db_session, feed)
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    connection = db_session.connection()
    event.listen(connection, "before_cursor_execute", record)
    try:
        stored = replace_item_iocs(db_session, item_id=item.id,
                                  extracted=[_match(f"{index:064x}") for index in range(count)])
    finally:
        event.remove(connection, "before_cursor_execute", record)
    assert stored.count == count
    assert len(statements) == 1 + 2 * ((count + IOC_WRITE_BATCH_SIZE - 1) // IOC_WRITE_BATCH_SIZE)
    assert len(db_session.scalars(select(ItemIOC).where(ItemIOC.item_id == item.id)).all()) == count


def test_ioc_snapshot_preserves_global_metadata_and_replaces_link_metadata(db_session):
    feed = Feed(name="Metadata", url="https://example.com/metadata.xml")
    db_session.add(feed)
    db_session.flush()
    item = _item(db_session, feed)
    value, removed = "a" * 64, "b" * 64
    first = datetime.now(timezone.utc) - timedelta(days=2)
    replace_item_iocs(db_session, item_id=item.id, now=first, extracted=[
        _match(value, raw=value.upper(), section="title", confidence=0.6),
        _match(value, section="article", confidence=0.9), _match(removed),
    ])
    original = db_session.scalar(select(IOC).where(IOC.value_norm == value))
    identity = original.id
    link = db_session.get(ItemIOC, (item.id, identity))
    assert (link.source_section, link.occurrences, link.confidence) == ("article,title", 2, 0.9)
    stored = replace_item_iocs(db_session, item_id=item.id, now=first + timedelta(days=1),
                              extracted=[_match(value, section="summary", confidence=0.8)])
    db_session.expire_all()
    current = db_session.get(IOC, identity)
    assert (current.value_raw, current.first_seen_at) == (value.upper(), first)
    assert current.last_seen_at == first + timedelta(days=1)
    assert stored.values_by_type == {"hash_sha256": [value]}
    link = db_session.get(ItemIOC, (item.id, identity))
    assert (link.source_section, link.occurrences, link.confidence) == ("summary", 1, 0.8)
    assert len(db_session.scalars(select(ItemIOC).where(ItemIOC.item_id == item.id)).all()) == 1
    # A delayed older observer cannot move the global last-seen watermark backwards.
    replace_item_iocs(db_session, item_id=item.id, now=first, extracted=[_match(value)])
    db_session.expire_all()
    assert db_session.get(IOC, identity).last_seen_at == first + timedelta(days=1)
    replace_item_iocs(db_session, item_id=item.id, extracted=[])
    assert db_session.scalar(select(ItemIOC).where(ItemIOC.item_id == item.id)) is None
    assert db_session.get(IOC, identity) is not None


def test_ioc_snapshot_rollback_restores_previous_associations(db_session):
    feed = Feed(name="Rollback", url="https://example.com/rollback.xml")
    db_session.add(feed)
    db_session.flush()
    item = _item(db_session, feed)
    replace_item_iocs(db_session, item_id=item.id, extracted=[_match("a" * 64)])
    old_id = db_session.scalar(select(ItemIOC.ioc_id).where(ItemIOC.item_id == item.id))
    with pytest.raises(RuntimeError, match="interrupted"):
        with db_session.begin_nested():
            replace_item_iocs(db_session, item_id=item.id, extracted=[_match("b" * 64)])
            raise RuntimeError("interrupted")
    assert db_session.scalars(select(ItemIOC.ioc_id).where(ItemIOC.item_id == item.id)).all() == [old_id]


def test_concurrent_items_share_unique_iocs_without_losing_links(database_engine):
    prefix = uuid.uuid4().hex
    with Session(database_engine) as db:
        feed = Feed(name="Concurrent", url=f"https://example.com/{prefix}.xml")
        db.add(feed)
        db.flush()
        feed_id = feed.id
        item_ids = [_item(db, feed).id for _ in range(2)]
        db.commit()
    values = [f"{prefix}{index:032x}" for index in range(IOC_WRITE_BATCH_SIZE + 1)]
    barrier = Barrier(2)
    first = datetime.now(timezone.utc) - timedelta(hours=1)

    def process(index):
        with Session(database_engine) as db:
            item, reason = claim_item_processing_target(db, item_id=item_ids[index])
            assert item is not None and reason is None
            barrier.wait(timeout=10)
            ordered = values if index == 0 else list(reversed(values))
            replace_item_iocs(db, item_id=item.id, now=first + timedelta(minutes=index),
                              extracted=[_match(value, section="title" if index else "article")
                                         for value in ordered])
            db.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(process, index) for index in range(2)]
            for future in futures:
                future.result(timeout=20)
        with Session(database_engine) as db:
            rows = db.scalars(select(IOC).where(IOC.value_norm.in_(values))).all()
            assert len(rows) == len(values)
            assert all(row.last_seen_at == first + timedelta(minutes=1) for row in rows)
            for index, item_id in enumerate(item_ids):
                links = db.scalars(select(ItemIOC).where(ItemIOC.item_id == item_id)).all()
                assert {link.ioc_id for link in links} == {row.id for row in rows}
                assert {link.source_section for link in links} == {"title" if index else "article"}
    finally:
        with Session(database_engine) as db:
            db.execute(delete(Feed).where(Feed.id == feed_id))
            db.execute(delete(IOC).where(IOC.value_norm.in_(values)))
            db.commit()
