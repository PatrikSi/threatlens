from tests.unit.ai_workflow_test_support import (
    cleanup_ai_workflow_probe as cleanup_ai_workflow_probe,
)
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from sqlalchemy.orm import Session
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.services.ai_execution_ownership import ai_worker_execution
from app.services.ai_brief_recovery import recover_completed_brief_attempt


def test_superseded_backfill_cannot_reopen_settled_child(database_engine):
    now = datetime.now(timezone.utc)
    with Session(database_engine) as db:
        brief = AIDailyBrief(
            brief_date=now.date(),
            status="ready",
            window_start=now - timedelta(days=1),
            window_end=now,
            model="synthetic",
        )
        db.add(brief)
        db.flush()
        parent = AITaskRun(
            task_type="reprocess",
            trigger_source="manual",
            status="running",
            celery_task_id="replacement",
            metadata_json={"scope": "daily_brief_backfill"},
            target_count=1,
        )
        db.add(parent)
        db.flush()
        child = AITaskRun(
            task_type="daily_brief",
            trigger_source="manual",
            status="error",
            reason="stale_running",
            finished_at=now,
            parent_run_id=parent.id,
            daily_brief_id=brief.id,
            celery_task_id="previous",
            metadata_json={
                "provider_claim": {
                    "resource_type": "daily_brief",
                    "resource_id": str(brief.id),
                    "updated_at": brief.updated_at.isoformat(),
                }
            },
        )
        db.add(child)
        db.commit()
        parent_id, child_id = parent.id, child.id

    @ai_worker_execution
    def resumed(task, task_run_id):
        with Session(database_engine) as db:
            recover_completed_brief_attempt(db, db.get(AITaskRun, child_id))
            db.commit()

    result = resumed(
        SimpleNamespace(request=SimpleNamespace(id="previous")), str(parent_id)
    )
    assert result == {"status": "skipped", "reason": "superseded_delivery"}
    with Session(database_engine) as db:
        row = db.get(AITaskRun, child_id)
        assert row.status == "error"
        assert row.finished_at is not None
        db.delete(db.get(AIDailyBrief, row.daily_brief_id))
        db.commit()
