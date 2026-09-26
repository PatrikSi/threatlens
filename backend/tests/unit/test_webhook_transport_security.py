from contextlib import contextmanager
from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.schemas.notification import NotificationWebhookWrite
from app.services import notification_webhook_http, safe_fetch
from app.services.notification_webhook_requests import render_notification_request


@pytest.mark.parametrize(
    "url,private_only",
    [("http://receiver.internal/hook", True), ("https://receiver.example/hook", False)],
)
def test_plaintext_webhook_uses_pinned_private_only_transport(
    monkeypatch, url, private_only
):
    rendered = render_notification_request(
        NotificationWebhookWrite(name="Internal", url_template=url, body_mode="none"),
        user=SimpleNamespace(id=uuid.uuid4(), email="operator@example.org"),
        item=None,
        feed=None,
    )
    selected = []

    @contextmanager
    def safe_client(**kwargs):
        selected.append(kwargs["private_network_only"])
        with httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(204))
        ) as client:
            yield client

    monkeypatch.setattr(
        notification_webhook_http, "build_safe_http_client", safe_client
    )
    monkeypatch.setattr(
        notification_webhook_http,
        "ensure_runtime_fetchable_url",
        lambda *_args, **_kwargs: None,
    )
    assert notification_webhook_http.send_rendered_notification_request(
        rendered
    ).success
    assert selected == [private_only]


def test_private_plaintext_hostname_cannot_rebind_to_public_address(monkeypatch):
    monkeypatch.setattr(
        safe_fetch, "resolve_runtime_allowed_ips", lambda *_args, **_kwargs: ["8.8.8.8"]
    )
    backend = safe_fetch._PinnedSyncBackend(
        allow_private_network=True, private_network_only=True
    )
    monkeypatch.setattr(
        backend._backend,
        "connect_tcp",
        lambda **_kwargs: pytest.fail("Public plaintext connection attempted"),
    )
    with pytest.raises(safe_fetch.UnsafeTargetError):
        backend.connect_tcp("receiver.internal", 80)
