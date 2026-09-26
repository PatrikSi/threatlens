"""Verify serialized requests through HTTPX, including the native signing path."""

import hashlib
import hmac
from types import SimpleNamespace
import uuid
import warnings

import httpx
import pytest

from app.services import notification_webhook_http as transport
from app.services import webhook_credentials
from app.services.secret_storage import encrypt_text


FORM_FIELDS = [("tag", "one"), ("tag", "two"), ("query", "é /+&="), ("empty", "")]
FORM_BYTES = b"tag=one&tag=two&query=%C3%A9+%2F%2B%26%3D&empty="
SIGNING_KEY = "synthetic-request-encoding-signing-key"


@pytest.mark.parametrize("redirect_status", [None, 307, 308])
@pytest.mark.parametrize(
    "json_body,form_body,raw_body,custom_type,expected_body,expected_type",
    [
        (None, FORM_FIELDS, None, None, FORM_BYTES, "application/x-www-form-urlencoded"),
        (None, [], None, None, b"", "application/x-www-form-urlencoded"),
        (None, FORM_FIELDS, None, "application/custom-form", FORM_BYTES, "application/custom-form"),
        (None, None, "é\nraw\x00".encode(), "application/octet-stream", "é\nraw\x00".encode(), "application/octet-stream"),
        (None, None, b"", "text/plain", b"", "text/plain"),
        ({"text": "é", "values": [1, False]}, None, None, None, '{"text":"é","values":[1,false]}'.encode(), "application/json"),
        (None, None, None, None, b"", None),
    ],
    ids=["form", "empty-form", "custom-form-type", "raw", "empty-raw", "json", "no-body"],
)
def test_body_encoding_and_native_signature_match_sent_bytes(
    monkeypatch, redirect_status, json_body, form_body, raw_body,
    custom_type, expected_body, expected_type,
):
    profile_id, user_id = uuid.uuid4(), uuid.uuid4()
    profile = SimpleNamespace(
        auth_type="bearer",
        auth_secret_encrypted=encrypt_text("synthetic-auth-token"),
        signing_secret_encrypted=encrypt_text(SIGNING_KEY),
        revision=3,
    )
    loaded = []

    def load_profile(db, *, profile_id, user_id, lock):
        loaded.append((profile_id, user_id, lock))
        return profile

    monkeypatch.setattr(webhook_credentials, "load_credential", load_profile)
    checked_urls = []
    monkeypatch.setattr(
        transport, "ensure_runtime_fetchable_url",
        lambda url, **_kwargs: checked_urls.append(url),
    )
    headers = {"X-Caller": "unchanged"}
    if custom_type:
        headers["content-type"] = custom_type
    original_headers = dict(headers)
    sent = []

    def receive(request):
        assert request.method == "POST"
        assert request.read() == expected_body
        assert request.headers.get("content-type") == expected_type
        assert request.headers.get_list("content-type") == ([expected_type] if expected_type else [])
        assert int(request.headers["content-length"]) == len(expected_body)
        assert "transfer-encoding" not in request.headers
        assert request.headers["authorization"] == "Bearer synthetic-auth-token"
        assert request.headers["x-threatlens-event-id"] == "event-1"
        assert request.headers["x-threatlens-attempt-id"] == "attempt-1"
        assert request.headers["x-threatlens-key-revision"] == "3"
        timestamp = request.headers["x-threatlens-timestamp"]
        canonical = f"v1\n{timestamp}\nevent-1\nattempt-1\n".encode() + expected_body
        expected_signature = hmac.new(SIGNING_KEY.encode(), canonical, hashlib.sha256).hexdigest()
        assert request.headers["x-threatlens-signature"] == f"v1={expected_signature}"
        sent.append(request)
        if redirect_status and len(sent) == 1:
            return httpx.Response(redirect_status, headers={"Location": "/final"})
        return httpx.Response(204)

    callback = webhook_credentials.credential_request_callback(
        None, profile_id=profile_id, user_id=user_id,
        event_id="event-1", attempt_id="attempt-1",
    )
    with (
        httpx.Client(transport=httpx.MockTransport(receive)) as client,
        transport.notification_request_credentials(callback),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("error", DeprecationWarning)
        response = transport.send_request_with_redirects(
            client, method="POST", url="https://receiver.example/start",
            headers=headers, params=[("attempt", "first")],
            json_body=json_body, form_body=form_body, raw_body=raw_body,
        )
        assert response.status_code == 204
        response.close()

    expected_urls = ["https://receiver.example/start"]
    if redirect_status:
        expected_urls.append("https://receiver.example/final")
    assert checked_urls == expected_urls
    assert len(sent) == len(expected_urls)
    assert str(sent[0].url) == "https://receiver.example/start?attempt=first"
    if redirect_status:
        assert str(sent[-1].url) == "https://receiver.example/final"
    assert loaded == [(profile_id, user_id, True)] * len(sent)
    assert headers == original_headers


@pytest.mark.parametrize("redirect_status", [301, 302, 303])
def test_form_redirect_to_get_drops_encoded_body_and_automatic_content_type(
    monkeypatch, redirect_status,
):
    monkeypatch.setattr(
        transport, "ensure_runtime_fetchable_url", lambda *_args, **_kwargs: None,
    )
    sent = []

    def receive(request):
        sent.append(request)
        if len(sent) == 1:
            assert request.content == FORM_BYTES
            assert request.headers["content-type"] == "application/x-www-form-urlencoded"
            return httpx.Response(redirect_status, headers={"Location": "/final"})
        assert request.method == "GET"
        assert request.content == b""
        assert "content-type" not in request.headers
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        response = transport.send_request_with_redirects(
            client, method="POST", url="https://receiver.example/start",
            headers={}, params=[], json_body=None, form_body=FORM_FIELDS, raw_body=None,
        )
        assert response.status_code == 204
        response.close()
    assert len(sent) == 2
