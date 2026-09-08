from __future__ import annotations
from app.services import extraction as _owner_extraction
from app.tasks import feed_task_runtime as _owner_feed_task_runtime

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.services.classification import compute_classification_source_hash
from app.services.classification_recovery import require_item_classification
from app.services.feed_pipeline import upsert_item_from_parsed
from app.tasks import article_fetch_tasks, feed_tasks, item_processing_tasks
from app.tasks.feed_task_dispatchers import dispatch_unclassified_items
from app.tasks.feed_task_storage import store_article_error


def _source(db):
    key = uuid.uuid4().hex
    feed = Feed(name="Recovery feed", url=f"https://example.com/{key}.xml")
    db.add(feed)
    db.flush()
    parsed = SimpleNamespace(url=f"https://example.com/{key}", guid=key, title="Original title",
                             summary="Original summary", published_at=None)
    item, _, _ = upsert_item_from_parsed(db, feed, parsed)
    article = Article(item_id=item.id, final_url=item.url, http_status=200, text="Original article")
    db.add(article)
    db.commit()
    return feed, item, article, parsed


def _runtime(monkeypatch, db):
    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(feed_tasks, "db_session", session)
    monkeypatch.setattr(item_processing_tasks, "_sync_classification_tags", lambda *_a, **_kw: None)
    monkeypatch.setattr(item_processing_tasks, "_complete_classification", lambda *_a, **_kw: {"status": "ok"})
    monkeypatch.setattr(_owner_extraction, 'extract_canonical_url', lambda _html: None)
    monkeypatch.setattr(_owner_extraction, 'extract_readable_text', lambda _html: {
        "text": "Refreshed article", "title": "Refreshed", "method": "readable",
        "word_count": 2, "error": None,
    })
    return session


def _repair(session, enqueue, limit=50):
    return dispatch_unclassified_items(
        db_session_factory=session,
        settings=SimpleNamespace(dispatch_unclassified_items_batch_size=limit),
        enqueue_classification_task=enqueue,
    )


def test_saved_article_and_failed_broker_publish_repair_stale_classification(db_session, monkeypatch):
    _feed, item, article, _parsed = _source(db_session)
    session = _runtime(monkeypatch, db_session)
    item_id = str(item.id)
    item_processing_tasks.run_classify_item(item_id, dependencies=feed_tasks._item_processing_dependencies())
    original_hash = db_session.get(ItemClassification, item.id).source_hash
    assert item.classification_completed_version == item.classification_required_version
    monkeypatch.setattr(article_fetch_tasks, "_fetch_candidates", lambda *_a, **_kw:
                        article_fetch_tasks.ArticleFetchResult(item.url, 200, "text/html", body=b"html"))
    publications = []

    def broker_down(_item_id):
        publications.append(_item_id)
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(feed_tasks.classify_item, "delay", broker_down)
    result = article_fetch_tasks.run_fetch_article(None, item_id, force=True, dependencies=feed_tasks._article_fetch_dependencies())
    db_session.refresh(item)
    db_session.refresh(article)
    assert result == {"status": "ok", "item_id": item_id}
    assert article.text == "Refreshed article"
    assert item.classification_completed_version < item.classification_required_version
    assert db_session.get(ItemClassification, item.id).source_hash == original_hash
    assert publications == [item_id]
    assert _repair(session, lambda _id: False) == {"queued": 0}
    queued = []
    assert _repair(session, lambda value: queued.append(value) or True) == {"queued": 1}
    assert queued == [item_id]
    item_processing_tasks.run_classify_item(item_id, dependencies=feed_tasks._item_processing_dependencies())
    assert db_session.get(ItemClassification, item.id).source_hash == compute_classification_source_hash(
        title=item.title, summary=item.summary, article_text=article.text,
    )
    assert item.classification_completed_version == item.classification_required_version
    assert _repair(session, lambda _id: pytest.fail("current item must not be repaired")) == {"queued": 0}


def test_feed_source_changes_and_missing_rows_are_repaired_with_bounded_deduplication(db_session, monkeypatch):
    feed, item, _article, parsed = _source(db_session)
    session = _runtime(monkeypatch, db_session)
    item_processing_tasks.run_classify_item(str(item.id), dependencies=feed_tasks._item_processing_dependencies())
    parsed.summary = "Changed RSS summary"
    upsert_item_from_parsed(db_session, feed, parsed)
    db_session.commit()
    assert item.classification_completed_version < item.classification_required_version
    _other_feed, other, _other_article, _ = _source(db_session)
    other.classification_completed_version = other.classification_required_version
    db_session.commit()
    queued = []
    assert _repair(session, lambda value: queued.append(value) or True, limit=1) == {"queued": 1}
    assert len(queued) == 1
    queued.clear()
    assert _repair(session, lambda value: queued.append(value) or True) == {"queued": 2}
    assert set(queued) == {str(item.id), str(other.id)}


def test_skipped_classifier_does_not_acknowledge_pending_source(db_session, monkeypatch):
    _feed, item, _article, _ = _source(db_session)
    session = _runtime(monkeypatch, db_session)
    monkeypatch.setattr(_owner_feed_task_runtime, 'claim_item_processing_target', lambda *_a, **_kw:
                        (None, "concurrent_fetch_in_progress"))
    result = item_processing_tasks.run_classify_item(str(item.id), dependencies=feed_tasks._item_processing_dependencies())
    assert result["reason"] == "concurrent_fetch_in_progress"
    queued = []
    assert _repair(session, lambda value: queued.append(value) or True) == {"queued": 1}
    assert queued == [str(item.id)]


def test_article_error_fallback_persists_classification_requirement(db_session):
    _feed, item, article, _ = _source(db_session)
    item.classification_completed_version = item.classification_required_version
    db_session.commit()
    store_article_error(db_session, item, item.url, 503, "text/html", 1, "http_status:503")
    db_session.refresh(item)
    db_session.refresh(article)
    assert article.text != "Original article"
    assert item.classification_completed_version < item.classification_required_version


def test_article_and_classification_requirement_rollback_together(db_session, monkeypatch):
    _feed, item, article, _ = _source(db_session)
    _runtime(monkeypatch, db_session)
    version = item.classification_required_version

    def fail_commit():
        db_session.flush()
        raise RuntimeError("commit unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit unavailable"):
            article_fetch_tasks._store_article_success(
                db_session, item,
                article_fetch_tasks.ArticleFetchResult(item.url, 200, "text/html", body=b"html"),
                1, dependencies=feed_tasks._article_fetch_dependencies(),
            )
    db_session.rollback()
    db_session.refresh(item)
    db_session.refresh(article)
    assert article.text == "Original article"
    assert item.classification_required_version == version


def test_concurrent_stale_item_snapshots_cannot_lose_source_revision(database_engine):
    with Session(database_engine) as setup:
        feed, item, _article, _ = _source(setup)
        feed_id, item_id = feed.id, item.id
    try:
        with Session(database_engine) as first, Session(database_engine) as second:
            first_item = first.get(Item, item_id)
            second_item = second.get(Item, item_id)
            assert first_item.classification_required_version == second_item.classification_required_version == 1
            require_item_classification(first_item)
            first.commit()
            require_item_classification(second_item)
            second.commit()
            second.refresh(second_item)
            assert second_item.classification_required_version == 3
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(delete(Feed).where(Feed.id == feed_id))
            cleanup.commit()
