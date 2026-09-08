from app.services import algorithm_tags as _owner_algorithm_tags
from app.services import classification as _owner_classification
from app.tasks import feed_task_constants as _owner_feed_task_constants
from app.tasks import feed_task_coordination as _owner_feed_task_coordination
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID, UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.models.tagging_rule import TaggingRule
from app.services import algorithm_tags
from app.services.bounded_regex import RegexResult
from app.services.classification_recovery import require_item_classification
from app.services.data_access_policy import DataAccessContext
from app.services.tagging_recovery import record_incomplete_tagging, tagging_recovery_summary
from app.tasks import feed_tasks, item_processing_tasks


def _source(db):
    identity = uuid.uuid4()
    feed = Feed(name="Tag recovery", url=f"https://example.com/{identity}.xml",
                handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID)
    db.add(feed)
    db.flush()
    item = Item(id=identity, feed_id=feed.id, title="Original title", url=f"https://example.com/{identity}",
                dedupe_key=str(identity), content_hash="a" * 64)
    db.add(item)
    db.flush()
    article = Article(item_id=item.id, final_url=item.url, http_status=200, text="oldmarker")
    rule = TaggingRule(name="Recovery", tag_name=f"old:{identity.hex[:24]}", match_type="regex", pattern="oldmarker",
                       applies_to_json=["article_text"], feed_scope="selected", feed_ids_json=[str(feed.id)])
    db.add_all([article, rule])
    db.flush()
    assert item_processing_tasks._reapply_item_tags(db, item.id, dependencies=feed_tasks._item_processing_dependencies())
    db.commit()
    return feed, item, article, rule


def _runtime(monkeypatch, db):
    @contextmanager
    def session():
        yield db
    monkeypatch.setattr(feed_tasks, "db_session", session)
    monkeypatch.setattr(item_processing_tasks, "_complete_classification", lambda *_a, **_kw: {"status": "ok"})


def _names(db, item_id):
    db.flush()
    return db.scalars(select(Tag.name).join(ItemTag).where(ItemTag.item_id == item_id)).all()


def _unavailable(monkeypatch, code="worker_unavailable"):
    def evaluation(rules, _texts):
        return [RegexResult([], code) for _ in rules]
    monkeypatch.setattr(algorithm_tags, "evaluate_regex_batch", evaluation)


def test_incomplete_tags_and_classification_recover_independently(db_session, monkeypatch):
    _feed, item, article, rule = _source(db_session)
    _runtime(monkeypatch, db_session)
    _unavailable(monkeypatch)
    feed_tasks.classify_item.run(str(item.id))
    assert item.classification_completed_version == item.classification_required_version
    assert item.tagging_pending and item.tagging_attempts == 1
    assert item.tagging_retry_at > datetime.now(timezone.utc)
    assert rule.tag_name in _names(db_session, item.id)
    assert feed_tasks.repair_pending_item_tags.run() == {"processed": 0, "pending": 0}
    article.text = "newmarker"
    article.retrieved_at = datetime.now(timezone.utc)
    require_item_classification(item)
    item.tagging_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    monkeypatch.setattr(algorithm_tags, "evaluate_regex_batch", lambda rules, _texts: [RegexResult([]) for _ in rules])
    assert feed_tasks.repair_pending_item_tags.run() == {"processed": 1, "pending": 0}
    assert not item.tagging_pending and item.tagging_error_code is None
    assert item.tagging_attempts == 0 and item.tagging_retry_at is None
    assert rule.tag_name not in _names(db_session, item.id)
    assert item.classification_completed_version < item.classification_required_version
    feed_tasks.classify_item.run(str(item.id))
    assert item.classification_completed_version == item.classification_required_version


@pytest.mark.parametrize("code", ["timeout", "budget_exhausted", "input_too_large", "invalid_pattern"])
def test_non_retryable_evaluation_waits_for_manual_reapply(db_session, monkeypatch, code):
    _feed, item, _article, rule = _source(db_session)
    _runtime(monkeypatch, db_session)
    _unavailable(monkeypatch, code)
    feed_tasks.classify_item.run(str(item.id))
    assert item.tagging_pending and item.tagging_retry_at is None
    assert item.tagging_error_code == code
    assert rule.tag_name in _names(db_session, item.id)
    assert feed_tasks.repair_pending_item_tags.run() == {"processed": 0, "pending": 0}
    rule.enabled = False
    db_session.commit()
    assert item_processing_tasks._reapply_item_tags(db_session, item.id, dependencies=feed_tasks._item_processing_dependencies())
    db_session.commit()
    assert not item.tagging_pending
    assert rule.tag_name not in _names(db_session, item.id)


def test_retry_budget_and_backoff_are_bounded(db_session, monkeypatch):
    _feed, item, _article, _rule = _source(db_session)
    _runtime(monkeypatch, db_session)
    _unavailable(monkeypatch, "worker_timeout")
    feed_tasks.classify_item.run(str(item.id))
    for attempt in range(2, 6):
        item.tagging_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
        assert feed_tasks.repair_pending_item_tags.run() == {"processed": 1, "pending": 1}
        assert item.tagging_attempts == attempt
        if attempt < 5:
            assert (item.tagging_retry_at - datetime.now(timezone.utc)).total_seconds() > 60 * 2 ** (attempt - 1) - 5
    assert item.tagging_retry_at is None
    assert feed_tasks.repair_pending_item_tags.run() == {"processed": 0, "pending": 0}


def test_recovery_summary_restricts_counts_and_errors(db_session):
    feed, item, _article, _rule = _source(db_session)
    record_incomplete_tagging(item, ("worker_unavailable",))
    db_session.flush()
    context = DataAccessContext("enforced", 1, 1, "user", uuid.uuid4(), True,
                                frozenset({UNRESTRICTED_HANDLING_LABEL_ID}))
    assert tagging_recovery_summary(db_session, context)["retrying"] == 1
    feed.handling_label_id = QUARANTINE_HANDLING_LABEL_ID
    db_session.flush()
    assert tagging_recovery_summary(db_session, context) == {
        "pending": 0, "retrying": 0, "needs_attention": 0, "errors": [],
    }


def test_settings_bundle_exposes_durable_error(client, auth_headers, db_session):
    _feed, item, _article, _rule = _source(db_session)
    record_incomplete_tagging(item, ("input_too_large",))
    db_session.commit()
    response = client.get("/tagging/settings", headers=auth_headers["admin"])
    assert response.status_code == 200
    summary = response.json()["tagging_recovery"]
    assert (summary["pending"], summary["retrying"], summary["needs_attention"]) == (1, 0, 1)
    assert summary["errors"][0]["code"] == "input_too_large"


@pytest.fixture
def committed_source(database_engine):
    with Session(database_engine) as db:
        feed, item, _article, rule = _source(db)
        ids = feed.id, item.id, rule.id, rule.tag_name
    try:
        yield ids
    finally:
        with Session(database_engine) as db:
            db.execute(delete(Feed).where(Feed.id == ids[0]))
            db.execute(delete(TaggingRule).where(TaggingRule.id == ids[2]))
            db.execute(delete(Tag).where(Tag.name == ids[3]))
            db.commit()


def test_reapply_refreshes_preloaded_source_and_holds_lock(database_engine, committed_source, monkeypatch):
    _feed_id, item_id, _rule_id, tag_name = committed_source
    snapshot_read, writer_committed, evaluating, release = (Event() for _ in range(4))
    actual_classify = _owner_classification.classify_item_content

    def inspect_source(**context):
        assert context["article_text"] == "newmarker"
        assert context["title"] == "New title"
        evaluating.set()
        assert release.wait(10)
        return actual_classify(**context)

    monkeypatch.setattr(_owner_classification, 'classify_item_content', inspect_source)

    def reapply():
        with Session(database_engine) as db:
            stale = [db.get(Item, item_id), db.scalar(select(Article).where(Article.item_id == item_id)),
                     db.get(ItemClassification, item_id)]
            snapshot_read.set()
            assert writer_committed.wait(10)
            assert item_processing_tasks._reapply_item_tags(db, item_id, dependencies=feed_tasks._item_processing_dependencies())
            db.commit()
            assert stale[0].title == "New title"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reapply)
        try:
            assert snapshot_read.wait(10)
            with Session(database_engine) as db:
                item = db.scalar(select(Item).where(Item.id == item_id).with_for_update())
                item.title = "New title"
                article = db.scalar(select(Article).where(Article.item_id == item_id))
                article.text = "newmarker"
                article.retrieved_at = datetime.now(timezone.utc)
                require_item_classification(item)
                db.commit()
            writer_committed.set()
            assert evaluating.wait(10)
            with Session(database_engine) as db:
                with pytest.raises(OperationalError) as exc:
                    db.scalar(select(Item).where(Item.id == item_id).with_for_update(nowait=True))
                assert exc.value.orig.sqlstate == "55P03"
        finally:
            release.set()
            writer_committed.set()
        future.result(timeout=15)
    with Session(database_engine) as db:
        assert tag_name not in _names(db, item_id)
        item = db.get(Item, item_id)
        assert item.classification_required_version > item.classification_completed_version


def test_crashed_repair_rolls_back_and_recovers(database_engine, committed_source, monkeypatch):
    _feed_id, item_id, _rule_id, tag_name = committed_source
    with Session(database_engine) as db:
        item = db.get(Item, item_id)
        record_incomplete_tagging(item, ("worker_unavailable",))
        item.tagging_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    original_sync = _owner_algorithm_tags.sync_item_algorithm_tags

    def crash(db, **context):
        original_sync(db, **context)
        raise RuntimeError("worker terminated before commit")

    @contextmanager
    def session():
        with Session(database_engine) as db:
            yield db

    monkeypatch.setattr(feed_tasks, "db_session", session)
    monkeypatch.setattr(_owner_algorithm_tags, 'sync_item_algorithm_tags', crash)
    with pytest.raises(RuntimeError, match="terminated"):
        feed_tasks.repair_pending_item_tags.run()
    with Session(database_engine) as db:
        assert db.get(Item, item_id).tagging_pending
        assert db.get(Item, item_id).tagging_attempts == 1
        assert tag_name in _names(db, item_id)
    monkeypatch.setattr(_owner_algorithm_tags, 'sync_item_algorithm_tags', original_sync)
    assert feed_tasks.repair_pending_item_tags.run() == {"processed": 1, "pending": 0}
    with Session(database_engine) as db:
        assert not db.get(Item, item_id).tagging_pending


def test_manual_reapply_keyset_handles_tied_dates_and_limit(db_session, monkeypatch):
    _feed, item, _article, _rule = _source(db_session)
    ids = [item.id]
    for _index in range(4):
        other = Item(feed_id=item.feed_id, title="Other", url=f"https://example.com/{uuid.uuid4()}",
                     dedupe_key=str(uuid.uuid4()), content_hash="a" * 64, first_seen_at=item.first_seen_at)
        db_session.add(other)
        db_session.flush()
        ids.append(other.id)
    db_session.commit()
    _runtime(monkeypatch, db_session)

    @contextmanager
    def lock(**_kwargs):
        yield True

    monkeypatch.setattr(_owner_feed_task_coordination, 'tagging_reapply_lock', lock)
    monkeypatch.setattr(_owner_feed_task_constants, 'TAGGING_REAPPLY_COMMIT_INTERVAL', 2)
    visited = []
    actual = item_processing_tasks._reapply_item_tags

    def observe(db, item_id, **kwargs):
        visited.append(item_id)
        return actual(db, item_id, **kwargs)

    monkeypatch.setattr(item_processing_tasks, "_reapply_item_tags", observe)
    assert feed_tasks.reapply_recent_item_tags.run(30, 3)["processed"] == 3
    assert visited == sorted(ids, reverse=True)[:3]
    visited.clear()
    assert feed_tasks.reapply_recent_item_tags.run(30, 0)["processed"] == 5
    assert visited == sorted(ids, reverse=True)
