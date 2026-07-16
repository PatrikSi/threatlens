import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.routes import alerts as alerts_routes
from app.models.feed import Feed
from app.models.item import Item


def test_alert_matches_enforces_aggregate_keyword_cap(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        alerts_routes,
        "get_settings",
        lambda: SimpleNamespace(alert_matches_keyword_cap=2),
    )

    feed = Feed(
        id=uuid.uuid4(),
        name="Alert keyword cap feed",
        url="https://example.com/alert-keyword-cap.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="alert-keyword-cap-item",
        url="https://example.com/alert-keyword-cap-item",
        canonical_url="https://example.com/alert-keyword-cap-item",
        title="Boundary-signal activity detected",
        summary="Alert cap boundary coverage",
        published_at=datetime.now(timezone.utc),
        dedupe_key="test:alert-keyword-cap-item",
        content_hash="d" * 64,
        status="content_fetched",
    )
    db_session.add_all([feed, item])
    db_session.commit()

    first_response = client.post(
        "/alerts",
        json={"name": "First cap alert", "category": "cap_test", "keywords": ["first-signal"]},
        headers=auth_headers["viewer"],
    )
    boundary_response = client.post(
        "/alerts",
        json={"name": "Boundary cap alert", "category": "cap_test", "keywords": ["boundary-signal"]},
        headers=auth_headers["viewer"],
    )
    assert first_response.status_code == 201
    assert boundary_response.status_code == 201

    at_cap_response = client.get("/alerts/matches", headers=auth_headers["viewer"])
    assert at_cap_response.status_code == 200
    at_cap_payload = at_cap_response.json()
    assert at_cap_payload["total"] == 1
    assert len(at_cap_payload["items"]) == 1
    assert at_cap_payload["items"][0]["matches"][0]["matched_keywords"] == ["boundary-signal"]

    overage_response = client.post(
        "/alerts",
        json={"name": "Overage cap alert", "category": "cap_test", "keywords": ["overage-signal"]},
        headers=auth_headers["viewer"],
    )
    assert overage_response.status_code == 201

    over_cap_response = client.get("/alerts/matches", headers=auth_headers["viewer"])
    assert over_cap_response.status_code == 422
    detail = over_cap_response.json()["detail"]
    assert "3 alerts with 3 distinct keywords" in detail
    assert "ALERT_MATCHES_KEYWORD_CAP=2" in detail
    assert "disable unneeded alerts" in detail
    assert "alert_ids or categories" in detail
    assert "increase ALERT_MATCHES_KEYWORD_CAP" in detail

    narrowed_response = client.get(
        "/alerts/matches",
        params={"alert_ids": f"{first_response.json()['id']},{boundary_response.json()['id']}"},
        headers=auth_headers["viewer"],
    )
    assert narrowed_response.status_code == 200
    assert narrowed_response.json()["total"] == 1
