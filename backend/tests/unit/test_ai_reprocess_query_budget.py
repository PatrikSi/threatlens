from tests.unit.ai_workflow_test_support import (
    cleanup_ai_workflow_probe as cleanup_ai_workflow_probe,
)
import uuid
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIReprocessMember
from app.services.ai_reprocess import recalculate_reprocess_progress


@pytest.mark.parametrize("size", [100, 500])
def test_reprocess_progress_has_constant_query_budget(database_engine, size):
    with Session(database_engine) as db:
        parent = AITaskRun(
            task_type="reprocess",
            trigger_source="manual",
            status="running",
            metadata_json={"selection_frozen": True, "selection_version": 1},
            target_count=size,
        )
        db.add(parent)
        db.flush()
        parent_id = parent.id
        for i in range(size):
            child = AITaskRun(
                id=uuid.uuid4(),
                task_type="item_enrichment",
                trigger_source="manual",
                status="queued",
                parent_run_id=parent_id,
                metadata_json={},
            )
            db.add(child)
            db.add(
                AIReprocessMember(
                    parent_run_id=parent_id,
                    item_id=uuid.uuid4(),
                    position=i,
                    child_run_id=child.id,
                )
            )
        db.commit()
    statements = []

    def after(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with Session(database_engine) as db:
        parent = db.get(AITaskRun, parent_id)
        event.listen(database_engine, "after_cursor_execute", after)
        try:
            recalculate_reprocess_progress(db, parent=parent)
            db.commit()
        finally:
            event.remove(database_engine, "after_cursor_execute", after)
    child_selects = sum(
        "FROM ai_task_runs" in sql and "WHERE ai_task_runs.id =" in sql
        for sql in statements
    )
    assert child_selects <= 2
    assert len(statements) <= 8


def test_outcome_repair_is_bounded_and_eventually_finishes(database_engine):
    from datetime import datetime, timezone
    from app.services.ai_reprocess import PROGRESS_REPAIR_BATCH_SIZE

    size = PROGRESS_REPAIR_BATCH_SIZE * 2 + 1
    with Session(database_engine) as db:
        parent = AITaskRun(
            task_type="reprocess",
            trigger_source="manual",
            status="running",
            metadata_json={"selection_frozen": True},
            target_count=size,
        )
        db.add(parent)
        db.flush()
        parent_id = parent.id
        for index in range(size):
            child = AITaskRun(
                id=uuid.uuid4(),
                task_type="item_enrichment",
                trigger_source="manual",
                status="ready",
                finished_at=datetime.now(timezone.utc),
                parent_run_id=parent_id,
                metadata_json={},
            )
            db.add(child)
            db.add(
                AIReprocessMember(
                    parent_run_id=parent_id,
                    item_id=uuid.uuid4(),
                    position=index,
                    child_run_id=child.id,
                )
            )
        db.commit()
    with Session(database_engine) as db:
        parent = db.get(AITaskRun, parent_id)
        recalculate_reprocess_progress(db, parent=parent)
        db.commit()
        assert parent.processed_count == PROGRESS_REPAIR_BATCH_SIZE
        assert parent.status == "running"
        recalculate_reprocess_progress(db, parent=parent)
        db.commit()
        assert parent.processed_count == PROGRESS_REPAIR_BATCH_SIZE * 2
        assert parent.status == "running"
        recalculate_reprocess_progress(db, parent=parent)
        db.commit()
        assert parent.processed_count == parent.success_count == size
        assert parent.status == "ready"
