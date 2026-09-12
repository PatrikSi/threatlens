"""Malformed publisher input must fail visibly without stranding feed polling."""

import uuid
from contextlib import contextmanager

import httpx
import pytest
from sqlalchemy import func, select

from app.models.feed import Feed
from app.models.item import Item
from app.services import feed_probe, safe_fetch
from app.tasks import feed_tasks


def feed_document(title: str, *, language: str = "en") -> bytes:
    return (
        '<rss version="2.0"><channel><title>Publisher</title>'
        f'<language>{language}</language><link>https://example.com</link>'
        '<item><guid>source-1</guid><link>https://example.com/article</link>'
        f'<title>{title}</title></item></channel></rss>'
    ).encode("utf-8")


@pytest.fixture
def fetch_feed_input(db_session, monkeypatch):
    feed = Feed(name="Publisher", url=f"https://example.com/{uuid.uuid4()}.xml")
    db_session.add(feed)
    db_session.commit()

    @contextmanager
    def sessions():
        yield db_session

    @contextmanager
    def unlocked(*_args, **_kwargs):
        yield True

    monkeypatch.setattr(feed_tasks, "db_session", sessions)
    monkeypatch.setattr("app.tasks.feed_task_coordination.feed_lock", unlocked)
    monkeypatch.setattr("app.tasks.feed_task_coordination.domain_slot", unlocked)
    monkeypatch.setattr(feed_tasks, "enqueue_article_fetch_processing", lambda *_args: True)
    monkeypatch.setattr("app.tasks.integration_tasks.enqueue_integration_event_routing", lambda *_args: True)
    requests = []

    def fetch(body, *, headers=None):
        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=body, headers=headers or {}, request=request)

        monkeypatch.setattr(safe_fetch, "_ensure_target", lambda *_args: None)
        monkeypatch.setattr(safe_fetch, "build_safe_http_client", lambda **_kwargs: httpx.Client(transport=httpx.MockTransport(handler)))
        result = feed_tasks.fetch_feed.run(str(feed.id), force=True)
        db_session.refresh(feed)
        return result

    return feed, fetch, requests


@pytest.mark.parametrize("title", ["A&#0;B", "A\x00B", "A&#xD800;B"])
def test_invalid_publisher_unicode_records_failure_and_recovers(fetch_feed_input, db_session, title):
    feed, fetch, _requests = fetch_feed_input
    assert fetch(feed_document(title))["status"] == "error"
    assert feed.last_error == "invalid_feed_content"
    assert feed.error_count == 1
    assert feed.next_fetch_at > feed.last_fetch_at
    assert db_session.scalar(select(func.count()).select_from(Item).where(Item.feed_id == feed.id)) == 0

    assert fetch(feed_document("Recovered source"))["status"] == "ok"
    assert feed.error_count == 0
    assert feed.last_error is None


def test_unsupported_conditional_headers_do_not_poison_later_polls(fetch_feed_input):
    feed, fetch, requests = fetch_feed_input
    feed.etag = "old\u00ff"
    feed.last_modified = "old\u00ff"
    assert fetch(feed_document("Source"), headers=[(b"etag", b"\xff"), (b"last-modified", b"\xff")])["status"] == "ok"
    assert "if-none-match" not in requests[0].headers
    assert "if-modified-since" not in requests[0].headers
    assert feed.etag is None
    assert feed.last_modified is None
    assert fetch(feed_document("Updated source"))["status"] == "ok"


def test_invalid_optional_language_does_not_abort_persistence(fetch_feed_input):
    feed, fetch, _requests = fetch_feed_input
    assert fetch(feed_document("Source", language="x" * 65))["status"] == "ok"
    assert feed.language is None


@pytest.mark.parametrize("body", [feed_document("A&#xD800;B"), feed_document("A&#0;B"), b"<html>Maintenance</html>"])
def test_probe_reports_malformed_documents_as_actionable_errors(monkeypatch, body):
    monkeypatch.setattr(safe_fetch, "_ensure_target", lambda *_args: None)
    monkeypatch.setattr(feed_probe, "build_safe_http_client", lambda **_kwargs: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body, request=request),
    )))
    with pytest.raises(feed_probe.FeedProbeError, match="valid feed"):
        feed_probe.probe_feed_metadata("https://example.com/feed.xml")
