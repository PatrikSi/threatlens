from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, insert, text

from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.schemas.ai import AILiveStatusResponse
from app.services import ai_ops_metrics
from app.services.ai_ops_common import _percentile
from app.services.ai_task_runtime import get_ai_db_live_status


NOW = datetime(2026, 9, 8, 1, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=2)


def _live(_db):
    return AILiveStatusResponse(
        worker_count=0,
        workers=[],
        active_tasks=[],
        reserved_tasks=[],
        scheduled_tasks=[],
        active_count=0,
        reserved_count=0,
        scheduled_count=0,
        queued_count=0,
        oldest_queued_age_seconds=None,
    )


@pytest.mark.parametrize("count", [0, 1, 2, 10, 11, 20, 21, 31, 51, 111])
def test_database_p95_matches_existing_nearest_rank_and_even_ties(db_session, count):
    values = [index * 10 for index in range(count)]
    db_session.add_all(
        [
            AIUsageEvent(
                feature_type="summary", success=True, latency_ms=value, created_at=NOW
            )
            for value in values + [None]
        ]
    )
    db_session.add(
        AIUsageEvent(
            feature_type="summary", success=False, latency_ms=9999, created_at=NOW
        )
    )
    db_session.flush()

    p95 = db_session.scalar(
        ai_ops_metrics._latency_percentiles(SINCE, None, successful_only=True)
    )
    assert float(p95 or 0) == _percentile(values, 0.95)
    points = ai_ops_metrics._build_time_series(db_session, since=SINCE, now=NOW)
    assert len(points) == 3
    assert points[-1].p95_latency_ms == _percentile(values + [9999], 0.95)
    assert points[0].requests == points[0].p95_latency_ms == 0


def test_overview_preserves_utc_buckets_nulls_failures_and_daily_brief_counts(
    db_session, monkeypatch
):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(ai_ops_metrics, "datetime", FixedDatetime)
    # A non-UTC connection must still produce the old Python UTC date buckets.
    db_session.execute(text("SET LOCAL TIME ZONE 'Pacific/Auckland'"))
    fields = [
        (1, True, 10, None, None, None, None, "summary", None),
        (2, True, 20, 10, 5, 15, "", "summary", None),
        (3, False, 1000, None, 5, 20, "model-b", "report", "Provider TIMEOUT"),
        (25, True, 30, 0, None, 0, "model-b", "report", None),
        (26, False, None, 20, 10, 30, "model-b", "report", "403 FORBIDDEN"),
        (49, False, 99999, 999, 999, 999, "outside-window", "report", "excluded"),
    ]
    db_session.add_all(
        [
            AIUsageEvent(
                created_at=NOW - timedelta(hours=hours),
                success=success,
                latency_ms=latency,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                model=model,
                feature_type=feature,
                error=error,
                failure_category="read_timeout" if hours == 3 else "provider_auth" if hours == 26 else None,
            )
            for hours, success, latency, prompt, completion, total, model, feature, error in fields
        ]
    )
    for status in ["ready", "error", "skipped", "queued"]:
        db_session.add(
            AITaskRun(
                task_type="daily_brief",
                trigger_source="manual",
                status=status,
                created_at=NOW - timedelta(hours=3),
                finished_at=NOW if status == "ready" else None,
            )
        )
    db_session.flush()
    overview = ai_ops_metrics.build_ai_ops_overview(
        db_session, days=2, live_status_loader=_live
    )

    assert overview.kpis.total_requests == 5
    assert overview.kpis.success_rate_pct == 60
    assert overview.kpis.total_tokens == 65
    assert overview.kpis.average_latency_ms == 20
    assert overview.kpis.p95_latency_ms == 30
    assert overview.kpis.last_successful_run_at == NOW
    assert [row.model for row in overview.per_model] == ["model-b", "unknown"]
    assert overview.per_model[0].total_requests == 3
    assert overview.per_model[0].average_latency_ms == 515
    assert overview.token_efficiency.average_prompt_tokens == 10
    assert overview.token_efficiency.average_completion_tokens == 6.67
    assert overview.token_efficiency.average_total_tokens == 16.25
    assert overview.token_efficiency.prompt_to_completion_ratio == 1.5
    assert overview.token_efficiency.top_expensive_feature == "report"
    assert overview.token_efficiency.top_expensive_feature_avg_tokens == 16.67
    assert [(point.bucket, point.requests) for point in overview.time_series] == [
        ("2026-09-06", 1),
        ("2026-09-07", 3),
        ("2026-09-08", 1),
    ]
    assert overview.time_series[1].average_latency_ms == 350
    assert overview.time_series[1].p95_latency_ms == 1000
    assert overview.time_series[1].daily_brief_successes == 1
    assert overview.time_series[1].daily_brief_failures == 1
    assert overview.time_series[1].daily_brief_skips == 1
    assert overview.endpoint_health.median_latency_ms == 20
    assert overview.endpoint_health.rolling_failure_rate_pct == 33.33
    assert overview.endpoint_health.timeout_failures == 1
    assert overview.endpoint_health.last_auth_error == "403 FORBIDDEN"
    assert overview.endpoint_health.last_provider_error == "Provider TIMEOUT"


def test_overview_materializes_groups_instead_of_event_or_run_history(db_session):
    now = datetime.now(timezone.utc)
    db_session.execute(
        insert(AIUsageEvent),
        [
            {
                "feature_type": "summary",
                "success": True,
                "model": "one-model",
                "total_tokens": 10,
                "latency_ms": index,
                "created_at": now,
            }
            for index in range(2000)
        ],
    )
    db_session.execute(
        insert(AITaskRun),
        [
            {
                "task_type": "daily_brief",
                "trigger_source": "manual",
                "status": "ready",
                "created_at": now,
                "finished_at": now,
            }
            for _ in range(1000)
        ],
    )
    db_session.execute(
        insert(AITaskRun),
        [
            {
                "task_type": "daily_brief",
                "trigger_source": "manual",
                "status": "queued",
                "created_at": now,
                "queued_at": now,
            }
            for _ in range(1000)
        ],
    )
    returned_counts = []

    def record_rows(_connection, cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            returned_counts.append(cursor.rowcount)

    connection = db_session.connection()
    event.listen(connection, "after_cursor_execute", record_rows)
    try:
        result = ai_ops_metrics.build_ai_ops_overview(
            db_session, days=2, live_status_loader=get_ai_db_live_status
        )
    finally:
        event.remove(connection, "after_cursor_execute", record_rows)
    assert result.kpis.total_requests == 2000
    assert result.kpis.total_tokens == 20000
    assert result.live.queued_count == 1000
    assert sum(point.daily_brief_successes for point in result.time_series) == 1000
    assert max(returned_counts) == 1


def test_empty_overview_keeps_zero_metrics_and_no_expensive_feature(db_session):
    result = ai_ops_metrics.build_ai_ops_overview(
        db_session, days=1, live_status_loader=_live
    )
    assert (
        result.kpis.total_requests
        == result.kpis.average_latency_ms
        == result.kpis.p95_latency_ms
        == 0
    )
    assert (
        result.endpoint_health.median_latency_ms
        == result.endpoint_health.timeout_failures
        == 0
    )
    assert result.endpoint_health.last_auth_error is None
    assert result.token_efficiency.top_expensive_feature is None
    assert result.token_efficiency.average_total_tokens == 0
    assert len(result.time_series) == 2
    assert result.per_model == []
