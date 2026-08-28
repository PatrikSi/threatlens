import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.routes import alerts as alerts_routes
from app.models.alert_interest import AlertInterest
from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.schemas.alert import ALERT_KEYWORD_MAX_LENGTH
from app.services.alert_rules import (
    ALERT_RULES_PER_USER_LIMIT,
    AlertRuleQuotaExceededError,
    lock_alert_rule_creation_slot,
)


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
        json={
            "name": "First cap alert",
            "category": "cap_test",
            "keywords": ["first-signal"],
        },
        headers=auth_headers["viewer"],
    )
    boundary_response = client.post(
        "/alerts",
        json={
            "name": "Boundary cap alert",
            "category": "cap_test",
            "keywords": ["boundary-signal"],
        },
        headers=auth_headers["viewer"],
    )
    assert first_response.status_code == 201
    assert boundary_response.status_code == 201

    at_cap_response = client.get("/alerts/matches", headers=auth_headers["viewer"])
    assert at_cap_response.status_code == 200
    at_cap_payload = at_cap_response.json()
    assert at_cap_payload["total"] == 1
    assert len(at_cap_payload["items"]) == 1
    assert at_cap_payload["items"][0]["matches"][0]["matched_keywords"] == [
        "boundary-signal"
    ]

    overage_response = client.post(
        "/alerts",
        json={
            "name": "Overage cap alert",
            "category": "cap_test",
            "keywords": ["overage-signal"],
        },
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
        params={
            "alert_ids": f"{first_response.json()['id']},{boundary_response.json()['id']}"
        },
        headers=auth_headers["viewer"],
    )
    assert narrowed_response.status_code == 200
    assert narrowed_response.json()["total"] == 1


def test_alert_rule_keywords_are_bounded_for_create_preview_and_update(
    client: TestClient,
    auth_headers,
):
    oversized_keyword = "x" * (ALERT_KEYWORD_MAX_LENGTH + 1)
    create = client.post(
        "/alerts",
        json={
            "name": "Oversized create",
            "category": "limits",
            "keywords": [oversized_keyword],
        },
        headers=auth_headers["viewer"],
    )
    preview = client.post(
        "/alerts/preview",
        json={"category": "limits", "keywords": [oversized_keyword]},
        headers=auth_headers["viewer"],
    )
    valid = client.post(
        "/alerts",
        json={
            "name": "Valid bounded rule",
            "category": "limits",
            "keywords": ["bounded"],
        },
        headers=auth_headers["viewer"],
    )
    assert valid.status_code == 201, valid.text
    update = client.patch(
        f"/alerts/{valid.json()['id']}",
        json={"keywords": [oversized_keyword]},
        headers=auth_headers["viewer"],
    )

    for response in (create, preview, update):
        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "string_too_long"


def test_alert_rule_create_rejects_the_per_user_quota(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    owner = seed_users["viewer"]
    db_session.add_all(
        [
            AlertInterest(
                user_id=owner.id,
                name=f"Quota rule {index}",
                category="quota",
                keywords=[f"quota-{index}"],
                enabled=index % 2 == 0,
            )
            for index in range(ALERT_RULES_PER_USER_LIMIT)
        ]
    )
    db_session.commit()

    response = client.post(
        "/alerts",
        json={
            "name": "One rule too many",
            "category": "quota",
            "keywords": ["over-quota"],
        },
        headers=auth_headers["viewer"],
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "alert_rule_quota_exceeded"
    assert response.headers["X-Error-Code"] == "alert_rule_quota_exceeded"
    assert str(ALERT_RULES_PER_USER_LIMIT) in response.json()["detail"]


def test_alert_rule_quota_serializes_competing_creates(database_engine):
    owner_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        setup_db.add(
            User(
                id=owner_id,
                email=f"alert-quota-{uuid.uuid4().hex}@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        setup_db.flush()
        setup_db.add(
            AlertInterest(
                user_id=owner_id,
                name="Existing quota rule",
                category="quota",
                keywords=["existing"],
            )
        )
        setup_db.commit()

    first_db = Session(database_engine)
    contender_started = Event()

    def _competing_create() -> str:
        with Session(database_engine) as contender_db:
            contender_started.set()
            try:
                lock_alert_rule_creation_slot(
                    contender_db, owner_user_id=owner_id, limit=2
                )
            except AlertRuleQuotaExceededError as exc:
                contender_db.rollback()
                return exc.code
            contender_db.add(
                AlertInterest(
                    user_id=owner_id,
                    name="Unexpected competing rule",
                    category="quota",
                    keywords=["unexpected"],
                )
            )
            contender_db.commit()
            return "created"

    try:
        lock_alert_rule_creation_slot(first_db, owner_user_id=owner_id, limit=2)
        first_db.add(
            AlertInterest(
                user_id=owner_id,
                name="Winning quota rule",
                category="quota",
                keywords=["winner"],
            )
        )
        first_db.flush()

        with ThreadPoolExecutor(max_workers=1) as executor:
            contender = executor.submit(_competing_create)
            assert contender_started.wait(timeout=2)
            time.sleep(0.1)
            assert not contender.done()
            first_db.commit()
            assert contender.result(timeout=5) == "alert_rule_quota_exceeded"

        with Session(database_engine) as verify_db:
            count = verify_db.scalar(
                select(func.count(AlertInterest.id)).where(
                    AlertInterest.user_id == owner_id
                )
            )
            assert count == 2
    finally:
        first_db.rollback()
        first_db.close()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(delete(User).where(User.id == owner_id))
            cleanup_db.commit()
