from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from queue import Queue
import time
import uuid

import pytest
from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.lifecycle_pruning import LifecyclePruningRecord
from app.models.report import Report
from app.services.lifecycle_pruning import prune_oversized_parent
from app.services.lifecycle_pruning_contracts import PruningContext


OLD = datetime(2024, 1, 1, tzinfo=timezone.utc)
CUTOFF = OLD + timedelta(days=1)


@pytest.fixture
def committed_db(database_engine):
    with Session(database_engine) as db:
        try:
            yield db
        finally:
            db.rollback()
            ids = db.info.get("owned_pruning_runs", [])
            db.execute(delete(Report).where(Report.request_task_run_id.in_(ids)))
            db.execute(delete(AITaskRun).where(AITaskRun.id.in_(ids)))
            db.commit()


def _context() -> PruningContext:
    return PruningContext(CUTOFF, and_(
        AITaskRun.finished_at < CUTOFF,
        ~select(Report.id).where(Report.request_task_run_id == AITaskRun.id).exists(),
    ))


def _run_with_events(db: Session, count: int = 25) -> uuid.UUID:
    run = AITaskRun(task_type="connection_test", trigger_source="manual", status="succeeded", finished_at=OLD)
    db.add(run)
    db.flush()
    run_id = run.id
    db.info.setdefault("owned_pruning_runs", []).append(run_id)
    db.add_all(AITaskEvent(task_run_id=run_id, event_type="step", payload_json={}) for _ in range(count))
    db.commit()
    return run_id


def _prune(db: Session, run_id: uuid.UUID, limit: int = 7):
    return prune_oversized_parent(db, model=AITaskRun, parent_id=run_id, context=_context(), limit=limit)


def _event_count(db: Session, run_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(AITaskEvent).where(AITaskEvent.task_run_id == run_id))


def _wait_for_block(db: Session, pid: int) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if db.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
            return
        time.sleep(0.01)
    raise AssertionError("Concurrent writer never reached the source lock")


def test_pruning_progress_is_durable_and_rolls_back_with_child_deletion(db_session):
    run_id = _run_with_events(db_session)
    with pytest.raises(RuntimeError), db_session.begin_nested():
        assert _prune(db_session, run_id).children_pruned == 7
        raise RuntimeError("worker crashed before commit")
    assert _event_count(db_session, run_id) == 25
    assert db_session.get(LifecyclePruningRecord, ("ai_task_runs", run_id)) is None
    assert _prune(db_session, run_id).parents_started == 1
    db_session.commit()
    db_session.expunge_all()
    assert _prune(db_session, run_id).parents_started == 0
    db_session.commit()
    progress = db_session.get(LifecyclePruningRecord, ("ai_task_runs", run_id))
    assert progress.children_pruned == 14
    assert progress.cutoff_at == CUTOFF
    assert _event_count(db_session, run_id) == 11


@pytest.mark.parametrize("write", ["event", "report", "reactivate"])
def test_claimed_history_rejects_new_references_and_reactivation(db_session, write):
    run_id = _run_with_events(db_session)
    _prune(db_session, run_id)
    db_session.commit()
    with pytest.raises(DBAPIError, match="Expired history cleanup"), db_session.begin_nested():
        if write == "event":
            db_session.add(AITaskEvent(task_run_id=run_id, event_type="late", payload_json={}))
        elif write == "report":
            db_session.add(Report(title="Late report", request_task_run_id=run_id, period_start=OLD, period_end=CUTOFF))
        else:
            db_session.execute(update(AITaskRun).where(AITaskRun.id == run_id).values(status="running", finished_at=None))
        db_session.flush()
    assert _event_count(db_session, run_id) == 18


def test_writer_waiting_before_claim_publication_is_rejected_after_commit(committed_db, database_engine):
    db_session = committed_db
    run_id = _run_with_events(db_session)
    _prune(db_session, run_id)
    pid_queue: Queue[int] = Queue()

    def write_late_event() -> str:
        with Session(database_engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '5s'"))
            pid_queue.put(db.scalar(text("SELECT pg_backend_pid()")))
            try:
                db.add(AITaskEvent(task_run_id=run_id, event_type="late", payload_json={}))
                db.commit()
            except DBAPIError as exc:
                return exc.orig.sqlstate
            raise AssertionError("A late event was published into pruning history")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(write_late_event)
        _wait_for_block(db_session, pid_queue.get(timeout=3))
        db_session.commit()
        assert future.result(timeout=5) == "55000"
    assert _event_count(db_session, run_id) == 18


def test_new_report_pin_committed_while_pruner_waits_preserves_events(committed_db, database_engine):
    db_session = committed_db
    run_id = _run_with_events(db_session)
    db_session.add(Report(title="Pinned evidence", request_task_run_id=run_id, period_start=OLD, period_end=CUTOFF))
    db_session.flush()
    pid_queue: Queue[int] = Queue()

    def prune_after_source_lock() -> int:
        with Session(database_engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '5s'"))
            pid_queue.put(db.scalar(text("SELECT pg_backend_pid()")))
            result = _prune(db, run_id)
            db.commit()
            return result.children_pruned

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(prune_after_source_lock)
        _wait_for_block(db_session, pid_queue.get(timeout=3))
        db_session.commit()
        assert future.result(timeout=5) == 0
    assert _event_count(db_session, run_id) == 25
    assert db_session.get(LifecyclePruningRecord, ("ai_task_runs", run_id)) is None
