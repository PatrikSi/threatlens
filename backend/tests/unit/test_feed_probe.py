from contextlib import contextmanager

import httpx
import pytest

from app.api.routes import feeds as feeds_routes
from app.services import feed_probe, safe_fetch
from app.services.feed_probe import FeedProbeError
from app.tasks.feed_task_coordination import CoordinationUnavailableError


def test_feed_probe_holds_and_validates_request_guard(monkeypatch):
    guard = object()
    events: list[str] = []
    validations: list[object] = []

    @contextmanager
    def request_context(_url: str):
        events.append("entered")
        try:
            yield guard
        finally:
            events.append("released")

    def handler(request: httpx.Request) -> httpx.Response:
        class BodyStream(httpx.SyncByteStream):
            def __iter__(self):
                yield (
                    b"<?xml version='1.0'?><rss version='2.0'><channel>"
                    b"<title>Guarded feed</title></channel></rss>"
                )

        return httpx.Response(
            200,
            headers={"content-type": "application/rss+xml", "etag": '"v1"'},
            stream=BodyStream(),
            request=request,
        )

    monkeypatch.setattr(safe_fetch, "_ensure_target", lambda *_args: None)
    monkeypatch.setattr(
        feed_probe,
        "build_safe_http_client",
        lambda **_kwargs: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    metadata = feed_probe.probe_feed_metadata(
        "https://example.com/feed.xml",
        request_context=request_context,
        request_guard_validator=validations.append,
    )

    assert metadata.name == "Guarded feed"
    assert metadata.etag == '"v1"'
    assert validations == [guard, guard]
    assert events == ["entered", "released"]


def test_api_feed_probe_maps_coordination_failure_to_actionable_error(monkeypatch):
    def unavailable_probe(_url: str, **_kwargs):
        raise CoordinationUnavailableError("domain slot unavailable")

    monkeypatch.setattr(feeds_routes, "probe_feed_metadata", unavailable_probe)

    with pytest.raises(FeedProbeError, match="temporarily unavailable"):
        feeds_routes._probe_feed_metadata("https://example.com/feed.xml")
