from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.lifecycle import LifecyclePolicy, LifecycleRun
from app.services.feed_pipeline import list_item_ids_missing_articles
from app.tasks.feed_tasks import fetch_article


@pytest.fixture()
def purged_article_case(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    feed = Feed(
        id=uuid.uuid4(),
        name="Lifecycle refetch feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="lifecycle-refetch-item",
        url="https://example.com/articles/lifecycle-refetch",
        canonical_url="https://example.com/articles/lifecycle-refetch",
        title="Lifecycle refetch item",
        summary="RSS summary with useful retained context.",
        published_at=now - timedelta(days=60),
        dedupe_key="lifecycle-refetch-item",
        content_hash="a" * 64,
        status="content_fetched",
    )
    policy = LifecyclePolicy(
        target_key="article_content",
        enabled=False,
        retention_days=30,
        schedule_cadence="daily",
        schedule_hour_utc=2,
        schedule_weekday=None,
        max_records_per_run=100,
        options_json={},
        revision=1,
        next_run_at=None,
    )
    db_session.add_all((feed, item, policy))
    db_session.flush()
    run = LifecycleRun(
        target_key=policy.target_key,
        trigger_source="scheduled",
        status="succeeded",
        policy_revision=policy.revision,
        policy_snapshot_json={},
        cutoff_at=now - timedelta(days=30),
        scheduled_for=now,
        max_records=100,
        queued_at=now,
        finished_at=now,
        stop_reason="completed",
    )
    db_session.add(run)
    db_session.flush()
    purged_at = now - timedelta(hours=1)
    article = Article(
        item_id=item.id,
        final_url=item.url,
        retrieved_at=now - timedelta(hours=2),
        http_status=200,
        content_type="text/html; charset=utf-8",
        title_extracted=None,
        text=None,
        extraction_method="retention_purged",
        language=None,
        word_count=None,
        error="no_extractor_succeeded",
        content_purged_at=purged_at,
        content_purge_run_id=run.id,
    )
    db_session.add(article)
    db_session.commit()

    scenario = {
        "status_code": 200,
        "extracted": {
            "title": "Recovered title",
            "text": "Recovered readable article text.",
            "method": "readability",
            "language": "en",
            "word_count": 4,
            "error": None,
        },
    }
    fetches: list[str] = []
    queued: list[str] = []

    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _domain_slot_override(_domain: str, max_wait_seconds: int = 30):
        _ = max_wait_seconds
        yield

    class _Response:
        headers = {"content-type": "text/html; charset=utf-8"}
        url = item.url

        @property
        def status_code(self):
            return scenario["status_code"]

        def iter_bytes(self):
            yield b"<html><body><article>Lifecycle refetch</article></body></html>"

        def close(self):
            pass

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    def _safe_stream(_client, _method: str, url: str, **_kwargs):
        fetches.append(url)
        return _Response()

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.domain_slot", _domain_slot_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.build_safe_http_client",
        lambda *_args, **_kwargs: _Client(),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.safe_stream_with_redirects",
        _safe_stream,
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.extract_canonical_url",
        lambda _html: None,
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.extract_readable_text",
        lambda _html: scenario["extracted"],
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.classify_item.delay",
        lambda item_id: queued.append(item_id),
    )
    return SimpleNamespace(
        now=now,
        item=item,
        article=article,
        run=run,
        purged_at=purged_at,
        scenario=scenario,
        fetches=fetches,
        queued=queued,
        db=db_session,
    )


def _refresh_case(case) -> None:
    case.db.refresh(case.item)
    case.db.refresh(case.article)


def _assert_tombstone_preserved(case) -> None:
    assert case.article.content_purged_at == case.purged_at
    assert case.article.content_purge_run_id == case.run.id
    assert case.article.title_extracted is None
    assert case.article.text is None
    assert case.article.extraction_method == "retention_purged"
    assert case.article.language is None
    assert case.article.word_count is None


def test_forced_refetch_clears_tombstone_after_extracted_text(purged_article_case):
    case = purged_article_case

    result = fetch_article.run(str(case.item.id), force=True)

    _refresh_case(case)
    assert result == {"status": "ok", "item_id": str(case.item.id)}
    assert case.article.text == "Recovered readable article text."
    assert case.article.extraction_method == "readability"
    assert case.article.content_purged_at is None
    assert case.article.content_purge_run_id is None
    assert case.item.status == "content_fetched"


def test_forced_refetch_clears_tombstone_after_rss_fallback(purged_article_case):
    case = purged_article_case
    case.scenario["status_code"] = 403

    result = fetch_article.run(str(case.item.id), force=True)

    _refresh_case(case)
    assert result == {
        "status": "degraded",
        "reason": "rss_summary_fallback",
        "item_id": str(case.item.id),
    }
    assert case.article.text == "RSS summary with useful retained context."
    assert case.article.extraction_method == "rss_summary_fallback"
    assert case.article.content_purged_at is None
    assert case.article.content_purge_run_id is None
    assert case.item.status == "content_fetched"


def test_forced_refetch_preserves_tombstone_after_terminal_failure(
    purged_article_case,
):
    case = purged_article_case
    case.scenario["status_code"] = 503

    result = fetch_article.run(str(case.item.id), force=True)

    _refresh_case(case)
    assert result == {"status": "error", "item_id": str(case.item.id)}
    assert case.article.error == "http_status:503"
    _assert_tombstone_preserved(case)


def test_forced_refetch_preserves_tombstone_without_usable_extracted_text(
    purged_article_case,
):
    case = purged_article_case
    case.item.summary = case.item.title
    case.db.add(case.item)
    case.db.commit()
    case.scenario["extracted"] = {
        "title": "Extractor title without body",
        "text": " \n ",
        "method": "readability",
        "language": "en",
        "word_count": 0,
        "error": "no_extractor_succeeded",
    }

    result = fetch_article.run(str(case.item.id), force=True)

    _refresh_case(case)
    assert result == {"status": "ok", "item_id": str(case.item.id)}
    assert case.article.error == "no_extractor_succeeded"
    _assert_tombstone_preserved(case)


def test_ordinary_repair_skips_lifecycle_tombstones(purged_article_case):
    case = purged_article_case

    repair_candidates = list_item_ids_missing_articles(
        case.db,
        limit=10,
        now=case.now,
        dispatch_after_seconds=60,
    )
    result = fetch_article.run(str(case.item.id))

    _refresh_case(case)
    assert case.item.id not in repair_candidates
    assert result == {
        "status": "skipped",
        "reason": "content_purged_by_lifecycle",
        "item_id": str(case.item.id),
    }
    assert case.fetches == []
    assert case.queued == []
    _assert_tombstone_preserved(case)
