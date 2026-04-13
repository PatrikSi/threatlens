import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.tag import Tag


def test_admin_can_manage_tagging_settings_and_preview_rules(client: TestClient, auth_headers, db_session):
    feed = Feed(
        id=uuid.uuid4(),
        name="Fortinet Feed",
        url="https://example.com/fortinet.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.flush()
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="fortinet-item",
        url="https://example.com/articles/fortinet",
        canonical_url="https://example.com/articles/fortinet",
        title="Fortinet patches active exploitation",
        summary="Security advisory for Fortinet customers.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="fortinet-item",
        content_hash="a" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed Fortinet devices under exploitation in the wild.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=[],
        confidence=0.88,
        scores_json={"vulnerability": 7.5},
        matched_terms_json={"vulnerability": ["title:cve"]},
        source_hash="hash",
        rules_version="v2",
        classified_at=datetime.now(timezone.utc),
    )
    db_session.add(item)
    db_session.flush()
    db_session.add_all([article, classification])
    db_session.commit()

    initial_response = client.get("/tagging/settings", headers=auth_headers["admin"])
    assert initial_response.status_code == 200
    initial_payload = initial_response.json()
    assert "vulnerability" in initial_payload["settings"]["enabled_categories"]
    assert initial_payload["rules"] == []

    update_response = client.put(
        "/tagging/settings",
        json={
            "enabled_categories": ["vulnerability", "apt_campaign", "threat_intelligence_research"],
            "min_auto_tag_confidence": 0.61,
            "secondary_tag_limit": 1,
        },
        headers=auth_headers["admin"],
    )
    assert update_response.status_code == 200
    updated_settings = update_response.json()
    assert updated_settings["secondary_tag_limit"] == 1
    assert updated_settings["min_auto_tag_confidence"] == 0.61

    rule_payload = {
        "name": "Fortinet vendor",
        "tag_name": "vendor:fortinet",
        "enabled": True,
        "match_type": "contains",
        "pattern": "fortinet",
        "case_sensitive": False,
        "applies_to": ["title", "article_text"],
        "required_categories": ["vulnerability"],
        "feed_scope": "selected",
        "feed_ids": [str(feed.id)],
        "min_classification_confidence": 0.4,
    }
    create_response = client.post("/tagging/rules", json=rule_payload, headers=auth_headers["admin"])
    assert create_response.status_code == 201
    created_rule = create_response.json()
    assert created_rule["tag_name"] == "vendor:fortinet"

    preview_response = client.post(
        "/tagging/rules/preview",
        json={**rule_payload, "limit": 5},
        headers=auth_headers["admin"],
    )
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["total"] == 1
    assert preview_payload["items"][0]["title"] == item.title
    assert preview_payload["items"][0]["matched_sections"] == ["title", "article_text"]

    bundle_response = client.get("/tagging/settings", headers=auth_headers["admin"])
    assert bundle_response.status_code == 200
    bundle_payload = bundle_response.json()
    assert len(bundle_payload["rules"]) == 1
    assert bundle_payload["rules"][0]["name"] == "Fortinet vendor"

    created_tag = db_session.scalar(select(Tag).where(Tag.name == "vendor:fortinet"))
    assert created_tag is not None


def test_non_admin_cannot_access_tagging_settings(client: TestClient, auth_headers):
    response = client.get("/tagging/settings", headers=auth_headers["viewer"])
    assert response.status_code == 403


def test_admin_can_queue_tagging_reapply(client: TestClient, auth_headers, monkeypatch):
    captured: dict[str, object] = {}

    class _FakeTask:
        id = "retag-task-123"

    def _fake_delay(days: int, limit: int):
        captured["days"] = days
        captured["limit"] = limit
        return _FakeTask()

    monkeypatch.setattr("app.api.routes.tagging.reapply_recent_item_tags.delay", _fake_delay)

    response = client.post(
        "/tagging/reapply",
        json={"days": 14, "limit": 250},
        headers=auth_headers["admin"],
    )
    assert response.status_code == 200
    assert response.json() == {"task_id": "retag-task-123", "queued": True}
    assert captured == {"days": 14, "limit": 250}
