"""Current export health stays independent of retained job history on PostgreSQL."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import event

from app.core.config import Settings
from app.models.export_job import ExportJob
from app.services.operations_freshness import load_export_backlog


NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)


def _job(db, **overrides):
    values = {
        "principal_type": "user", "principal_id": uuid.uuid4(),
        "idempotency_key": uuid.uuid4(), "request_hash": "a" * 64,
        "request_encrypted": {}, "authorization_encrypted": {}, "format": "json",
        "status": "failed", "error_code": "generation_failed", "reserved_bytes": 0,
        "created_at": NOW - timedelta(hours=2), "completed_at": NOW,
        "expires_at": NOW + timedelta(days=1),
    }
    values.update(overrides)
    job = ExportJob(**values)
    db.add(job)
    db.flush()
    return job


def _backlog(db, *, now=NOW):
    return load_export_backlog(db, settings=Settings(_env_file=None), now=now)


@pytest.mark.parametrize("reason", [
    "authorization_changed", "size_limit", "empty_export", "snapshot_changed", "owner_deleted",
])
def test_expected_export_failure_and_successful_replacement_are_healthy(db_session, reason):
    failed = _job(db_session, error_code=reason)
    _job(
        db_session, principal_id=failed.principal_id, request_hash=failed.request_hash,
        status="ready", error_code=None, created_at=NOW, completed_at=NOW + timedelta(seconds=1),
    )
    result = _backlog(db_session, now=NOW + timedelta(seconds=1))
    assert result.failed_count == 1
    assert result.pending_count == result.active_count == result.stale_count == 0
    assert result.oldest_pending_age_seconds is None
    assert result.status == "healthy"


@pytest.mark.parametrize("reason", [
    "generation_failed", "generation_timeout", "worker_interrupted",
    "artifact_unavailable", "coordination_unavailable", "future_failure", None,
])
def test_recent_technical_export_failure_ages_out_without_losing_history(db_session, reason):
    failed = _job(db_session, error_code=reason)
    _job(db_session, principal_id=failed.principal_id, status="ready", error_code=None)
    recent = _backlog(db_session)
    assert recent.status == "degraded" and recent.failed_count == 1
    assert recent.pending_count == recent.active_count == 0

    boundary = NOW + timedelta(minutes=15)
    assert _backlog(db_session, now=boundary).status == "degraded"
    recovered = _backlog(db_session, now=boundary + timedelta(microseconds=1))
    assert recovered.status == "healthy" and recovered.failed_count == 1


def test_export_failure_without_completion_time_uses_creation_time(db_session):
    _job(db_session, created_at=NOW, completed_at=None)
    assert _backlog(db_session).status == "degraded"
    recovered = _backlog(db_session, now=NOW + timedelta(minutes=16))
    assert recovered.status == "healthy" and recovered.failed_count == 1


def test_export_queued_age_and_stale_leases_are_independent_of_failure_history(db_session):
    _job(db_session, error_code="size_limit")
    _job(db_session, completed_at=NOW - timedelta(minutes=16))
    queued = _job(
        db_session, status="queued", created_at=NOW - timedelta(seconds=599),
        completed_at=None, error_code=None,
    )
    assert _backlog(db_session).status == "healthy"
    boundary = _backlog(db_session, now=NOW + timedelta(seconds=1))
    assert boundary.status == "degraded"
    assert boundary.pending_count == 1 and boundary.failed_count == 2
    assert boundary.oldest_pending_age_seconds == 600

    queued.status = "running"
    queued.lease_expires_at = NOW
    db_session.flush()
    assert _backlog(db_session).status == "healthy"
    stale = _backlog(db_session, now=NOW + timedelta(seconds=1))
    assert stale.status == "critical"
    assert stale.pending_count == 0 and stale.active_count == stale.stale_count == 1
    assert stale.failed_count == 2 and stale.oldest_pending_age_seconds is None


def test_export_health_uses_one_aggregate_without_loading_encrypted_content(db_session):
    _job(db_session)
    statements = []
    connection = db_session.connection()

    def listener(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", listener)
    try:
        result = _backlog(db_session)
    finally:
        event.remove(connection, "before_cursor_execute", listener)
    assert result.failed_count == 1
    assert len(statements) == 1
    assert "count(" in statements[0]
    assert "encrypted" not in statements[0]
