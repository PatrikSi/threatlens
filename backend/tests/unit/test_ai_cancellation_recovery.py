from tests.unit.ai_workflow_test_support import (
    cleanup_ai_workflow_probe as cleanup_ai_workflow_probe,
)
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIWorkflowDispatch
from app.models.data_policy import DataPolicyState
from app.services import ai_ops
from app.services.ai_reprocess import ensure_reprocess_child
from app.services.ai_telemetry_data_policy import cancel_ai_task_run_for_data_access
from app.services.ai_workflow_publication import claim_publication
from app.services.data_access_policy import DataAccessContext
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation
from tests.unit.test_ai_workflow_durability import item, parent


def access(db):
    state = db.get(DataPolicyState, 1)
    return DataAccessContext(
        mode=state.mode,
        policy_revision=state.revision,
        coverage_version=state.coverage_version,
        principal_type="user",
        principal_id=uuid.uuid4(),
        principal_eligible=True,
        allowed_label_ids=frozenset(),
    )


def test_active_api_cancel_service_does_not_deadlock_child_completion(
    database_engine, monkeypatch
):
    with Session(database_engine) as db:
        row = item(db, "api-cancel-deadlock-" + uuid.uuid4().hex)
        run = parent(db, [row])
        run_id = run.id
        child = ensure_reprocess_child(db, parent_id=run_id, item_id=row.id, model=None)
        child_id = child.id
        db.commit()
        context = access(db)
    monkeypatch.setattr(
        ai_ops, "_load_live_task_snapshot", lambda: (True, [], [], [], [])
    )
    monkeypatch.setattr(ai_ops.celery_app.control, "revoke", lambda *args, **kw: None)
    child_locked, parent_locked = Event(), Event()

    def finish():
        with Session(database_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            lock_data_policy_revision_for_derivation(db)
            db.scalar(
                select(AITaskRun).where(AITaskRun.id == child_id).with_for_update()
            )
            child_locked.set()
            assert parent_locked.wait(5)
            ai_ops.finish_ai_task_run(db, run_id=child_id, status="ready")
            db.commit()

    errors = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(finish)
        assert child_locked.wait(5)
        try:
            with Session(database_engine) as db:
                db.execute(text("SET LOCAL lock_timeout = '5s'"))
                original_scalar = db.scalar

                def observed_scalar(statement, *args, **kwargs):
                    result = original_scalar(statement, *args, **kwargs)
                    if (
                        isinstance(result, AITaskRun)
                        and result.id == run_id
                        and statement._for_update_arg is not None
                    ):
                        parent_locked.set()
                    return result

                db.scalar = observed_scalar
                cancel_ai_task_run_for_data_access(
                    db, run_id=run_id, actor_user_id=None, data_access=context
                )
                db.commit()
        except Exception as exc:
            errors.append(
                type(exc.orig).__name__ if hasattr(exc, "orig") else type(exc).__name__
            )
        try:
            future.result(timeout=10)
        except Exception as exc:
            errors.append(
                type(exc.orig).__name__ if hasattr(exc, "orig") else type(exc).__name__
            )
    assert errors == []


def test_active_api_cancel_without_inspection_finishes_queued(
    database_engine, monkeypatch
):
    with Session(database_engine) as db:
        row = item(db, "api-cancel-queued-" + uuid.uuid4().hex)
        run = ai_ops.queue_ai_task_run(
            db, task_type="item_enrichment", trigger_source="manual", item_id=row.id
        )
        run_id = run.id
        assert (
            claim_publication(
                db, run_id=run_id, now=datetime.now(timezone.utc), broker_ids=set()
            )
            is not None
        )
        db.commit()
        context = access(db)
    monkeypatch.setattr(
        ai_ops, "_load_live_task_snapshot", lambda: (False, [], [], [], [])
    )
    revocations = []
    monkeypatch.setattr(
        ai_ops.celery_app.control,
        "revoke",
        lambda *args, **kw: revocations.append(args[0]),
    )
    with Session(database_engine) as db:
        cancel_ai_task_run_for_data_access(
            db, run_id=run_id, actor_user_id=None, data_access=context
        )
        db.commit()
        run = db.get(AITaskRun, run_id)
        assert revocations == [run.celery_task_id]
        assert run.status == "skipped" and run.reason == "canceled"
        old = datetime.now(timezone.utc) - timedelta(days=30)
        run.updated_at = run.queued_at = run.created_at = old
        db.get(AIWorkflowDispatch, run_id).next_attempt_at = old
        db.commit()
        assert (
            ai_ops._reconcile_stale_ai_runs(
                db,
                snapshot_available=True,
                workers=[],
                active_tasks=[],
                reserved_tasks=[],
                scheduled_tasks=[],
            )
            == 0
        )
        assert (
            claim_publication(
                db, run_id=run_id, now=datetime.now(timezone.utc), broker_ids=set()
            )
            is None
        )
        db.commit()
        db.expire_all()
        run = db.get(AITaskRun, run_id)
        assert run.status == "skipped" and run.finished_at is not None


def test_maintenance_finishes_legacy_queued_cancellation(database_engine):
    from app.services.ai_task_cancellation import reconcile_canceled_ai_tasks

    with Session(database_engine) as db:
        row = item(db, "legacy-cancel-" + uuid.uuid4().hex)
        run = ai_ops.queue_ai_task_run(
            db, task_type="item_enrichment", trigger_source="manual", item_id=row.id
        )
        run_id = run.id
        assert claim_publication(
            db, run_id=run_id, now=datetime.now(timezone.utc), broker_ids=set()
        )
        run.reason = "cancel_requested"
        run.metadata_json = {
            **run.metadata_json,
            "cancel_requested_at": datetime.now(timezone.utc).isoformat(),
        }
        db.commit()
        assert reconcile_canceled_ai_tasks(db) == 1
        assert db.get(AITaskRun, run_id).status == "skipped"
        assert db.get(AIWorkflowDispatch, run_id).state == "complete"


def test_cancellation_refences_policy_between_child_transactions(
    database_engine, monkeypatch
):
    import pytest
    from app.services.data_access_policy import DataPolicyRevisionConflict
    from app.services.ai_task_cancellation import reconcile_canceled_ai_tasks

    with Session(database_engine) as db:
        rows = [item(db, "cancel-policy-" + uuid.uuid4().hex) for _ in range(2)]
        run = parent(db, rows)
        run_id = run.id
        children = [
            ensure_reprocess_child(db, parent_id=run_id, item_id=row.id, model=None).id
            for row in rows
        ]
        db.commit()
        context = access(db)
    monkeypatch.setattr(
        ai_ops, "_load_live_task_snapshot", lambda: (False, [], [], [], [])
    )
    checkpoints = []

    def checkpoint(db):
        checkpoints.append(1)
        if len(checkpoints) == 4:
            with Session(database_engine) as change:
                state = change.get(DataPolicyState, 1)
                state.revision += 1
                change.commit()

    try:
        with Session(database_engine) as db:
            with pytest.raises(DataPolicyRevisionConflict):
                cancel_ai_task_run_for_data_access(
                    db,
                    run_id=run_id,
                    actor_user_id=None,
                    data_access=context,
                    authorization_checkpoint=checkpoint,
                )
            db.rollback()
            states = [db.get(AITaskRun, child).status for child in children]
            assert sorted(states) == ["queued", "skipped"]
            assert db.get(AITaskRun, run_id).metadata_json["cancel_requested_at"]
            assert reconcile_canceled_ai_tasks(db) >= 1
            assert all(
                db.get(AITaskRun, child).status == "skipped" for child in children
            )
    finally:
        with Session(database_engine) as db:
            state = db.get(DataPolicyState, 1)
            state.revision = context.policy_revision
            db.commit()
