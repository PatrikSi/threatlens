from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.services.ai_ops import AI_TASK_TYPE_ITEM_ENRICHMENT, AI_TRIGGER_MANUAL, queue_ai_task_run


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def issue_api_token(db_session, user: User, *, name: str, scopes: list[str], expires_at: datetime) -> str:
    token_value, token_prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=user.id,
            name=name,
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=expires_at,
        )
    )
    db_session.commit()
    return token_value


def test_api_token_children_are_short_lived_and_cannot_delegate_write_tokens(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    parent_token = issue_api_token(
        db_session,
        admin,
        name="token-delegator",
        scopes=["write:tokens", "read:feeds"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )

    child_response = client.post(
        "/tokens",
        json={"name": "feed-reader-child", "expires_in_days": 30, "scopes": ["read:feeds"]},
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert child_response.status_code == 201

    child_payload = child_response.json()
    child_expires_at = datetime.fromisoformat(child_payload["expires_at"])
    assert child_expires_at <= datetime.now(timezone.utc) + timedelta(hours=1, minutes=2)

    escalated_response = client.post(
        "/tokens",
        json={"name": "child-delegator", "expires_in_days": 1, "scopes": ["write:tokens"]},
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert escalated_response.status_code == 403
    assert escalated_response.json()["detail"] == "API tokens cannot mint child tokens with write:tokens scope"


def test_ai_settings_route_rejects_unsafe_base_url_with_shared_server_key(
    client: TestClient,
    auth_headers,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "shared-provider-secret")

    response = client.put(
        "/ai/settings",
        json={
            "provider_type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "local-threat-model",
            "summary_enabled": True,
            "relevance_enabled": True,
            "daily_brief_enabled": True,
            "auto_enrich_new_items": True,
            "daily_brief_window_hours": 24,
            "daily_brief_max_items": 10,
            "relevance_medium_threshold": 0.55,
            "relevance_high_threshold": 0.8,
            "company_regions": [],
            "company_stack": [],
            "company_priority_topics": [],
            "company_keywords": [],
            "company_exclusions": [],
        },
        headers=auth_headers["admin"],
    )

    assert response.status_code == 422
    assert "https://api.openai.com" in response.text


def test_item_and_ai_responses_sanitize_raw_url_query_secrets(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AI_ENABLED", "true")

    feed = Feed(
        id=uuid.uuid4(),
        name="Threat Feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="security-batch-item",
        url="https://example.com/articles/1?token=secret&keep=1",
        canonical_url="https://example.com/articles/1?sig=abc123&view=full",
        title="Security batch item",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="security-batch-item",
        content_hash="a" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url="https://example.com/final?auth=secret&foo=bar",
        http_status=200,
        text="Analysts observed active exploitation.",
        extraction_method="readable",
    )
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add(article)

    actor = db_session.scalar(select(User).where(User.email == "admin@example.com"))
    assert actor is not None
    queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=actor.id,
        item_id=item.id,
        metadata={"source": "security-surface-batch"},
    )
    db_session.commit()

    list_response = client.get("/items", headers=auth_headers["viewer"])
    assert list_response.status_code == 200
    list_item = next(entry for entry in list_response.json()["items"] if entry["id"] == str(item.id))
    assert list_item["url"] == "https://example.com/articles/1?keep=1"
    assert list_item["canonical_url"] == "https://example.com/articles/1?view=full"

    detail_response = client.get(f"/items/{item.id}", headers=auth_headers["viewer"])
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["url"] == "https://example.com/articles/1?keep=1"
    assert detail_payload["canonical_url"] == "https://example.com/articles/1?view=full"
    assert detail_payload["article"]["final_url"] == "https://example.com/final?foo=bar"

    ai_response = client.get("/ai/ops/runs?limit=10", headers=auth_headers["admin"])
    assert ai_response.status_code == 200
    ai_item = next(entry for entry in ai_response.json()["items"] if entry["item_id"] == str(item.id))
    assert ai_item["item_url"] == "https://example.com/articles/1?keep=1"
