from contextlib import contextmanager

import httpx
import pytest

from app.api.routes import feeds as feeds_routes
from app.services import feed_probe, safe_fetch
from app.services.feed_probe import FeedProbeCoordinationError
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

    def validate(active_guard):
        assert "released" not in events
        validations.append(active_guard)

    metadata = feed_probe.probe_feed_metadata(
        "https://example.com/feed.xml",
        request_context=request_context,
        request_guard_validator=validate,
    )

    assert metadata.name == "Guarded feed"
    assert metadata.etag == '"v1"'
    assert len(validations) >= 2
    assert all(value is guard for value in validations)
    assert events == ["entered", "released"]


def test_api_feed_probe_maps_coordination_failure_to_actionable_error(monkeypatch):
    def unavailable_probe(_url: str, **_kwargs):
        raise CoordinationUnavailableError("domain slot unavailable")

    monkeypatch.setattr(feeds_routes, "probe_feed_metadata", unavailable_probe)

    with pytest.raises(FeedProbeCoordinationError, match="temporarily unavailable"):
        feeds_routes._probe_feed_metadata("https://example.com/feed.xml")


@pytest.mark.parametrize("mode", ["compressed_oversize", "deadline", "revoked_guard"])
def test_feed_probe_bounds_decoding_and_duration_while_retaining_coordination(monkeypatch, mode):
    import gzip
    from types import SimpleNamespace

    from app.services import outbound_deadline as deadline_module

    settings = feed_probe.get_settings().model_copy(update={"feed_max_bytes": 10_000, "feed_total_timeout_seconds": 1})
    monkeypatch.setattr(feed_probe, "get_settings", lambda: settings)
    clock = [0.0]
    monkeypatch.setattr(deadline_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    state = {"released": False, "revoked": False, "closed": False}
    guard = object()

    @contextmanager
    def request_context(_url):
        try:
            yield guard
        finally:
            state["released"] = True

    def validate(value):
        assert value is guard
        assert not state["released"]
        if state["revoked"]:
            raise FeedProbeCoordinationError("Guard was revoked")

    class BodyStream(httpx.SyncByteStream):
        def __iter__(self):
            if mode == "compressed_oversize":
                yield gzip.compress(b"x" * 20_000)
                return
            yield b"<rss><channel>"
            if mode == "deadline":
                clock[0] = 2.0
            if mode == "revoked_guard":
                state["revoked"] = True
            yield b"<title>Late feed</title></channel></rss>"

        def close(self):
            state["closed"] = True

    def handler(request):
        return httpx.Response(200, request=request, stream=BodyStream(), headers={
            "content-type": "application/rss+xml",
            **({"content-encoding": "gzip"} if mode == "compressed_oversize" else {}),
        })

    monkeypatch.setattr(safe_fetch, "_ensure_target", lambda *_args: None)
    monkeypatch.setattr(feed_probe, "build_safe_http_client", lambda **_kwargs: httpx.Client(transport=httpx.MockTransport(handler)))
    error = FeedProbeCoordinationError if mode == "revoked_guard" else feed_probe.FeedProbeError
    with pytest.raises(error):
        feed_probe.probe_feed_metadata("https://example.com/feed.xml", request_context=request_context, request_guard_validator=validate)
    assert state["released"]
    assert state["closed"]
