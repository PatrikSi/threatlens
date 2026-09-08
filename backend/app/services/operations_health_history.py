from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.lifecycle import LifecycleCatalogState, LifecyclePolicy
from app.models.system_health_sample import SystemHealthSample
from app.schemas.operations import (
    OperationsHealthHistoryCoverage,
    OperationsHealthHistoryGapInterval,
    OperationsHealthHistoryPoint,
    OperationsHealthHistoryResponse,
    OperationsOverviewResponse,
    OperationsWorkerTopologyResponse,
)


HEALTH_SAMPLE_INTERVAL_SECONDS = 300
HEALTH_HISTORY_MAX_POINTS = 720
HEALTH_HISTORY_MAX_GAP_INTERVALS = 720
HEALTH_HISTORY_MAX_RAW_SAMPLES = 9_000
MAX_AGGREGATE_COUNT = 2_000_000_000
HEALTH_HISTORY_WINDOWS = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

_STATUS_VALUES = frozenset(
    {"healthy", "degraded", "critical", "unavailable", "unknown"}
)
_WORKER_REASONS = frozenset(
    {
        "healthy",
        "no_replies",
        "probe_failed",
        "queue_inventory_unavailable",
        "partial_inventory",
        "missing_consumers",
        "canary_dispatch_unavailable",
        "execution_evidence_missing",
        "execution_stalled",
        "saturated",
    }
)
_QUEUE_VALUES = frozenset(
    {
        "default",
        "ingest",
        "processing",
        "notifications",
        "maintenance",
        "lifecycle-v1",
        "ai",
        "ai-reports-v2",
    }
)
_COMPONENT_STATUS_KEYS = {
    "component": frozenset(
        {"database", "redis", "workers", "scheduler", "encrypted_data"}
    ),
    "storage": frozenset({"database", "application_filesystem"}),
    "backlog": frozenset({"integration_deliveries", "reports"}),
}
_ALLOWED_COMPONENT_STATUS_KEYS = frozenset(
    f"{prefix}:{key}"
    for prefix, keys in _COMPONENT_STATUS_KEYS.items()
    for key in keys
)
_SAFE_CODE_PATTERN = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class _HistorySelection:
    rows: list[SystemHealthSample]
    stride: int
    strategy: str
    anomaly_indexes: frozenset[int]
    returned_anomaly_count: int
    transition_indexes: frozenset[int]
    returned_transition_count: int


def record_system_health_sample(
    db: Session,
    *,
    overview: OperationsOverviewResponse,
    worker_topology: OperationsWorkerTopologyResponse,
    sampled_at: datetime | None = None,
) -> tuple[SystemHealthSample, bool]:
    bucket = _sample_bucket(sampled_at or overview.generated_at)
    existing = db.scalar(
        select(SystemHealthSample).where(SystemHealthSample.sampled_at == bucket)
    )
    if existing is not None:
        return existing, False

    component_statuses = _component_statuses(overview)
    issue_codes = sorted(
        {
            _safe_code(entry.code)
            for entry in overview.issues
            if _safe_code(entry.code)
        }
    )[:128]
    critical_count = sum(entry.severity == "critical" for entry in overview.issues)
    warning_count = sum(entry.severity == "warning" for entry in overview.issues)

    sample = SystemHealthSample(
        sampled_at=bucket,
        overall_status=_worst_status(
            overview.overall_status,
            worker_topology.status,
        ),
        component_statuses_json=component_statuses,
        worker_status=worker_topology.status,
        worker_reason=worker_topology.reason,
        responding_worker_count=worker_topology.responding_worker_count,
        observed_worker_count=worker_topology.observed_worker_count,
        worker_inventory_truncated=worker_topology.worker_inventory_truncated,
        total_capacity=worker_topology.total_capacity,
        active_count=worker_topology.active_count,
        reserved_count=worker_topology.reserved_count,
        scheduled_count=worker_topology.scheduled_count,
        missing_queues_json=list(worker_topology.missing_queues),
        stale_execution_queues_json=list(
            worker_topology.stale_execution_queues
        ),
        backlog_pending_count=min(
            MAX_AGGREGATE_COUNT,
            sum(entry.pending_count for entry in overview.backlogs),
        ),
        backlog_stale_count=min(
            MAX_AGGREGATE_COUNT,
            sum(entry.stale_count for entry in overview.backlogs),
        ),
        critical_issue_count=min(MAX_AGGREGATE_COUNT, critical_count),
        warning_issue_count=min(MAX_AGGREGATE_COUNT, warning_count),
        issue_codes_json=issue_codes,
    )
    db.add(sample)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(SystemHealthSample).where(SystemHealthSample.sampled_at == bucket)
        )
        if existing is None:
            raise
        return existing, False
    db.refresh(sample)
    return sample, True


def collect_health_history(
    db: Session,
    *,
    window: str = "24h",
    now: datetime | None = None,
    settings: Settings | None = None,
) -> OperationsHealthHistoryResponse:
    if window not in HEALTH_HISTORY_WINDOWS:
        raise ValueError("Unsupported health-history window")
    active_settings = settings or get_settings()
    generated_at = _as_utc(now or datetime.now(timezone.utc))
    requested_start = generated_at - HEALTH_HISTORY_WINDOWS[window]
    rows = list(
        db.scalars(
            select(SystemHealthSample)
            .where(
                SystemHealthSample.sampled_at >= requested_start,
                SystemHealthSample.sampled_at <= generated_at,
            )
            .order_by(SystemHealthSample.sampled_at.asc())
            .limit(HEALTH_HISTORY_MAX_RAW_SAMPLES)
        ).all()
    )
    selection = _select_history_rows(rows)
    selected = selection.rows
    stride = selection.stride

    expected_count = max(
        1,
        int(HEALTH_HISTORY_WINDOWS[window].total_seconds())
        // HEALTH_SAMPLE_INTERVAL_SECONDS,
    )
    gap_intervals = _gap_intervals(
        rows,
        requested_start=requested_start,
        requested_end=generated_at,
    )
    returned_gap_intervals = _bounded_gap_intervals(gap_intervals)
    first_sample = _as_utc(rows[0].sampled_at) if rows else None
    last_sample = _as_utc(rows[-1].sampled_at) if rows else None
    actual_count = len(rows)
    missing_count = max(0, expected_count - actual_count)
    coverage_percent = min(
        100.0,
        round((actual_count / expected_count) * 100, 1),
    )
    collection_stale = (
        last_sample is None
        or (generated_at - last_sample).total_seconds()
        > HEALTH_SAMPLE_INTERVAL_SECONDS * 2
    )
    return OperationsHealthHistoryResponse(
        generated_at=generated_at,
        window=window,
        effective_resolution_seconds=HEALTH_SAMPLE_INTERVAL_SECONDS * stride,
        downsampling_strategy=selection.strategy,
        retention_days=_health_history_retention_days(db, active_settings),
        coverage=OperationsHealthHistoryCoverage(
            requested_start=requested_start,
            requested_end=generated_at,
            first_sample_at=first_sample,
            last_sample_at=last_sample,
            expected_sample_count=expected_count,
            actual_sample_count=actual_count,
            returned_sample_count=len(selected),
            missing_sample_count=missing_count,
            coverage_percent=coverage_percent,
            gap_count=len(gap_intervals),
            returned_gap_interval_count=len(returned_gap_intervals),
            gap_intervals=returned_gap_intervals,
            gap_intervals_truncated=(
                len(returned_gap_intervals) < len(gap_intervals)
            ),
            largest_gap_seconds=max(
                (gap.duration_seconds for gap in gap_intervals),
                default=None,
            ),
            collection_stale=collection_stale,
            source_anomaly_sample_count=len(selection.anomaly_indexes),
            returned_anomaly_sample_count=selection.returned_anomaly_count,
            source_transition_count=len(selection.transition_indexes),
            returned_transition_count=selection.returned_transition_count,
            anomaly_evidence_truncated=(
                selection.returned_anomaly_count < len(selection.anomaly_indexes)
            ),
            transition_evidence_truncated=(
                selection.returned_transition_count
                < len(selection.transition_indexes)
            ),
        ),
        samples=[_history_point(row) for row in selected],
    )


def _health_history_retention_days(db: Session, settings: Settings) -> int:
    catalog_state = db.get(LifecycleCatalogState, 1)
    if catalog_state is None or catalog_state.bootstrapped_at is None:
        return max(
            1,
            min(3650, int(settings.operations_health_history_retention_days)),
        )
    retention_days = db.scalar(
        select(LifecyclePolicy.retention_days).where(
            LifecyclePolicy.target_key == "system_health_samples"
        )
    )
    if retention_days is None:
        raise RuntimeError(
            "The bootstrapped lifecycle catalog is missing the system health policy."
        )
    return max(1, min(3650, int(retention_days)))


def _history_point(row: SystemHealthSample) -> OperationsHealthHistoryPoint:
    component_statuses = _safe_component_statuses(row.component_statuses_json)
    worker_reason = (
        row.worker_reason if row.worker_reason in _WORKER_REASONS else "probe_failed"
    )
    return OperationsHealthHistoryPoint(
        sampled_at=_as_utc(row.sampled_at),
        overall_status=_safe_status(row.overall_status),
        component_statuses=component_statuses,
        worker_status=_safe_status(row.worker_status),
        worker_reason=worker_reason,
        responding_worker_count=max(0, min(64, row.responding_worker_count)),
        observed_worker_count=max(
            0,
            min(1_000_000, row.observed_worker_count),
        ),
        worker_inventory_truncated=bool(row.worker_inventory_truncated),
        total_capacity=(
            max(0, min(1_000_000, row.total_capacity))
            if row.total_capacity is not None
            else None
        ),
        active_count=_safe_optional_count(row.active_count),
        reserved_count=_safe_optional_count(row.reserved_count),
        scheduled_count=_safe_optional_count(row.scheduled_count),
        missing_queues=_safe_queue_list(row.missing_queues_json),
        stale_execution_queues=_safe_queue_list(
            row.stale_execution_queues_json
        ),
        backlog_pending_count=max(
            0,
            min(MAX_AGGREGATE_COUNT, row.backlog_pending_count),
        ),
        backlog_stale_count=max(
            0,
            min(MAX_AGGREGATE_COUNT, row.backlog_stale_count),
        ),
        critical_issue_count=max(
            0,
            min(MAX_AGGREGATE_COUNT, row.critical_issue_count),
        ),
        warning_issue_count=max(
            0,
            min(MAX_AGGREGATE_COUNT, row.warning_issue_count),
        ),
        issue_codes=_safe_issue_codes(row.issue_codes_json, limit=32),
    )


def _select_history_rows(rows: list[SystemHealthSample]) -> _HistorySelection:
    anomaly_indexes = frozenset(
        index for index, row in enumerate(rows) if _is_anomaly(row)
    )
    transition_indexes = frozenset(
        index
        for index in range(1, len(rows))
        if _status_signature(rows[index - 1]) != _status_signature(rows[index])
    )
    if len(rows) <= HEALTH_HISTORY_MAX_POINTS:
        return _HistorySelection(
            rows=list(rows),
            stride=1,
            strategy="none",
            anomaly_indexes=anomaly_indexes,
            returned_anomaly_count=len(anomaly_indexes),
            transition_indexes=transition_indexes,
            returned_transition_count=len(transition_indexes),
        )

    stride = max(1, math.ceil(len(rows) / HEALTH_HISTORY_MAX_POINTS))
    selected = {0, len(rows) - 1}
    transition_context = {
        candidate
        for index in transition_indexes
        for candidate in (index - 1, index)
        if 0 <= candidate < len(rows)
    }
    exceptional = {
        index
        for index, row in enumerate(rows)
        if row.overall_status in {"critical", "unavailable"}
        or row.worker_status in {"critical", "unavailable"}
        or row.critical_issue_count > 0
    }
    _add_selected_indexes(selected, transition_context)
    _add_selected_indexes(selected, exceptional)
    _add_selected_indexes(selected, set(anomaly_indexes))
    _add_selected_indexes(selected, set(range(0, len(rows), stride)))
    bounded_indexes = sorted(selected)[:HEALTH_HISTORY_MAX_POINTS]
    # `_add_selected_indexes` never exceeds the limit; the slice is a final
    # defensive bound against future selection-policy changes.
    selected_set = set(bounded_indexes)
    return _HistorySelection(
        rows=[rows[index] for index in bounded_indexes],
        stride=stride,
        strategy="transition_anomaly_preserving",
        anomaly_indexes=anomaly_indexes,
        returned_anomaly_count=len(selected_set & anomaly_indexes),
        transition_indexes=transition_indexes,
        returned_transition_count=len(selected_set & transition_indexes),
    )


def _add_selected_indexes(selected: set[int], candidates: set[int]) -> None:
    available = HEALTH_HISTORY_MAX_POINTS - len(selected)
    if available <= 0:
        return
    remaining = sorted(candidates - selected)
    if len(remaining) <= available:
        selected.update(remaining)
        return
    selected.update(_evenly_spaced_indexes(remaining, available))


def _evenly_spaced_indexes(indexes: list[int], count: int) -> list[int]:
    if count <= 0:
        return []
    if count >= len(indexes):
        return indexes
    if count == 1:
        return [indexes[len(indexes) // 2]]
    positions = {
        round(position * (len(indexes) - 1) / (count - 1))
        for position in range(count)
    }
    selected = [indexes[position] for position in sorted(positions)]
    if len(selected) < count:
        selected_set = set(selected)
        selected.extend(
            index for index in indexes if index not in selected_set
        )
    return selected[:count]


def _is_anomaly(row: SystemHealthSample) -> bool:
    component_statuses = _safe_component_statuses(row.component_statuses_json)
    return bool(
        row.overall_status != "healthy"
        or row.worker_status != "healthy"
        or row.critical_issue_count
        or row.warning_issue_count
        or row.backlog_stale_count
        or row.missing_queues_json
        or row.stale_execution_queues_json
        or any(
            status != "healthy"
            for status in component_statuses.values()
            if isinstance(status, str)
        )
    )


def _status_signature(row: SystemHealthSample) -> tuple[object, ...]:
    safe_components = tuple(
        sorted(_safe_component_statuses(row.component_statuses_json).items())
    )
    return (
        row.overall_status,
        row.worker_status,
        row.worker_reason,
        safe_components,
        tuple(_safe_queue_list(row.missing_queues_json)),
        tuple(_safe_queue_list(row.stale_execution_queues_json)),
        max(0, row.backlog_stale_count),
        max(0, row.critical_issue_count),
        max(0, row.warning_issue_count),
        tuple(_safe_issue_codes(row.issue_codes_json, limit=128)),
    )


def _gap_intervals(
    rows: list[SystemHealthSample],
    *,
    requested_start: datetime,
    requested_end: datetime,
) -> list[OperationsHealthHistoryGapInterval]:
    gaps = []
    timestamps = [
        _as_utc(requested_start),
        *(_as_utc(row.sampled_at) for row in rows),
        _as_utc(requested_end),
    ]
    final_pair_index = len(timestamps) - 2
    for index, (previous, current) in enumerate(
        zip(timestamps, timestamps[1:], strict=False)
    ):
        delta = int((current - previous).total_seconds())
        if delta > HEALTH_SAMPLE_INTERVAL_SECONDS * 3 // 2:
            if not rows:
                kind = "window"
            elif index == 0:
                kind = "leading"
            elif index == final_pair_index:
                kind = "trailing"
            else:
                kind = "internal"
            gaps.append(
                OperationsHealthHistoryGapInterval(
                    start_at=previous,
                    end_at=current,
                    duration_seconds=delta,
                    kind=kind,
                )
            )
    return gaps


def _bounded_gap_intervals(
    gaps: list[OperationsHealthHistoryGapInterval],
) -> list[OperationsHealthHistoryGapInterval]:
    if len(gaps) <= HEALTH_HISTORY_MAX_GAP_INTERVALS:
        return gaps
    longest_index = max(
        range(len(gaps)),
        key=lambda index: gaps[index].duration_seconds,
    )
    selected = {0, len(gaps) - 1, longest_index}
    remaining = [index for index in range(len(gaps)) if index not in selected]
    selected.update(
        _evenly_spaced_indexes(
            remaining,
            HEALTH_HISTORY_MAX_GAP_INTERVALS - len(selected),
        )
    )
    return [gaps[index] for index in sorted(selected)]


def _component_statuses(
    overview: OperationsOverviewResponse,
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for prefix, entries in (
        ("component", overview.components),
        ("storage", overview.storage),
        ("backlog", overview.backlogs),
    ):
        allowed_keys = _COMPONENT_STATUS_KEYS[prefix]
        for entry in entries:
            if entry.key in allowed_keys and entry.status in _STATUS_VALUES:
                statuses[f"{prefix}:{entry.key}"] = entry.status
    return statuses


def _sample_bucket(value: datetime) -> datetime:
    observed = _as_utc(value)
    minute = observed.minute - (observed.minute % 5)
    return observed.replace(minute=minute, second=0, microsecond=0)


def _safe_status(value: object) -> str:
    return str(value) if value in _STATUS_VALUES else "unknown"


def _safe_optional_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, min(1_000_000, value))


def _safe_queue_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted(
        {value for value in values[:16] if isinstance(value, str) and value in _QUEUE_VALUES}
    )


def _safe_component_statuses(values: object) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    return {
        key: value
        for key, value in values.items()
        if isinstance(key, str)
        and key in _ALLOWED_COMPONENT_STATUS_KEYS
        and value in _STATUS_VALUES
    }


def _safe_issue_codes(values: object, *, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    return [
        code
        for value in values[:limit]
        if isinstance(value, str) and (code := _safe_code(value))
    ]


def _safe_code(value: object) -> str:
    normalized = _SAFE_CODE_PATTERN.sub("_", str(value or "").lower()).strip("_")
    return normalized[:64]


def _worst_status(*statuses: str) -> str:
    priority = {
        "healthy": 0,
        "unknown": 1,
        "degraded": 2,
        "unavailable": 3,
        "critical": 4,
    }
    return max(statuses, key=lambda value: priority.get(value, 1))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
