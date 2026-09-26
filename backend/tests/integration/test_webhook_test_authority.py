"""Synthetic sends retain their accepting credential through the HTTP boundary."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.services import notification_webhook_http, notification_webhook_testing
from app.services import export_job_access
from app.services.notification_webhook_test_policy import (
    NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION,
)


@pytest.mark.parametrize(
    "change", ["revoked_after_authentication", "expires_during_dns"]
)
def test_synthetic_webhook_rechecks_accepting_credential_before_send(
    client,
    db_session,
    auth_headers,
    seed_users,
    monkeypatch,
    change,
):
    token = db_session.scalar(
        select(ApiToken).where(ApiToken.user_id == seed_users["analyst"].id)
    )
    expires = datetime.now(timezone.utc) + timedelta(minutes=1)
    token.expires_at = expires
    db_session.commit()
    render = notification_webhook_testing.render_notification_request

    def render_and_revoke(*args, **kwargs):
        result = render(*args, **kwargs)
        if change == "revoked_after_authentication":
            token.revoked_at = datetime.now(timezone.utc)
            db_session.flush()
        return result

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return expires + timedelta(seconds=1)

    def dns(*_args, **_kwargs):
        if change == "expires_during_dns":
            monkeypatch.setattr(export_job_access, "datetime", ExpiredClock)

    sent = []

    @contextmanager
    def safe_client(**_kwargs):
        def respond(request):
            sent.append(request)
            return httpx.Response(204)

        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            yield transport

    monkeypatch.setattr(
        notification_webhook_testing, "render_notification_request", render_and_revoke
    )
    monkeypatch.setattr(notification_webhook_http, "ensure_runtime_fetchable_url", dns)
    monkeypatch.setattr(
        notification_webhook_http, "build_safe_http_client", safe_client
    )
    response = client.post(
        "/notifications/webhooks/test",
        headers=auth_headers["analyst"],
        json={
            "webhook": {
                "name": "Credential fence",
                "event_type": "feed_failing",
                "url_template": "https://receiver.example/hook",
                "body_mode": "none",
            },
        },
    )
    assert response.status_code == 403, response.text
    assert (
        response.headers["X-Error-Code"] == "notification_webhook_test_credential_changed"
    )
    assert not sent
    outcome = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION
        )
    )
    assert outcome.metadata_json["io_outcome"] == "not_sent"
    assert (
        outcome.metadata_json["error_code"]
        == "notification_webhook_test_credential_changed"
    )
