from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import String, event, insert, literal, select

from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.services.ai_ops_metrics import (
    _normalize_error_text, _normalized_error_expression, list_ai_failures,
)


@pytest.mark.parametrize("value", [None, "", " \t\n", "\u2003error\u00a0\u0085", "a" * 200, "x" * 201,
                                   "\x1f" + "é" * 210 + "\u3000"])
def test_sql_error_normalization_preserves_python_grouping(db_session, value):
    assert db_session.scalar(select(_normalized_error_expression(literal(value, String)))) == _normalize_error_text(value)


def test_failure_history_groups_in_database_and_materializes_only_limit(db_session):
    now = datetime.now(timezone.utc)
    db_session.execute(insert(AIUsageEvent), [
        {"feature_type": "summary", "model": "model-a", "error": "timeout", "success": False,
         "created_at": now} for _ in range(2000)
    ])
    db_session.execute(insert(AIUsageEvent), [
        {"feature_type": "summary", "model": f"model-{index}", "error": "other", "success": False,
         "created_at": now} for index in range(1000)
    ])
    db_session.execute(insert(AITaskRun), [
        {"task_type": "daily_brief", "trigger_source": "manual", "status": "error",
         "error": "timeout", "model": None, "created_at": now, "finished_at": now}
        for _ in range(1500)
    ])
    db_session.flush()
    counts = []

    def observe(_connection, cursor, statement, _parameters, _context, _many):
        if "ai_usage_events" in statement or "ai_task_runs" in statement:
            counts.append(cursor.rowcount)

    connection = db_session.connection()
    event.listen(connection, "after_cursor_execute", observe)
    try:
        result = list_ai_failures(db_session, limit=2)
    finally:
        event.remove(connection, "after_cursor_execute", observe)
    assert [(row.feature_type, row.task_type, row.count) for row in result] == [
        ("summary", None, 2000), (None, "daily_brief", 1500),
    ]
    assert counts == [2]


def test_failure_history_uses_actual_latest_fallback_time_and_stable_ties(db_session):
    now = datetime.now(timezone.utc)
    older = now - timedelta(hours=1)
    db_session.add_all([
        AITaskRun(task_type="report", trigger_source="manual", status="error", model="run",
                  error="failure", created_at=older, updated_at=now, finished_at=None),
        AITaskRun(task_type="report", trigger_source="manual", status="error", model="run",
                  error="failure", created_at=older, updated_at=older, finished_at=older),
        AIUsageEvent(feature_type="summary", model="b", success=False, error=" ", created_at=now),
        AIUsageEvent(feature_type="summary", model="a", success=False, error=" ", created_at=now),
        AIUsageEvent(feature_type="summary", model="outside", success=False, error="failure",
                     created_at=now - timedelta(days=31)),
        AIUsageEvent(feature_type="summary", model="success", success=True, error="failure", created_at=now),
    ])
    db_session.flush()
    result = list_ai_failures(db_session)
    assert [row.model for row in result] == ["run", "a", "b"]
    assert result[0].last_seen_at == now
    assert result[1].error == ""
    assert list_ai_failures(db_session, limit=0) == []
