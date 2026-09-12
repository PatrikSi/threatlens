from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.schemas.ai import AISettingsUpdate
from app.services.ai_config import apply_ai_settings_update, get_or_create_ai_settings
from app.services.ai_integration import AIDailyBriefGenerationResult
from app.services.ai_ops import (
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TASK_TYPE_REPROCESS,
    AI_TRIGGER_MANUAL,
    _finish_reconciled_stale_run,
    queue_ai_task_run,
)
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation
from app.tasks import ai_brief_tasks


@pytest.mark.parametrize("legacy_parent", [False, True])
def test_backfill_redelivery_after_midnight_preserves_original_dates(
    db_session, monkeypatch, legacy_parent
):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")
    get_settings.cache_clear()
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="synthetic-model",
            daily_brief_enabled=True,
            daily_brief_history_limit=7,
        ),
    )
    parent = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"scope": "daily_brief_backfill", "days": 3},
        target_count=3,
    )
    requested_at = datetime(2026, 7, 3, 23, 58, tzinfo=timezone.utc)
    parent.created_at = parent.queued_at = requested_at
    db_session.commit()
    parent_id = parent.id

    current_time = requested_at

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current_time if tz is not None else current_time.replace(tzinfo=None)

    @contextmanager
    def session_override():
        yield db_session

    @contextmanager
    def acquired_lock():
        yield True

    attempted_references = []

    def generate(_db, *, reference_time, **_kwargs):
        attempted_references.append(reference_time)
        if len(attempted_references) == 2:
            raise SystemExit("synthetic worker loss")
        return AIDailyBriefGenerationResult(
            brief=None,
            status="ready",
            reason=None,
            items_considered=0,
            items_selected=0,
        )

    monkeypatch.setattr(ai_brief_tasks, "datetime", Clock)
    monkeypatch.setattr(ai_brief_tasks, "db_session", session_override)
    monkeypatch.setattr(ai_brief_tasks, "daily_ai_brief_lock", acquired_lock)
    monkeypatch.setattr(ai_brief_tasks, "run_daily_brief_generation", generate)

    with pytest.raises(SystemExit, match="synthetic worker loss"):
        ai_brief_tasks.backfill_daily_ai_briefs.run(3, task_run_id=str(parent_id))

    if legacy_parent:
        # Legacy parents had no anchor; their first child is the authoritative
        # reference even when the request was queued on an earlier date.
        db_session.expire_all()
        parent = db_session.get(AITaskRun, parent_id)
        metadata = dict(parent.metadata_json)
        metadata.pop("backfill_reference_time", None)
        parent.metadata_json = metadata
        parent.created_at = datetime(2026, 7, 2, 20, tzinfo=timezone.utc)
        db_session.commit()

    current_time = datetime(2026, 7, 4, 0, 5, tzinfo=timezone.utc)
    # Accepted work is resumed only after durable recovery fences the old
    # delivery. The synthetic interruption occurred before a provider claim.
    lock_data_policy_revision_for_derivation(db_session)
    assert _finish_reconciled_stale_run(
        db_session, run=parent, snapshot_available=True,
        stale_reason="stale_reprocess_tracking", stale_error="Synthetic worker loss",
    ) == "guarded"
    db_session.commit()
    result = ai_brief_tasks.backfill_daily_ai_briefs.apply(
        kwargs={"days": 3, "task_run_id": str(parent_id)},
        task_id=parent.celery_task_id, throw=True,
    ).get()

    db_session.expire_all()
    parent = db_session.get(AITaskRun, parent_id)
    children = db_session.scalars(
        select(AITaskRun).where(
            AITaskRun.parent_run_id == parent_id,
            AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
        )
    ).all()
    assert result["status"] == "ready"
    assert result["processed_dates"] == ["2026-07-03", "2026-07-02", "2026-07-01"]
    assert [reference.date().isoformat() for reference in attempted_references] == [
        "2026-07-03", "2026-07-02", "2026-07-02", "2026-07-01"
    ]
    assert attempted_references[0] == requested_at
    assert {child.metadata_json["brief_date"] for child in children} == {
        "2026-07-03", "2026-07-02", "2026-07-01"
    }
    assert parent.metadata_json["backfill_reference_time"] == requested_at.isoformat()
    assert parent.processed_count == parent.success_count == parent.target_count == 3
