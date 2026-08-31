from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_data_access_context
from app.main import app
from app.models.article import Article
from app.models.data_policy import (
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.models.tagging_rule import TaggingRule
from app.services.data_access_policy import DataAccessContext, DataPolicyMode


def _data_access_context(mode: DataPolicyMode) -> DataAccessContext:
    return DataAccessContext(
        mode=mode,
        policy_revision=1,
        coverage_version=1,
        principal_type="user",
        principal_id=uuid.uuid4(),
        principal_eligible=True,
        allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
    )


@contextmanager
def _override_data_access(mode: DataPolicyMode) -> Iterator[None]:
    previous = app.dependency_overrides.get(get_data_access_context)
    context = _data_access_context(mode)
    app.dependency_overrides[get_data_access_context] = lambda: context
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_data_access_context, None)
        else:
            app.dependency_overrides[get_data_access_context] = previous


def _feed(
    db: Session,
    *,
    name: str,
    handling_label_id: uuid.UUID,
) -> Feed:
    feed = Feed(
        name=name,
        url=f"https://example.com/{name.lower().replace(' ', '-')}.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        handling_label_id=handling_label_id,
    )
    db.add(feed)
    db.flush()
    return feed


def _item(
    db: Session,
    *,
    feed: Feed,
    key: str,
    title: str,
    category: str,
    status: str = "content_fetched",
) -> Item:
    observed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid=key,
        url=f"https://{key}.example/article",
        canonical_url=f"https://{key}.example/article",
        url_domain=f"{key}.example",
        title=title,
        summary=f"Summary for {key}",
        published_at=observed_at,
        first_seen_at=observed_at,
        dedupe_key=f"policy:{key}",
        content_hash=key.ljust(64, "0")[:64],
        status=status,
    )
    db.add(item)
    db.flush()
    db.add_all(
        [
            Article(
                item_id=item.id,
                final_url=item.url,
                http_status=200,
                text=f"Extracted text for {key}",
                extraction_method="readable",
            ),
            ItemClassification(
                item_id=item.id,
                primary_category=category,
                secondary_categories=[],
                confidence=0.9,
                scores_json={category: 9.0},
                matched_terms_json={category: ["title:policy"]},
                source_hash=key.ljust(64, "1")[:64],
                rules_version="v2",
                classified_at=observed_at,
            ),
        ]
    )
    return item


def _seed_stats_data(db: Session) -> tuple[Feed, Feed]:
    visible_feed = _feed(
        db,
        name="Visible aggregate feed",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = _feed(
        db,
        name="Restricted aggregate feed",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    _item(
        db,
        feed=visible_feed,
        key="visible-aggregate",
        title="Policy hit visible",
        category="vulnerability",
    )
    for index in range(3):
        _item(
            db,
            feed=restricted_feed,
            key=f"restricted-aggregate-{index}",
            title=f"Policy hit restricted {index}",
            category="apt_campaign",
            status="error" if index == 2 else "content_fetched",
        )
    db.commit()
    return visible_feed, restricted_feed


def test_enforced_stats_filter_before_aggregation_ranking_and_limits(
    client: TestClient,
    auth_headers,
    db_session: Session,
):
    visible_feed, restricted_feed = _seed_stats_data(db_session)

    with _override_data_access("enforced"):
        overview = client.get("/stats/overview?days=7", headers=auth_headers["viewer"])
        timeseries = client.get(
            "/stats/feed-timeseries?days=7&top_feeds=1",
            headers=auth_headers["viewer"],
        )
        heatmap = client.get(
            "/stats/activity-heatmap?days=7", headers=auth_headers["viewer"]
        )
        radar = client.get("/stats/signal-radar?days=7", headers=auth_headers["viewer"])
        restricted_series = client.get(
            f"/stats/feed-timeseries?days=7&feed_ids={restricted_feed.id}",
            headers=auth_headers["viewer"],
        )
        unknown_series = client.get(
            f"/stats/feed-timeseries?days=7&feed_ids={uuid.uuid4()}",
            headers=auth_headers["viewer"],
        )

    assert overview.status_code == 200
    overview_payload = overview.json()
    assert overview_payload["totals"]["feeds_total"] == 1
    assert overview_payload["totals"]["items_total"] == 1
    assert overview_payload["totals"]["articles_total"] == 1
    assert overview_payload["status_breakdown"] == [
        {"status": "content_fetched", "count": 1}
    ]
    assert sum(point["count"] for point in overview_payload["daily_volume"]) == 1
    assert [row["feed_id"] for row in overview_payload["feed_breakdown"]] == [
        str(visible_feed.id)
    ]
    assert overview_payload["top_domains"] == [
        {"domain": "visible-aggregate.example", "count": 1}
    ]

    assert timeseries.status_code == 200
    assert [row["feed_id"] for row in timeseries.json()["series"]] == [
        str(visible_feed.id)
    ]
    assert (
        sum(point["count"] for point in timeseries.json()["series"][0]["points"]) == 1
    )

    assert heatmap.status_code == 200
    assert sum(sum(row["counts"]) for row in heatmap.json()["rows"]) == 1
    assert radar.status_code == 200
    assert radar.json()["total"] == 1
    assert (
        next(
            axis["count"]
            for axis in radar.json()["axes"]
            if axis["category"] == "vulnerability"
        )
        == 1
    )
    assert restricted_series.status_code == unknown_series.status_code == 200
    assert restricted_series.json()["series"] == unknown_series.json()["series"] == []


@pytest.mark.parametrize("mode", ["disabled", "audit"])
def test_non_enforced_stats_preserve_existing_aggregate_results(
    mode: DataPolicyMode,
    client: TestClient,
    auth_headers,
    db_session: Session,
):
    _seed_stats_data(db_session)

    with _override_data_access(mode):
        response = client.get("/stats/overview?days=7", headers=auth_headers["viewer"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["feeds_total"] == 2
    assert payload["totals"]["items_total"] == 4
    assert payload["totals"]["articles_total"] == 4
    assert len(payload["feed_breakdown"]) == 2


def test_tags_and_preview_only_include_accessible_item_data(
    client: TestClient,
    auth_headers,
    db_session: Session,
):
    visible_feed, restricted_feed = _seed_stats_data(db_session)
    visible_item = db_session.scalar(
        select(Item).where(Item.feed_id == visible_feed.id)
    )
    restricted_item = db_session.scalar(
        select(Item).where(Item.feed_id == restricted_feed.id)
    )
    assert visible_item is not None
    assert restricted_item is not None
    visible_tag = Tag(name="policy-visible")
    restricted_tag = Tag(name="policy-restricted")
    shared_tag = Tag(name="policy-shared")
    db_session.add_all([visible_tag, restricted_tag, shared_tag])
    db_session.flush()
    db_session.add_all(
        [
            ItemTag(item_id=visible_item.id, tag_id=visible_tag.id, source="manual"),
            ItemTag(item_id=visible_item.id, tag_id=shared_tag.id, source="manual"),
            ItemTag(
                item_id=restricted_item.id,
                tag_id=restricted_tag.id,
                source="manual",
            ),
            ItemTag(item_id=restricted_item.id, tag_id=shared_tag.id, source="manual"),
        ]
    )
    db_session.commit()
    preview_payload = {
        "name": "Policy preview",
        "tag_name": "policy-preview",
        "enabled": True,
        "match_type": "contains",
        "pattern": "policy hit",
        "case_sensitive": False,
        "applies_to": ["title"],
        "required_categories": [],
        "feed_scope": "all",
        "feed_ids": [],
        "limit": 25,
    }

    with _override_data_access("enforced"):
        tags_response = client.get("/tags", headers=auth_headers["viewer"])
        preview_response = client.post(
            "/tagging/rules/preview",
            json=preview_payload,
            headers=auth_headers["admin"],
        )

    assert tags_response.status_code == 200
    assert [tag["name"] for tag in tags_response.json()] == [
        "policy-shared",
        "policy-visible",
    ]
    assert preview_response.status_code == 200
    assert preview_response.json()["total"] == 1
    assert [item["id"] for item in preview_response.json()["items"]] == [
        str(visible_item.id)
    ]
    assert preview_response.json()["items"][0]["current_tags"] == [
        "policy-shared",
        "policy-visible",
    ]

    with _override_data_access("audit"):
        audit_tags = client.get("/tags", headers=auth_headers["viewer"])
        audit_preview = client.post(
            "/tagging/rules/preview",
            json=preview_payload,
            headers=auth_headers["admin"],
        )

    assert {tag["name"] for tag in audit_tags.json()} == {
        "policy-restricted",
        "policy-shared",
        "policy-visible",
    }
    assert audit_preview.json()["total"] == 4


def test_tagging_feed_validation_does_not_distinguish_unknown_from_inaccessible(
    client: TestClient,
    auth_headers,
    db_session: Session,
):
    visible_feed, restricted_feed = _seed_stats_data(db_session)

    def payload(feed_id: uuid.UUID, suffix: str) -> dict[str, object]:
        return {
            "name": f"Policy selected {suffix}",
            "tag_name": f"policy-{suffix}",
            "enabled": True,
            "match_type": "contains",
            "pattern": "policy",
            "case_sensitive": False,
            "applies_to": ["title"],
            "required_categories": [],
            "feed_scope": "selected",
            "feed_ids": [str(feed_id)],
        }

    with _override_data_access("enforced"):
        restricted = client.post(
            "/tagging/rules",
            json=payload(restricted_feed.id, "restricted"),
            headers=auth_headers["admin"],
        )
        unknown = client.post(
            "/tagging/rules",
            json=payload(uuid.uuid4(), "unknown"),
            headers=auth_headers["admin"],
        )
        visible = client.post(
            "/tagging/rules",
            json=payload(visible_feed.id, "visible"),
            headers=auth_headers["admin"],
        )

    assert restricted.status_code == unknown.status_code == 422
    for response in (restricted, unknown):
        body = response.json()
        assert body["detail"] == (
            "One or more selected feed ids are unknown or inaccessible"
        )
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["message"] == body["detail"]
        assert body["error"]["retryable"] is False
    assert visible.status_code == 201

    with _override_data_access("audit"):
        audit = client.post(
            "/tagging/rules",
            json=payload(restricted_feed.id, "audit-restricted"),
            headers=auth_headers["admin"],
        )

    assert audit.status_code == 201


def test_stored_tagging_rules_hide_or_sanitize_revoked_feed_references(
    client: TestClient,
    auth_headers,
    db_session: Session,
):
    visible_feed, restricted_feed = _seed_stats_data(db_session)
    restricted_rule = TaggingRule(
        name="Restricted stored rule",
        tag_name="restricted-stored",
        enabled=True,
        match_type="contains",
        pattern="restricted",
        applies_to_json=["title"],
        required_categories_json=[],
        feed_scope="selected",
        feed_ids_json=[str(restricted_feed.id)],
    )
    mixed_rule = TaggingRule(
        name="Mixed stored rule",
        tag_name="mixed-stored",
        enabled=True,
        match_type="contains",
        pattern="policy",
        applies_to_json=["title"],
        required_categories_json=[],
        feed_scope="selected",
        feed_ids_json=[str(visible_feed.id), str(restricted_feed.id)],
    )
    db_session.add_all([restricted_rule, mixed_rule])
    db_session.commit()

    with _override_data_access("enforced"):
        bundle = client.get("/tagging/settings", headers=auth_headers["admin"])
        hidden_update = client.patch(
            f"/tagging/rules/{restricted_rule.id}",
            headers=auth_headers["admin"],
            json={
                "name": "Hidden update",
                "tag_name": "hidden-update",
                "enabled": True,
                "match_type": "contains",
                "pattern": "hidden",
                "case_sensitive": False,
                "applies_to": ["title"],
                "required_categories": [],
                "feed_scope": "selected",
                "feed_ids": [str(visible_feed.id)],
            },
        )
        mixed_delete = client.delete(
            f"/tagging/rules/{mixed_rule.id}",
            headers=auth_headers["admin"],
        )

    assert bundle.status_code == 200, bundle.text
    rules_by_name = {rule["name"]: rule for rule in bundle.json()["rules"]}
    assert "Restricted stored rule" not in rules_by_name
    assert rules_by_name["Mixed stored rule"]["feed_ids"] == [str(visible_feed.id)]
    assert hidden_update.status_code == 404
    assert mixed_delete.status_code == 404
    assert db_session.get(TaggingRule, restricted_rule.id) is not None
    assert db_session.get(TaggingRule, mixed_rule.id) is not None
