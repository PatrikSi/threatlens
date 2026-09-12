"""Workflow membership and replay history obey lifecycle row and write fences."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from queue import Queue
import uuid

import pytest
from sqlalchemy import and_, delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIReprocessMember, AIReportStageArtifact, AIWorkflowDispatch
from app.models.lifecycle_pruning import LifecyclePruningRecord
from app.models.report import Report
from app.services.data_access_envelopes import DATA_ACCESS_RESOURCE_AI_TASK_RUN
from app.services.history_maintenance import _delete_ai_history_with_envelopes
from app.services.lifecycle_dependencies import lifecycle_dependent_row_counts, select_with_dependent_budget
from app.services.lifecycle_pruning import lock_history_dependants, prune_oversized_parent
from app.services.lifecycle_pruning_contracts import PruningContext
from tests.integration.test_incremental_history_pruning import _wait_for_block


OLD = datetime(2024, 1, 1, tzinfo=timezone.utc)
CUTOFF = OLD + timedelta(days=1)
CHILDREN = {
    "dispatch": (AIWorkflowDispatch, AIWorkflowDispatch.run_id),
    "member": (AIReprocessMember, AIReprocessMember.parent_run_id),
    "stage": (AIReportStageArtifact, AIReportStageArtifact.task_run_id),
}


def _parent(db):
    run = AITaskRun(task_type="reprocess", trigger_source="manual", status="ready", finished_at=OLD)
    db.add(run)
    db.flush()
    db.info.setdefault("workflow_cleanup_runs", []).append(run.id)
    return run.id


def _rows(db, kind, run_id, count=1):
    if kind == "dispatch":
        assert count == 1
        return [{"run_id": run_id, "task_name": "fixture", "payload_json": {}, "next_attempt_at": OLD}]
    if kind == "member":
        return [{"parent_run_id": run_id, "item_id": uuid.UUID(int=i + 1), "position": i,
                 "child_run_id": uuid.uuid4(), "outcome": "ready"} for i in range(count)]
    report = Report(title="Retained synthetic document", period_start=OLD, period_end=CUTOFF)
    db.add(report)
    db.flush()
    db.info.setdefault("workflow_cleanup_reports", []).append(report.id)
    return [{"task_run_id": run_id, "operation_scope": f"section:{i:06}", "report_id": report.id,
             "request_fingerprint": "a" * 64, "completion_json": {"payload": {"body_markdown": "Synthetic"}}}
            for i in range(count)]


def _seed(db, kind, run_id, count=1):
    model, _ = CHILDREN[kind]
    rows = _rows(db, kind, run_id, count)
    db.execute(insert(model), rows)
    return rows


def _count(db, kind, run_id):
    model, reference = CHILDREN[kind]
    return db.scalar(select(func.count()).select_from(model).where(reference == run_id))


def _context():
    return PruningContext(CUTOFF, and_(
        AITaskRun.finished_at < CUTOFF,
        ~select(Report.id).where(Report.request_task_run_id == AITaskRun.id).exists(),
    ))


@pytest.fixture
def committed_db(database_engine):
    with Session(database_engine) as db:
        try:
            yield db
        finally:
            db.rollback()
            db.execute(delete(Report).where(Report.id.in_(db.info.get("workflow_cleanup_reports", []))))
            db.execute(delete(AITaskRun).where(AITaskRun.id.in_(db.info.get("workflow_cleanup_runs", []))))
            db.commit()


def test_new_workflow_dependents_participate_in_exact_and_capped_counts(db_session):
    run_id = _parent(db_session)
    _seed(db_session, "dispatch", run_id)
    _seed(db_session, "member", run_id, 4)
    _seed(db_session, "stage", run_id, 5)
    db_session.add_all(AITaskEvent(task_run_id=run_id, event_type="fixture") for _ in range(2))
    db_session.flush()
    assert lifecycle_dependent_row_counts(db_session, model=AITaskRun, parent_ids=[run_id]) == {run_id: 12}
    # Every direct count stops at cap + 1; no payload rows are materialized.
    assert lifecycle_dependent_row_counts(
        db_session, model=AITaskRun, parent_ids=[run_id], max_rows_per_parent=3,
    ) == {run_id: 11}


@pytest.mark.parametrize("kind", ["member", "stage"])
def test_large_composite_history_drains_within_budget_and_preserves_other_parent(db_session, kind):
    run_id, other_id = _parent(db_session), _parent(db_session)
    _seed(db_session, kind, run_id, 10_001)
    _seed(db_session, kind, other_id)  # Same item/scope under a different parent.
    db_session.commit()
    before = select_with_dependent_budget(db_session, model=AITaskRun, candidate_ids=[run_id])
    assert before.ids == [] and before.oversized_count == 1
    drained = select_with_dependent_budget(
        db_session, model=AITaskRun, candidate_ids=[run_id], pruning=_context(),
    )
    assert drained.ids == [] and drained.children_pruned == drained.dependent_rows == 10_000
    db_session.commit()
    assert _count(db_session, kind, run_id) == _count(db_session, kind, other_id) == 1
    assert db_session.get(LifecyclePruningRecord, ("ai_task_runs", run_id)).children_pruned == 10_000
    deleted = _delete_ai_history_with_envelopes(
        db_session, AITaskRun, AITaskRun.finished_at, CUTOFF, 1,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN, extra_predicate=AITaskRun.id == run_id,
        max_dependent_rows=10_000,
    )
    assert deleted == 1
    assert _count(db_session, kind, run_id) == 0 and _count(db_session, kind, other_id) == 1
    assert db_session.get(LifecyclePruningRecord, ("ai_task_runs", run_id)) is None


@pytest.mark.parametrize("kind", list(CHILDREN))
@pytest.mark.parametrize("write", ["insert", "move"])
def test_claimed_task_history_rejects_new_or_retargeted_workflow_references(db_session, kind, write):
    target_id, source_id = _parent(db_session), _parent(db_session)
    existing = _seed(db_session, kind, source_id)
    model, reference = CHILDREN[kind]
    db_session.add(AITaskEvent(task_run_id=target_id, event_type="fixture"))
    db_session.commit()
    assert prune_oversized_parent(
        db_session, model=AITaskRun, parent_id=target_id, context=_context(), limit=1,
    ).children_pruned == 1
    db_session.commit()
    with pytest.raises(DBAPIError, match="Expired history cleanup"), db_session.begin_nested():
        if write == "insert":
            db_session.execute(insert(model), [{**existing[0], reference.key: target_id}])
        else:
            db_session.execute(update(model).where(reference == source_id).values({reference.key: target_id}))
    assert _count(db_session, kind, target_id) == 0 and _count(db_session, kind, source_id) == 1


@pytest.mark.parametrize("kind", list(CHILDREN))
def test_workflow_writer_waiting_before_pruning_commit_is_rejected(committed_db, database_engine, kind):
    db = committed_db
    run_id = _parent(db)
    model, _ = CHILDREN[kind]
    late_rows = _rows(db, kind, run_id)
    db.add(AITaskEvent(task_run_id=run_id, event_type="fixture"))
    db.commit()
    prune_oversized_parent(db, model=AITaskRun, parent_id=run_id, context=_context(), limit=1)
    pids = Queue()

    def insert_late():
        with Session(database_engine) as writer:
            writer.execute(text("SET LOCAL statement_timeout = '5s'"))
            pids.put(writer.scalar(text("SELECT pg_backend_pid()")))
            try:
                writer.execute(insert(model), late_rows)
                writer.commit()
            except DBAPIError as error:
                return error.orig.sqlstate
            return "unexpected_success"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(insert_late)
        _wait_for_block(db, pids.get(timeout=3))
        db.commit()
        assert future.result(timeout=5) == "55000"
    assert _count(db, kind, run_id) == 0


@pytest.mark.parametrize("kind", list(CHILDREN))
def test_final_deletion_skips_locked_workflow_child(committed_db, database_engine, kind):
    db = committed_db
    run_id = _parent(db)
    _seed(db, kind, run_id)
    db.commit()
    model, reference = CHILDREN[kind]
    with Session(database_engine) as writer:
        writer.execute(select(reference).where(reference == run_id).with_for_update()).all()
        assert _delete_ai_history_with_envelopes(
            db, AITaskRun, AITaskRun.finished_at, CUTOFF, 1,
            resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN, extra_predicate=AITaskRun.id == run_id,
            max_dependent_rows=10_000,
        ) == 0
        db.commit()
    assert db.get(AITaskRun, run_id) is not None and _count(db, kind, run_id) == 1


def test_composite_lock_scan_checks_pages_after_the_first_thousand(committed_db, database_engine):
    db = committed_db
    blocked_id, other_id = _parent(db), _parent(db)
    _seed(db, "member", blocked_id, 1002)
    _seed(db, "member", other_id)
    db.commit()
    with Session(database_engine) as writer:
        writer.execute(select(AIReprocessMember.item_id).where(
            AIReprocessMember.parent_run_id == blocked_id, AIReprocessMember.item_id == uuid.UUID(int=1002),
        ).with_for_update()).all()
        db.execute(select(AITaskRun.id).where(AITaskRun.id.in_([blocked_id, other_id])).with_for_update()).all()
        assert lock_history_dependants(db, model=AITaskRun, parent_ids=[blocked_id, other_id]) == [other_id]
        db.rollback()


@pytest.mark.parametrize("protection", ["active", "report"])
def test_recovery_history_survives_parent_eligibility_protections(db_session, protection):
    run_id = _parent(db_session)
    _seed(db_session, "stage", run_id, 12)
    if protection == "active":
        db_session.execute(update(AITaskRun).where(AITaskRun.id == run_id).values(status="running", finished_at=None))
    else:
        db_session.add(Report(title="Pinned report", period_start=OLD, period_end=CUTOFF, request_task_run_id=run_id))
    db_session.commit()
    result = prune_oversized_parent(db_session, model=AITaskRun, parent_id=run_id, context=_context(), limit=7)
    assert result.children_pruned == result.parents_started == 0
    assert _count(db_session, "stage", run_id) == 12
    assert db_session.get(LifecyclePruningRecord, ("ai_task_runs", run_id)) is None
