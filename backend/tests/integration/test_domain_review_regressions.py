"""Regression checks for report concurrency and bounded analytics reads."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
import time
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import reports as report_routes
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.report import Report
from app.models.report_source_item import ReportSourceItem
from app.models.user import User
from app.services.data_access_policy import DataAccessContext


def _item(db, feed, *, published_at, suffix):
    identity = uuid.uuid4()
    item = Item(
        id=identity,
        feed_id=feed.id,
        source_guid=str(identity),
        url=f"https://{suffix}.example/{identity}",
        canonical_url=f"https://{suffix}.example/{identity}",
        url_domain=f"{suffix}.example",
        title=suffix,
        summary="summary",
        published_at=published_at,
        first_seen_at=published_at,
        dedupe_key=str(identity),
        content_hash="1" * 64,
        status="content_fetched",
    )
    db.add(item)
    db.flush()
    db.add(
        ItemClassification(
            item_id=identity,
            primary_category="vulnerability",
            source_hash="1" * 64,
        )
    )
    return item


@pytest.mark.parametrize("days", [7, 30])
def test_statistics_exclude_future_publication_and_ingestion_times(
    client,
    db_session,
    auth_headers,
    days,
):
    now = datetime.now(timezone.utc)
    feed = Feed(name="Future publication", url=f"https://{uuid.uuid4()}.example/feed")
    db_session.add(feed)
    db_session.flush()
    _item(db_session, feed, published_at=now - timedelta(hours=1), suffix="past")
    _item(db_session, feed, published_at=now + timedelta(minutes=5), suffix="future")
    _item(db_session, feed, published_at=now + timedelta(days=1), suffix="future")
    db_session.commit()
    suffix = f"?days={days}&feed_ids={feed.id}"
    overview = client.get(f"/stats/overview{suffix}", headers=auth_headers["viewer"])
    assert overview.status_code == 200, overview.text
    value = overview.json()
    assert value["totals"]["items_total"] == 3  # All-time inventory remains complete.
    assert sum(row["count"] for row in value["daily_volume"]) == 1
    assert value["feed_breakdown"][0]["items_in_window"] == 1
    assert value["activity"]["items_last_24h"] == 1
    assert value["top_domains"] == [{"domain": "past.example", "count": 1}]
    timeseries = client.get(
        f"/stats/feed-timeseries{suffix}", headers=auth_headers["viewer"]
    )
    assert (
        sum(
            point["count"]
            for series in timeseries.json()["series"]
            for point in series["points"]
        )
        == 1
    )
    heatmap = client.get(
        f"/stats/activity-heatmap{suffix}", headers=auth_headers["viewer"]
    )
    assert sum(sum(row["counts"]) for row in heatmap.json()["rows"]) == 1
    radar = client.get(f"/stats/signal-radar{suffix}", headers=auth_headers["viewer"])
    assert radar.json()["total"] == 1


def test_statistics_feed_ranking_ignores_future_only_feeds(
    client, db_session, auth_headers
):
    now = datetime.now(timezone.utc)
    feeds = [
        Feed(name=name, url=f"https://{uuid.uuid4()}.example/feed")
        for name in ("Past", "Future")
    ]
    db_session.add_all(feeds)
    db_session.flush()
    _item(db_session, feeds[0], published_at=now - timedelta(hours=1), suffix="past")
    for _ in range(2):
        _item(
            db_session, feeds[1], published_at=now + timedelta(days=1), suffix="future"
        )
    db_session.commit()
    response = client.get(
        "/stats/feed-timeseries?days=7&top_feeds=1", headers=auth_headers["viewer"]
    )
    assert response.status_code == 200
    assert [series["feed_id"] for series in response.json()["series"]] == [
        str(feeds[0].id)
    ]


def test_statistics_timeseries_discloses_bounded_default_scope(
    client, db_session, auth_headers
):
    now = datetime.now(timezone.utc)
    for index in range(101):
        feed = Feed(
            name=f"Bounded feed {index}", url=f"https://{uuid.uuid4()}.example/feed"
        )
        db_session.add(feed)
        db_session.flush()
        _item(db_session, feed, published_at=now - timedelta(hours=1), suffix="past")
    db_session.commit()
    response = client.get(
        "/stats/feed-timeseries?days=7", headers=auth_headers["viewer"]
    )
    assert response.status_code == 200
    value = response.json()
    assert len(value["series"]) == value["feed_limit"] == 100
    assert value["total_feeds"] == 101
    assert value["truncated"] is True
    selection = ",".join(str(uuid.uuid4()) for _ in range(501))
    oversized = client.get(
        f"/stats/feed-timeseries?feed_ids={selection}", headers=auth_headers["viewer"]
    )
    assert oversized.status_code == 422
    assert "500" in oversized.json()["detail"]


def test_report_delete_rechecks_status_after_waiting_for_retry(database_engine):
    factory = sessionmaker(bind=database_engine, class_=Session, expire_on_commit=False)
    user_id, report_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    with factory.begin() as db:
        db.add(
            User(
                id=user_id,
                email=f"delete-retry-{user_id}@example.com",
                password_hash="unused",
                role="analyst",
                is_active=True,
                is_approved=True,
            )
        )
        db.flush()
        db.add(
            Report(
                id=report_id,
                owner_user_id=user_id,
                title="Retry race",
                status="error",
                period_start=now - timedelta(days=1),
                period_end=now,
            )
        )
    ready = Event()
    delete_pid = []
    access = DataAccessContext(
        mode="disabled",
        policy_revision=1,
        coverage_version=1,
        principal_type="user",
        principal_id=user_id,
        principal_eligible=True,
        allowed_label_ids=frozenset(),
    )

    def remove():
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            delete_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            try:
                report_routes.remove_report(
                    report_id, db, SimpleNamespace(id=user_id, role="analyst"), access
                )
            except HTTPException as exc:
                return exc.status_code
            return 204

    try:
        with factory() as retry_db, ThreadPoolExecutor(max_workers=1) as executor:
            report = retry_db.scalar(
                select(Report).where(Report.id == report_id).with_for_update()
            )
            report.status = "queued"
            retry_db.flush()
            future = executor.submit(remove)
            assert ready.wait(timeout=5)
            deadline = time.monotonic() + 5
            with factory() as observer:
                while time.monotonic() < deadline:
                    wait_type = observer.scalar(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": delete_pid[0]},
                    )
                    observer.rollback()  # Refresh the statistics snapshot.
                    if wait_type == "Lock":
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("Delete never reached the locked report")
            retry_db.commit()
            assert future.result(timeout=10) == 409
        with factory() as db:
            assert db.get(Report, report_id).status == "queued"
    finally:
        with factory.begin() as db:
            db.execute(delete(Report).where(Report.id == report_id))
            db.execute(delete(User).where(User.id == user_id))


def test_report_reads_omit_unused_large_evidence_and_generation_payloads(
    client, db_session, seed_users, auth_headers
):
    now = datetime.now(timezone.utc)
    report = Report(
        owner_user_id=seed_users["analyst"].id,
        title="Projection",
        status="ready",
        period_start=now - timedelta(days=1),
        period_end=now,
        generation_context_json={"large": "x" * 1_000_000},
        error="e" * 10_000,
    )
    db_session.add(report)
    db_session.flush()
    db_session.add(
        ReportSourceItem(
            report_id=report.id,
            citation_key="S1",
            title_snapshot="Source",
            feed_name_snapshot="Feed",
            url_snapshot="https://example.com/article",
            first_seen_at_snapshot=now,
            evidence_text="x" * 1_000_000,
        )
    )
    db_session.commit()
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    connection = db_session.connection()
    event.listen(connection, "before_cursor_execute", capture)
    try:
        listing = client.get("/reports", headers=auth_headers["viewer"])
        assert listing.status_code == 200, listing.text
        assert len(listing.json()[0]["error"]) == 4000
        report_queries = [sql for sql in statements if "FROM reports" in sql]
        assert report_queries
        assert all(
            "reports.generation_context_json" not in sql for sql in report_queries
        )
        statements.clear()
        detail = client.get(f"/reports/{report.id}", headers=auth_headers["viewer"])
        assert detail.status_code == 200, detail.text
        assert detail.json()["sources"][0]["title"] == "Source"
        source_queries = [
            sql for sql in statements if "FROM report_source_items" in sql
        ]
        assert source_queries
        assert all(
            "report_source_items.evidence_text" not in sql for sql in source_queries
        )
    finally:
        event.remove(connection, "before_cursor_execute", capture)
