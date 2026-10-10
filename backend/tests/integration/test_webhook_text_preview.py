"""Real API previews and compatibility updates for optional article bodies."""

import json
import uuid
import pytest
from datetime import timedelta

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services.integration_events import emit_integration_event


def event_with_text(db):
    feed = Feed(name="Preview feed", url=f"https://preview.example/{uuid.uuid4()}")
    db.add(feed)
    db.flush()
    item = Item(
        feed_id=feed.id,
        title="Evidence source",
        url="https://preview.example/article",
        dedupe_key=str(uuid.uuid4()),
        content_hash="b" * 64,
    )
    db.add(item)
    db.flush()
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Inspected article evidence " * 600,
    )
    db.add(article)
    db.flush()
    event = emit_integration_event(
        db,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"text-preview:{uuid.uuid4()}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    db.commit()
    return event, article


def test_stored_template_preview_renders_full_text_without_send_and_discloses_change(
    client, db_session, auth_headers, monkeypatch
):
    from app.services import notification_webhook_http

    def forbidden(*_args, **_kwargs):
        raise AssertionError("preview attempted external I/O")

    monkeypatch.setattr(
        notification_webhook_http, "send_rendered_notification_request", forbidden
    )
    event, article = event_with_text(db_session)
    request = {
        "event_id": str(event.id),
        "webhook": {
            "name": "Preview",
            "url_template": "https://siem.example/events",
            "body_fields": [
                {"key": "text", "value": "{{item.full_text}}"},
                {"key": "status", "value": "{{item.full_text_status}}"},
            ],
        },
    }
    response = client.post(
        "/notifications/webhooks/preview", headers=auth_headers["analyst"], json=request
    )
    assert response.status_code == 200, response.text
    assert response.json()["template_body_error"] is None
    assert json.loads(response.json()["template_body"]) == {
        "text": article.text,
        "status": "available",
    }
    article.retrieved_at += timedelta(seconds=1)
    article.text = "New body must not appear in the historical event"
    db_session.commit()
    response = client.post(
        "/notifications/webhooks/preview", headers=auth_headers["analyst"], json=request
    )
    assert response.status_code == 200, response.text
    assert json.loads(response.json()["template_body"]) == {
        "text": "",
        "status": "source_changed",
    }


def test_structured_preview_excludes_text_until_opted_in(
    client, db_session, auth_headers
):
    event, article = event_with_text(db_session)
    request = {
        "event_id": str(event.id),
        "webhook": {
            "name": "Preview",
            "url_template": "https://siem.example/events",
            "payload_mode": "automation_v1",
        },
    }
    response = client.post(
        "/notifications/webhooks/preview", headers=auth_headers["analyst"], json=request
    )
    assert response.status_code == 200, response.text
    assert "article_text" not in response.json()["automation_payload"]["data"]
    request["webhook"]["include_article_text"] = True
    response = client.post(
        "/notifications/webhooks/preview", headers=auth_headers["analyst"], json=request
    )
    assert response.status_code == 200, response.text
    text = response.json()["automation_payload"]["data"]["article_text"]
    assert text["text"] == article.text
    assert text["status"] == "available"


def test_older_personal_editor_preserves_article_text_setting(client, auth_headers):
    body = {
        "name": "SOC destination",
        "url_template": "https://siem.example/events",
        "payload_mode": "automation_v1",
        "include_article_text": True,
    }
    response = client.post(
        "/notifications/webhooks", headers=auth_headers["analyst"], json=body
    )
    assert response.status_code == 201, response.text
    webhook_id = response.json()["id"]
    body.pop("include_article_text")
    body["name"] = "Older editor rename"
    response = client.patch(
        f"/notifications/webhooks/{webhook_id}",
        headers=auth_headers["analyst"],
        json=body,
    )
    assert response.status_code == 200, response.text
    assert response.json()["include_article_text"] is True


def test_full_text_sample_requires_article_read_permission_before_loading(
    client, db_session, auth_headers, seed_users, monkeypatch
):
    from sqlalchemy import select
    from app.models.api_token import ApiToken
    from app.services import notification_webhook_testing

    token = db_session.scalar(
        select(ApiToken).where(ApiToken.user_id == seed_users["analyst"].id)
    )
    token.scopes = ["write:notifications"]
    db_session.commit()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unauthorized sample resolved article content")

    monkeypatch.setattr(
        notification_webhook_testing, "_resolve_sample_feed_and_item", forbidden
    )
    response = client.post(
        "/notifications/webhooks/test",
        headers=auth_headers["analyst"],
        json={
            "webhook": {
                "name": "Content",
                "url_template": "https://receiver.example/events",
                "body_fields": [{"key": "text", "value": "{{item.full_text}}"}],
            }
        },
    )
    assert response.status_code == 403, response.text
    assert "read:items" in response.text


def test_full_text_sample_rechecks_article_read_scope_after_dns(
    client, db_session, auth_headers, seed_users, monkeypatch
):
    from contextlib import contextmanager
    import httpx
    from sqlalchemy import select
    from app.models.api_token import ApiToken
    from app.services import notification_webhook_http

    event, article = event_with_text(db_session)
    token = db_session.scalar(
        select(ApiToken).where(ApiToken.user_id == seed_users["analyst"].id)
    )
    token.scopes = ["write:notifications", "read:items"]
    db_session.commit()

    def dns(*_args, **_kwargs):
        token.scopes = ["write:notifications"]
        db_session.flush()

    sent = []

    @contextmanager
    def safe_client(**_kwargs):
        def respond(request):
            sent.append(request)
            return httpx.Response(204)

        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            yield transport

    monkeypatch.setattr(notification_webhook_http, "ensure_runtime_fetchable_url", dns)
    monkeypatch.setattr(
        notification_webhook_http, "build_safe_http_client", safe_client
    )
    response = client.post(
        "/notifications/webhooks/test",
        headers=auth_headers["analyst"],
        json={
            "sample_item_id": str(article.item_id),
            "webhook": {
                "name": "Content",
                "url_template": "https://receiver.example/events",
                "body_fields": [{"key": "text", "value": "{{item.full_text}}"}],
            },
        },
    )
    assert response.status_code == 403, response.text
    assert "read:items" in response.text
    assert not sent


@pytest.mark.parametrize("surface", ["events", "preview"])
def test_event_evidence_rechecks_current_read_scope_under_fence(
    client, db_session, auth_headers, seed_users, monkeypatch, surface
):
    from sqlalchemy import select
    from app.models.api_token import ApiToken
    from app.services import webhook_request_authority

    event, _article = event_with_text(db_session)
    token = db_session.scalar(
        select(ApiToken).where(ApiToken.user_id == seed_users["analyst"].id)
    )
    token.scopes = ["read:notifications", "read:items"]
    db_session.commit()
    capture = webhook_request_authority.capture_export_authorization

    def capture_and_reduce(request, authorization, access):
        snapshot = capture(request, authorization, access)
        token.scopes = ["read:notifications"]
        db_session.flush()
        return snapshot

    monkeypatch.setattr(
        webhook_request_authority, "capture_export_authorization", capture_and_reduce
    )
    if surface == "events":
        response = client.get(
            "/notifications/webhooks/events",
            params={"event_type": "rss_item_new"},
            headers=auth_headers["analyst"],
        )
    else:
        response = client.post(
            "/notifications/webhooks/preview",
            headers=auth_headers["analyst"],
            json={
                "event_id": str(event.id),
                "webhook": {
                    "name": "Read test",
                    "url_template": "https://receiver.example/events",
                    "body_fields": [{"key": "text", "value": "{{item.full_text}}"}],
                },
            },
        )
    assert response.status_code == 403, response.text
    assert "Inspected article evidence" not in response.text
