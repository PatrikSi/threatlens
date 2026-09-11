from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event

from app.core import runtime_metrics
from app.core.config import Settings
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.processing_work import ProcessingWork
from app.services.operations_freshness import load_processing_backlog
from app.services.operations_runtime import collect_memory_pressure, safe_runtime_metrics
from app.services.processing_queries import stage_statement


NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)


def _pending_item(db, *, seen, required):
    feed = Feed(name="Synthetic freshness feed")
    feed.url = f"https://freshness.invalid/{uuid.uuid4()}.rss"
    db.add(feed)
    db.flush()
    item = Item(
        feed_id=feed.id, url="https://freshness.invalid/item", title="Synthetic",
        first_seen_at=seen, classification_required_at=required,
        classification_required_version=2, classification_completed_version=1,
        tagging_pending=True, tagging_pending_since_at=required,
        dedupe_key=str(uuid.uuid4()), content_hash="a" * 64,
    )
    db.add(item)
    db.flush()
    return item


def test_refreshed_old_item_uses_current_obligation_age_and_database_aggregation(db_session):
    now = datetime.now(timezone.utc)
    _pending_item(db_session, seen=now - timedelta(days=400), required=now - timedelta(seconds=30))
    statements = []
    connection = db_session.connection()
    def listener(_conn, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(connection, "before_cursor_execute", listener)
    try:
        result = load_processing_backlog(db_session, stage="classification", settings=Settings(_env_file=None), now=now)
    finally:
        event.remove(connection, "before_cursor_execute", listener)
    assert result.pending_count == 1
    assert result.oldest_pending_age_seconds == 30
    assert result.status == "healthy"
    assert len(statements) == 2
    assert all("articles.text" not in statement and "items.summary" not in statement for statement in statements)


@pytest.mark.parametrize("stage", ["classification", "tagging"])
def test_freshness_objective_boundary_is_inclusive(db_session, stage):
    now = datetime.now(timezone.utc)
    _pending_item(db_session, seen=now, required=now - timedelta(seconds=600))
    settings = Settings(_env_file=None, classification_freshness_seconds=600, tagging_freshness_seconds=600)
    result = load_processing_backlog(db_session, stage=stage, settings=settings, now=now)
    assert result.status == "degraded"
    assert result.oldest_pending_age_seconds == 600


def test_memory_pressure_is_explicitly_container_scoped_and_bounded(tmp_path):
    (tmp_path / "memory.current").write_text("90")
    (tmp_path / "memory.max").write_text("100")
    (tmp_path / "memory.events").write_text("oom_kill 2\n")
    (tmp_path / "memory.pressure").write_text("some avg10=1.25 avg60=0 avg300=0 total=50\n")
    (tmp_path / "statm").write_text("100 10 0")
    result = collect_memory_pressure(cgroup=tmp_path, process=tmp_path / "statm")
    assert result["container_memory_percent"] == 90
    assert result["container_oom_kills"] == 2
    assert result["container_memory_pressure_avg10"] == 1.25
    (tmp_path / "memory.max").write_text("max")
    assert collect_memory_pressure(cgroup=tmp_path)["container_memory_percent"] is None


def test_runtime_metric_history_drops_untrusted_fields_and_invalid_numbers():
    assert safe_runtime_metrics({
        "database_connections": 2, "private_destination": "secret",
        "container_memory_percent": float("nan"), "container_oom_kills": True,
    }) == {"database_connections": 2}


def test_shared_deadline_metrics_have_fixed_labels_and_expire(test_redis_url, monkeypatch):
    if test_redis_url is None:
        pytest.skip("Disposable Redis unavailable")
    monkeypatch.setattr(runtime_metrics, "get_settings", lambda: SimpleNamespace(redis_url=test_redis_url))
    monkeypatch.setattr(runtime_metrics, "_PREFIX", f"test:metrics:{uuid.uuid4()}:")
    runtime_metrics.record_runtime_event("database_deadline")
    result = runtime_metrics.collect_runtime_events()
    assert result["database_deadline_last_15m"] == 1
    client = runtime_metrics._client(test_redis_url)
    keys = list(client.scan_iter(match=f"{runtime_metrics._PREFIX}*"))
    assert len(keys) == 1
    assert 0 < client.ttl(keys[0]) <= 3600
    assert client.hlen(keys[0]) == 1
    with pytest.raises(ValueError):
        runtime_metrics.record_runtime_event("user:secret")


def test_removed_classification_remains_visible_even_when_versions_match(db_session):
    now = datetime.now(timezone.utc)
    item = _pending_item(db_session, seen=now, required=now - timedelta(seconds=601))
    item.classification_completed_version = item.classification_required_version
    db_session.flush()
    result = load_processing_backlog(db_session, stage="classification", settings=Settings(_env_file=None), now=now)
    assert result.pending_count == 1
    assert result.status == "degraded"


@pytest.mark.parametrize("stage", ["classification", "tagging"])
def test_exhausted_cancelled_processing_work_matches_current_worklist(db_session, monkeypatch, stage):
    monkeypatch.setenv("PROCESSING_MAX_ATTEMPTS", "3")
    settings = Settings(_env_file=None, processing_max_attempts=3)
    item = _pending_item(db_session, seen=NOW, required=NOW)
    work = ProcessingWork(
        item_id=item.id, feed_id=item.feed_id, stage=stage,
        source_version=item.classification_required_version,
        status="cancelled", attempts=3, reason="cancelled",
    )
    db_session.add(work)
    db_session.flush()
    row = db_session.execute(stage_statement(stage)).mappings().one()
    backlog = load_processing_backlog(db_session, stage=stage, settings=settings, now=NOW)
    assert row["state"] == "attention" and row["reason"] == "retry_exhausted"
    assert backlog.pending_count == backlog.failed_count == 1
    assert backlog.status == "degraded"

    # A source refresh invalidates exhausted work from the previous obligation.
    item.classification_required_version += 1
    db_session.flush()
    row = db_session.execute(stage_statement(stage)).mappings().one()
    backlog = load_processing_backlog(db_session, stage=stage, settings=settings, now=NOW)
    assert row["reason"] is None
    assert backlog.pending_count == 1 and backlog.failed_count == 0
    assert backlog.status == "healthy"

    # Cancelling with automatic attempts remaining is not exhaustion.
    work.source_version = item.classification_required_version
    work.attempts = 2
    db_session.flush()
    backlog = load_processing_backlog(db_session, stage=stage, settings=settings, now=NOW)
    assert backlog.failed_count == 0 and backlog.status == "healthy"

    work.status = "attention"
    db_session.flush()
    backlog = load_processing_backlog(db_session, stage=stage, settings=settings, now=NOW)
    assert backlog.failed_count == 1 and backlog.status == "degraded"

    # Domain completion removes stale dispatch failures from the backlog.
    item.classification_completed_version = item.classification_required_version
    item.tagging_pending = False
    db_session.add(ItemClassification(
        item_id=item.id, primary_category="uncategorized", source_hash="a" * 64,
    ))
    db_session.flush()
    backlog = load_processing_backlog(db_session, stage=stage, settings=settings, now=NOW)
    assert backlog.pending_count == backlog.failed_count == 0
    assert backlog.status == "healthy"
