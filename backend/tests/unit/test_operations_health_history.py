from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.system_health_sample import SystemHealthSample
from app.schemas.operations import (
    OperationsApplicationInfo,
    OperationsBacklogSnapshot,
    OperationsComponentCheck,
    OperationsIssue,
    OperationsOverviewResponse,
    OperationsRecoverySnapshot,
    OperationsStorageIndicator,
    OperationsWorkerTopologyResponse,
)
from app.services.operations_health_history import (
    HEALTH_HISTORY_MAX_POINTS,
    collect_health_history,
    record_system_health_sample,
)


def _overview(now: datetime) -> OperationsOverviewResponse:
    return OperationsOverviewResponse(
        generated_at=now,
        overall_status="degraded",
        application=OperationsApplicationInfo(
            version="1.0.0",
            schema_revision="0083_system_health_history",
            expected_schema_revision="0083_system_health_history",
            schema_current=True,
        ),
        components=[
            OperationsComponentCheck(
                key="database",
                label="Database",
                status="healthy",
                summary="Healthy",
                checked_at=now,
            ),
            OperationsComponentCheck(
                key="not_allowlisted",
                label="Unexpected",
                status="critical",
                summary="Must not persist",
                checked_at=now,
            ),
        ],
        storage=[
            OperationsStorageIndicator(
                key="application_filesystem",
                label="Filesystem",
                status="degraded",
                percent_used=90,
            )
        ],
        backlogs=[
            OperationsBacklogSnapshot(
                key="reports",
                label="Reports",
                status="degraded",
                pending_count=4,
                active_count=1,
                stale_count=2,
                failed_count=1,
                degraded_after_seconds=300,
            )
        ],
        recovery=OperationsRecoverySnapshot(),
        issues=[
            OperationsIssue(
                code="Reports/Stale secret detail",
                severity="warning",
                component="reports",
                summary="Reports are stale",
                effect="Reports may be delayed",
                recommended_action="Inspect report workers",
            )
        ],
    )


def _topology(now: datetime, *, status="degraded", reason="saturated"):
    return OperationsWorkerTopologyResponse(
        generated_at=now,
        status=status,
        reason=reason,
        timeout_seconds=1.0,
        responding_worker_count=2,
        observed_worker_count=2,
        worker_inventory_truncated=False,
        total_capacity=4,
        active_count=4,
        reserved_count=1,
        scheduled_count=0,
        missing_queues=[],
        stale_execution_queues=["processing"],
        missing_execution_evidence_queues=[],
        canary_dispatch_ok=True,
        canary_dispatch_reason="healthy",
        canary_dispatch_heartbeat_at=now,
        canary_dispatch_age_seconds=1,
        probes=[],
        workers=[],
        queues=[],
    )


def _row(sampled_at: datetime, **overrides) -> SystemHealthSample:
    values = {
        "id": uuid.uuid4(),
        "sampled_at": sampled_at,
        "overall_status": "healthy",
        "component_statuses_json": {"component:database": "healthy"},
        "worker_status": "healthy",
        "worker_reason": "healthy",
        "responding_worker_count": 1,
        "observed_worker_count": 1,
        "worker_inventory_truncated": False,
        "total_capacity": 2,
        "active_count": 0,
        "reserved_count": 0,
        "scheduled_count": 0,
        "missing_queues_json": [],
        "stale_execution_queues_json": [],
        "backlog_pending_count": 0,
        "backlog_stale_count": 0,
        "critical_issue_count": 0,
        "warning_issue_count": 0,
        "issue_codes_json": [],
    }
    values.update(overrides)
    return SystemHealthSample(**values)


def _settings(retention_days=30):
    return SimpleNamespace(
        operations_health_history_retention_days=retention_days,
    )


def test_health_sampler_is_idempotent_per_five_minute_bucket(db_session):
    observed_at = datetime(2026, 9, 1, 12, 3, 45, tzinfo=timezone.utc)

    first, created = record_system_health_sample(
        db_session,
        overview=_overview(observed_at),
        worker_topology=_topology(observed_at),
    )
    second, created_again = record_system_health_sample(
        db_session,
        overview=_overview(observed_at + timedelta(minutes=1)),
        worker_topology=_topology(observed_at + timedelta(minutes=1)),
    )

    assert created is True
    assert created_again is False
    assert second.id == first.id
    assert first.sampled_at == datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    assert first.overall_status == "degraded"
    assert first.component_statuses_json == {
        "component:database": "healthy",
        "storage:application_filesystem": "degraded",
        "backlog:reports": "degraded",
    }
    assert "not_allowlisted" not in str(first.component_statuses_json)
    assert first.issue_codes_json == ["reports_stale_secret_detail"]
    assert first.warning_issue_count == 1


def test_health_history_preserves_unknown_worker_load_as_null(db_session):
    observed_at = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    topology = _topology(observed_at).model_copy(
        update={
            "status": "degraded",
            "reason": "partial_inventory",
            "active_count": None,
            "reserved_count": None,
            "scheduled_count": None,
        }
    )

    sample, created = record_system_health_sample(
        db_session,
        overview=_overview(observed_at),
        worker_topology=topology,
    )
    result = collect_health_history(
        db_session,
        window="1h",
        now=observed_at,
        settings=_settings(),
    )

    assert created is True
    assert sample.active_count is None
    assert sample.reserved_count is None
    assert sample.scheduled_count is None
    assert result.samples[0].active_count is None
    assert result.samples[0].reserved_count is None
    assert result.samples[0].scheduled_count is None


def test_health_history_reports_coverage_gaps_and_stale_collection(db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            _row(now - timedelta(minutes=55)),
            _row(now - timedelta(minutes=50), worker_reason="partial_inventory"),
            _row(
                now - timedelta(minutes=35),
                worker_status="degraded",
                worker_reason="execution_stalled",
                stale_execution_queues_json=["processing", "unknown"],
            ),
        ]
    )
    db_session.commit()

    result = collect_health_history(
        db_session,
        window="1h",
        now=now,
        settings=_settings(),
    )

    assert result.sample_interval_seconds == 300
    assert result.effective_resolution_seconds == 300
    assert result.coverage.expected_sample_count == 12
    assert result.coverage.actual_sample_count == 3
    assert result.coverage.returned_sample_count == 3
    assert result.coverage.missing_sample_count == 9
    assert result.coverage.coverage_percent == 25.0
    assert result.coverage.gap_count == 2
    assert result.coverage.returned_gap_interval_count == 2
    assert result.coverage.gap_intervals_truncated is False
    assert [gap.kind for gap in result.coverage.gap_intervals] == [
        "internal",
        "trailing",
    ]
    assert result.coverage.largest_gap_seconds == 2100
    assert result.coverage.collection_stale is True
    assert result.samples[-1].stale_execution_queues == ["processing"]


def test_health_history_downsamples_to_bounded_points_and_keeps_latest(db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row(now - timedelta(minutes=5 * index))
        for index in range(HEALTH_HISTORY_MAX_POINTS + 1)
    ]
    db_session.add_all(rows)
    db_session.commit()

    result = collect_health_history(
        db_session,
        window="7d",
        now=now,
        settings=_settings(retention_days=45),
    )

    assert len(result.samples) <= HEALTH_HISTORY_MAX_POINTS
    assert result.coverage.actual_sample_count == HEALTH_HISTORY_MAX_POINTS + 1
    assert result.coverage.returned_sample_count == len(result.samples)
    assert result.effective_resolution_seconds == 600
    assert result.downsampling_strategy == "transition_anomaly_preserving"
    assert result.samples[-1].sampled_at == now
    assert result.retention_days == 45


def test_health_history_downsampling_preserves_one_sample_incident(db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=5 * HEALTH_HISTORY_MAX_POINTS)
    incident_index = 111
    incident_at = start + timedelta(minutes=5 * incident_index)
    rows = []
    for index in range(HEALTH_HISTORY_MAX_POINTS + 1):
        sampled_at = start + timedelta(minutes=5 * index)
        overrides = {}
        if index == incident_index:
            overrides = {
                "overall_status": "critical",
                "worker_status": "critical",
                "worker_reason": "missing_consumers",
                "critical_issue_count": 1,
                "missing_queues_json": ["processing"],
                "issue_codes_json": ["required_workers_unavailable"],
            }
        rows.append(_row(sampled_at, **overrides))
    db_session.add_all(rows)
    db_session.commit()

    result = collect_health_history(
        db_session,
        window="7d",
        now=now,
        settings=_settings(),
    )

    samples_by_time = {sample.sampled_at: sample for sample in result.samples}
    assert incident_at in samples_by_time
    assert samples_by_time[incident_at].overall_status == "critical"
    assert result.coverage.source_anomaly_sample_count == 1
    assert result.coverage.returned_anomaly_sample_count == 1
    assert result.coverage.source_transition_count == 2
    assert result.coverage.returned_transition_count == 2
    assert result.coverage.anomaly_evidence_truncated is False
    assert result.coverage.transition_evidence_truncated is False


def test_health_history_downsampling_preserves_non_worker_component_incident(db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=5 * HEALTH_HISTORY_MAX_POINTS)
    incident_index = 222
    incident_at = start + timedelta(minutes=5 * incident_index)
    rows = []
    for index in range(HEALTH_HISTORY_MAX_POINTS + 1):
        sampled_at = start + timedelta(minutes=5 * index)
        overrides = {}
        if index == incident_index:
            overrides = {
                "overall_status": "critical",
                "component_statuses_json": {
                    "component:database": "critical",
                    "component:workers": "healthy",
                },
                "critical_issue_count": 1,
                "issue_codes_json": ["database_unavailable"],
            }
        rows.append(_row(sampled_at, **overrides))
    db_session.add_all(rows)
    db_session.commit()

    result = collect_health_history(
        db_session,
        window="7d",
        now=now,
        settings=_settings(),
    )

    samples_by_time = {sample.sampled_at: sample for sample in result.samples}
    incident = samples_by_time[incident_at]
    assert incident.component_statuses["component:database"] == "critical"
    assert incident.worker_status == "healthy"
    assert incident.issue_codes == ["database_unavailable"]
    assert result.coverage.returned_anomaly_sample_count == 1


def test_health_history_gap_summary_includes_window_boundaries(db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    db_session.add(_row(now - timedelta(minutes=5)))
    db_session.commit()

    result = collect_health_history(
        db_session,
        window="1h",
        now=now,
        settings=_settings(),
    )

    assert result.coverage.gap_count == 1
    assert result.coverage.largest_gap_seconds == 3300
    assert result.coverage.gap_intervals[0].kind == "leading"
    assert result.coverage.gap_intervals[0].start_at == now - timedelta(hours=1)
    assert result.coverage.gap_intervals[0].end_at == now - timedelta(minutes=5)


def test_downsampled_history_exposes_internal_source_gap_interval(db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    sample_count = HEALTH_HISTORY_MAX_POINTS + 100
    start = now - timedelta(minutes=5 * (sample_count - 1))
    missing_indexes = set(range(400, 408))
    db_session.add_all(
        _row(start + timedelta(minutes=5 * index))
        for index in range(sample_count)
        if index not in missing_indexes
    )
    db_session.commit()

    result = collect_health_history(
        db_session,
        window="7d",
        now=now,
        settings=_settings(),
    )

    assert result.downsampling_strategy == "transition_anomaly_preserving"
    outage_start = start + timedelta(minutes=5 * 399)
    outage_end = start + timedelta(minutes=5 * 408)
    assert any(
        gap.kind == "internal"
        and gap.start_at == outage_start
        and gap.end_at == outage_end
        and gap.duration_seconds == 45 * 60
        for gap in result.coverage.gap_intervals
    )


def test_health_history_bounds_gap_intervals_and_reports_truncation(db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    sample_count = HEALTH_HISTORY_MAX_POINTS + 30
    start = now - timedelta(minutes=10 * (sample_count - 1))
    db_session.add_all(
        _row(start + timedelta(minutes=10 * index))
        for index in range(sample_count)
    )
    db_session.commit()

    result = collect_health_history(
        db_session,
        window="7d",
        now=now,
        settings=_settings(),
    )

    assert result.coverage.gap_count == sample_count
    assert result.coverage.returned_gap_interval_count == HEALTH_HISTORY_MAX_POINTS
    assert len(result.coverage.gap_intervals) == HEALTH_HISTORY_MAX_POINTS
    assert result.coverage.gap_intervals_truncated is True
    assert result.coverage.gap_intervals[0].kind == "leading"
    assert result.coverage.gap_intervals == sorted(
        result.coverage.gap_intervals,
        key=lambda gap: gap.start_at,
    )


def test_health_history_filters_malformed_or_non_allowlisted_json(db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    db_session.add(
        _row(
            now - timedelta(minutes=5),
            component_statuses_json={
                "component:database": "healthy",
                "secret:internal": "critical",
            },
            issue_codes_json=["safe-code", {"secret": "must-not-render"}],
        )
    )
    db_session.commit()

    result = collect_health_history(
        db_session,
        window="1h",
        now=now,
        settings=_settings(),
    )

    assert result.samples[0].component_statuses == {
        "component:database": "healthy"
    }
    assert result.samples[0].issue_codes == ["safe-code"]
    assert "must-not-render" not in result.model_dump_json()


def test_health_history_rejects_unsupported_window(db_session):
    with pytest.raises(ValueError, match="Unsupported"):
        collect_health_history(
            db_session,
            window="forever",
            settings=_settings(),
        )
