import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import feeds as feeds_routes
from app.models.audit_log import AuditLog
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.user import User
from app.schemas.feed import FeedImportRequest
from app.services.data_access_policy import DataAccessContext
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


@pytest.mark.parametrize("overwrite_existing", [False, True])
def test_import_uses_stable_feed_lock_order(overwrite_existing: bool):
    payload = FeedImportRequest.model_validate(
        {
            "overwrite_existing": overwrite_existing,
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


def test_opposite_feed_imports_complete_without_database_deadlock(
    database_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    run_id = uuid.uuid4().hex
    first_url = f"https://example.com/{run_id}-first.xml"
    second_url = f"https://example.com/{run_id}-second.xml"
    user_id = uuid.uuid4()
    session_factory = sessionmaker(
        bind=database_engine,
        autoflush=False,
        autocommit=False,
        class_=Session,
    )
    with session_factory.begin() as db:
        db.add(
            User(
                id=user_id,
                email=f"feed-import-race-{run_id}@example.com",
                password_hash="not-a-login-secret",
                role="analyst",
                is_active=True,
                is_approved=True,
            )
        )

    first_payload = FeedImportRequest.model_validate(
        {
            "feeds": [
                {"name": "First", "url": first_url},
                {"name": "Second", "url": second_url},
            ]
        }
    )
    second_payload = FeedImportRequest.model_validate(
        {
            "feeds": [
                {"name": "Second", "url": second_url},
                {"name": "First", "url": first_url},
            ]
        }
    )
    actor = SimpleNamespace(id=user_id)
    data_access = DataAccessContext(
        mode="disabled",
        policy_revision=1,
        coverage_version=0,
        principal_type="user",
        principal_id=user_id,
        principal_eligible=True,
        allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
    )
    first_insert_barrier = Barrier(2)
    thread_state = local()
    create_feed_record = feeds_routes._create_feed_record

    def _synchronized_create(db: Session, **feed_values):
        call_count = getattr(thread_state, "create_call_count", 0)
        thread_state.create_call_count = call_count + 1
        if call_count == 0:
            first_insert_barrier.wait(timeout=5)
        return create_feed_record(db, **feed_values)

    monkeypatch.setattr(feeds_routes, "_create_feed_record", _synchronized_create)
    monkeypatch.setattr(
        feeds_routes,
        "_enqueue_metadata_backfills",
        lambda *_args, **_kwargs: 0,
    )

    def _run_import(payload: FeedImportRequest):
        with session_factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            return feeds_routes.import_feeds(payload, db, actor, data_access)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(_run_import, first_payload)
            second_future = executor.submit(_run_import, second_payload)
            results = [first_future.result(timeout=15), second_future.result(timeout=15)]

        assert sum(result.created for result in results) == 2
        assert sum(result.skipped for result in results) == 2
        assert all(not result.errors for result in results)
    finally:
        digests = [feed_url_digest(first_url), feed_url_digest(second_url)]
        with session_factory.begin() as db:
            db.execute(delete(Feed).where(Feed.url_digest.in_(digests)))
            db.execute(delete(AuditLog).where(AuditLog.actor_user_id == user_id))
            db.execute(delete(User).where(User.id == user_id))
