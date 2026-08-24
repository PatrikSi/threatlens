import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.routes import feeds as feeds_routes
from app.models.feed import Feed
from app.schemas.feed import FeedImportRequest
from app.services.feed_storage import feed_url_digest


def test_feed_patch_advances_fetch_fence_once_for_material_changes(
    client: TestClient,
    auth_headers,
    db_session,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Patch-fenced feed",
        url=f"https://example.com/{uuid.uuid4()}.xml",
        enabled=True,
        fetch_mode="interval",
        fetch_interval_seconds=1800,
        fetch_fence=11,
    )
    db_session.add(feed)
    db_session.commit()

    response = client.patch(
        f"/feeds/{feed.id}",
        json={
            "url": f"https://example.net/{uuid.uuid4()}.xml",
            "enabled": False,
            "fetch_mode": "schedule",
            "fetch_interval_seconds": 3600,
            "schedule_cron": "0 * * * *",
        },
        headers=auth_headers["admin"],
    )

    assert response.status_code == 200
    db_session.expire_all()
    stored_feed = db_session.get(Feed, feed.id)
    assert stored_feed is not None
    assert stored_feed.fetch_fence == 12


def test_feed_import_overwrite_advances_fetch_fence_for_material_changes(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Import-fenced feed",
        url=f"https://example.com/{uuid.uuid4()}.xml",
        enabled=False,
        fetch_mode="schedule",
        fetch_interval_seconds=1800,
        schedule_cron="0 * * * *",
        fetch_fence=19,
    )
    db_session.add(feed)
    db_session.commit()
    monkeypatch.setattr(
        "app.api.routes.feeds._enqueue_metadata_backfills",
        lambda *_args, **_kwargs: 0,
    )

    response = client.post(
        "/feeds/import",
        json={
            "overwrite_existing": True,
            "feeds": [
                {
                    "name": "Updated import-fenced feed",
                    "url": feed.url,
                    "enabled": True,
                    "fetch_mode": "interval",
                    "fetch_interval_seconds": 900,
                }
            ],
        },
        headers=auth_headers["admin"],
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    db_session.expire_all()
    stored_feed = db_session.get(Feed, feed.id)
    assert stored_feed is not None
    assert stored_feed.fetch_fence == 20


def test_overwrite_import_uses_stable_feed_lock_order():
    payload = FeedImportRequest.model_validate(
        {
            "overwrite_existing": True,
            "feeds": [
                {"name": "Second", "url": "https://example.com/second.xml"},
                {"name": "First", "url": "https://example.com/first.xml"},
            ],
        }
    )

    entries = feeds_routes._ordered_import_entries(payload)

    digests = [feed_url_digest(feed_url) for _index, _entry, feed_url in entries]
    assert digests == sorted(digests)
    assert {index for index, _entry, _url in entries} == {1, 2}
