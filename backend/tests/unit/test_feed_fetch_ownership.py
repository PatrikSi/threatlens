from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.feed import Feed
from app.models.integration import IntegrationEvent
from app.models.item import Item
from app.services.feed_fetch_ownership import (
    FeedFetchOwnershipLostError,
    claim_feed_fetch,
    ensure_feed_fetch_owned,
)
from app.tasks.feed_tasks import fetch_feed


def test_newer_feed_fetch_fence_prevents_stale_worker_commit(database_engine):
    session_factory = sessionmaker(
        bind=database_engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()

    with session_factory() as setup:
        setup.add(
            Feed(
                id=feed_id,
                name="Fenced feed",
                url=f"https://example.com/{feed_id}.xml",
                enabled=True,
            )
        )
        setup.commit()

    try:
        with session_factory() as stale_worker, session_factory() as current_worker:
            stale_feed = stale_worker.scalar(
                select(Feed).where(Feed.id == feed_id).with_for_update()
            )
            assert stale_feed is not None
            stale_claim = claim_feed_fetch(stale_worker, feed=stale_feed)
            stale_worker.commit()

            current_feed = current_worker.scalar(
                select(Feed).where(Feed.id == feed_id).with_for_update()
            )
            assert current_feed is not None
            current_claim = claim_feed_fetch(current_worker, feed=current_feed)
            current_worker.commit()

            stale_feed.last_error = "stale-worker-write"
            stale_worker.add(
                Item(
                    id=item_id,
                    feed_id=feed_id,
                    source_guid="stale-item",
                    url="https://example.com/stale-item",
                    title="Stale item",
                    dedupe_key=f"stale-{item_id}",
                    content_hash="a" * 64,
                    status="new",
                )
            )
            with pytest.raises(
                FeedFetchOwnershipLostError,
                match="newer worker",
            ):
                ensure_feed_fetch_owned(stale_worker, claim=stale_claim)
            stale_worker.rollback()

            current_feed.last_error = "current-worker-write"
            ensure_feed_fetch_owned(current_worker, claim=current_claim)
            current_worker.commit()

        with session_factory() as verify:
            persisted_feed = verify.get(Feed, feed_id)
            assert persisted_feed is not None
            assert persisted_feed.fetch_fence == current_claim.fence
            assert persisted_feed.last_error == "current-worker-write"
            assert verify.get(Item, item_id) is None
    finally:
        with session_factory() as cleanup:
            cleanup.execute(delete(Item).where(Item.feed_id == feed_id))
            cleanup.execute(delete(Feed).where(Feed.id == feed_id))
            cleanup.commit()


def test_feed_fetch_fence_rejects_disabled_or_reconfigured_feed(db_session):
    feed = Feed(
        id=uuid.uuid4(),
        name="Mutable feed",
        url="https://example.com/original.xml",
        enabled=True,
    )
    db_session.add(feed)
    db_session.commit()

    claim = claim_feed_fetch(db_session, feed=feed)
    db_session.commit()

    feed.enabled = False
    db_session.commit()
    with pytest.raises(FeedFetchOwnershipLostError, match="disabled"):
        ensure_feed_fetch_owned(db_session, claim=claim)
    db_session.rollback()

    feed.enabled = True
    feed.url = "https://example.com/reconfigured.xml"
    db_session.commit()
    with pytest.raises(FeedFetchOwnershipLostError, match="URL changed"):
        ensure_feed_fetch_owned(db_session, claim=claim)


def test_fetch_task_rolls_back_items_and_events_when_fence_is_lost(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Superseded feed",
        url="https://example.com/superseded.xml",
        enabled=True,
    )
    db_session.add(feed)
    db_session.commit()

    @contextmanager
    def db_session_override():
        yield db_session

    @contextmanager
    def feed_lock_override(_feed_id: str, ttl_seconds: int = 900):
        _ = ttl_seconds
        yield True

    class Response:
        status_code = 200
        headers: dict[str, str] = {}
        url = "https://example.com/superseded.xml"

        def iter_bytes(self):
            yield b"<rss />"

        def close(self):
            pass

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    created_item_id = uuid.uuid4()

    def upsert_item(db, current_feed, _parsed):
        item = Item(
            id=created_item_id,
            feed_id=current_feed.id,
            source_guid="superseded-item",
            url="https://example.com/superseded-item",
            title="Superseded item",
            published_at=datetime.now(timezone.utc),
            dedupe_key=f"superseded-{created_item_id}",
            content_hash="b" * 64,
            status="new",
        )
        db.add(item)
        db.flush()
        return item, True, True

    enqueued: list[str] = []
    monkeypatch.setattr("app.tasks.feed_tasks.db_session", db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.feed_lock", feed_lock_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.build_safe_http_client",
        lambda *args, **kwargs: Client(),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.safe_stream_with_redirects",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.RSSConnector.poll",
        lambda *_args, **_kwargs: ([{"id": "superseded-item"}], None),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks._backfill_feed_metadata_from_body",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr("app.tasks.feed_tasks._upsert_item_from_parsed", upsert_item)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.ensure_feed_fetch_owned",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FeedFetchOwnershipLostError("a newer worker owns this feed fetch")
        ),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.fetch_article.delay",
        lambda item_id: enqueued.append(item_id),
    )

    result = fetch_feed.run(str(feed.id), force=True)

    db_session.expire_all()
    assert result == {
        "status": "skipped",
        "reason": "fetch_ownership_lost",
        "feed_id": str(feed.id),
    }
    assert db_session.get(Item, created_item_id) is None
    assert db_session.scalar(
        select(IntegrationEvent).where(
            IntegrationEvent.source_id == str(created_item_id)
        )
    ) is None
    assert enqueued == []
