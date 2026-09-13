from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.models.ai_usage_event import AIUsageEvent
from app.models.ai_task_run import AITaskRun
from app.services.ai_ops_metrics import _build_coverage_stats, _build_endpoint_health
from app.services.data_access_policy import DataAccessContext


@pytest.mark.parametrize(
    ("latencies", "expected"),
    [([], 0.0), ([None], 0.0), ([None, 20, 40], 30.0), ([0, None], 0.0)],
)
def test_endpoint_health_handles_missing_success_latency(
    db_session, latencies, expected
):
    now = datetime.now(timezone.utc)
    observed_at = now - timedelta(microseconds=1)
    events = [
        AIUsageEvent(
            feature_type="summary", success=True, latency_ms=value, created_at=observed_at
        )
        for value in latencies
    ]
    events.append(
        AIUsageEvent(
            feature_type="summary", success=False, latency_ms=999, created_at=observed_at
        )
    )
    # An event at the exclusive upper bound cannot change latency or freshness.
    events.append(
        AIUsageEvent(
            feature_type="summary", success=True, latency_ms=12345, created_at=now
        )
    )

    db_session.add_all(events)
    db_session.flush()
    health = _build_endpoint_health(db_session, since=now - timedelta(days=1), now=now)

    assert health.median_latency_ms == expected
    assert health.last_success_at == (observed_at if latencies else None)


@pytest.mark.parametrize("completed_status", ["ready", "error", "cancelled"])
def test_coverage_latest_finished_run_ignores_queued_and_running_work(
    db_session, completed_status
):
    now = datetime.now(timezone.utc)
    db_session.add_all([
        AITaskRun(
            task_type="connection_test",
            trigger_source="manual",
            status=status,
            finished_at=finished_at,
        )
        for status, finished_at in [
            ("ready", now - timedelta(minutes=2)),
            (completed_status, now - timedelta(minutes=1)),
            ("queued", None),
            ("running", None),
        ]
    ])
    db_session.flush()

    assert _build_coverage_stats(db_session).last_ai_run_at == now - timedelta(minutes=1)


@pytest.mark.parametrize("statuses", [[], ["queued", "running"]])
def test_coverage_without_finished_work_has_no_last_run_timestamp(db_session, statuses):
    db_session.add_all([
        AITaskRun(task_type="connection_test", trigger_source="manual", status=status)
        for status in statuses
    ])
    db_session.flush()

    assert _build_coverage_stats(db_session).last_ai_run_at is None


def test_coverage_latest_finished_run_respects_current_data_access(db_session):
    now = datetime.now(timezone.utc)
    db_session.add_all([
        AITaskRun(
            task_type="connection_test",
            trigger_source="manual",
            status="ready",
            finished_at=now - timedelta(minutes=1),
            data_access_scope="system",
            data_access_lineage_complete=True,
        ),
        # This newer governed result has no accessible lineage.
        AITaskRun(task_type="report", trigger_source="manual", status="ready", finished_at=now),
    ])
    db_session.flush()
    context = DataAccessContext(
        mode="enforced",
        policy_revision=1,
        coverage_version=1,
        principal_type="user",
        principal_id=uuid.uuid4(),
        principal_eligible=True,
        allowed_label_ids=frozenset(),
    )

    coverage = _build_coverage_stats(db_session, data_access=context)

    assert coverage.last_ai_run_at == now - timedelta(minutes=1)
