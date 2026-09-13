import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIReprocessMember, AIWorkflowDispatch
from app.models.feed import Feed
from app.services import ai_ops
from app.services.ai_reprocess import ensure_reprocess_child
from app.services.ai_workflow_publication import claim_publication
from tests.unit.test_ai_workflow_durability import item, parent


def concurrent(operation):
    barrier = Barrier(2)
    def worker(index):
        barrier.wait(timeout=5)
        return operation(index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(worker, index) for index in range(2)]
        return [result.result(timeout=15) for result in results]


def test_two_publishers_admit_one_delivery_and_two_workers_claim_once(database_engine):
    with Session(database_engine) as db:
        row = item(db, 'concurrent-' + uuid.uuid4().hex)
        feed_id = row.feed_id
        run = ai_ops.queue_ai_task_run(db, task_type='item_enrichment', trigger_source='manual', item_id=row.id)
        db.commit()
        run_id = run.id
    try:
        def publish(_):
            with Session(database_engine) as db:
                result = claim_publication(db, run_id=run_id, now=datetime.now(timezone.utc), broker_ids=set())
                db.commit()
                return result
        results = concurrent(publish)
        assert sum(result is not None for result in results) == 1
        delivery_id = next(result[2] for result in results if result)
        def execute(_):
            with Session(database_engine) as db:
                run = ai_ops.start_ai_task_run(db, run_id=run_id, celery_task_id=delivery_id)
                result = run is not None
                db.commit()
                return result
        assert sorted(concurrent(execute)) == [False, True]
    finally:
        with Session(database_engine) as db:
            db.execute(delete(AITaskRun).where(AITaskRun.id == run_id))
            db.execute(delete(Feed).where(Feed.id == feed_id))
            db.commit()


def test_two_reprocess_fanouts_create_one_child_and_one_outcome(database_engine):
    with Session(database_engine) as db:
        row = item(db, 'fanout-' + uuid.uuid4().hex)
        item_id, feed_id = row.id, row.feed_id
        run = parent(db, [row])
        run_id = run.id
    try:
        def fanout(_):
            with Session(database_engine) as db:
                child = ensure_reprocess_child(db, parent_id=run_id, item_id=item_id, model='synthetic')
                child_id = child.id
                db.commit()
                return child_id
        child_ids = concurrent(fanout)
        assert child_ids[0] == child_ids[1]
        def finish(_):
            with Session(database_engine) as db:
                ai_ops.finish_ai_task_run(db, run_id=child_ids[0], status='ready')
                db.commit()
        concurrent(finish)
        with Session(database_engine) as db:
            run = db.get(AITaskRun, run_id)
            assert run.status == 'ready'
            assert run.processed_count == run.success_count == 1
            assert len(list(db.scalars(select(AIReprocessMember).where(AIReprocessMember.parent_run_id == run_id)))) == 1
    finally:
        with Session(database_engine) as db:
            db.execute(delete(AITaskRun).where((AITaskRun.id == run_id) | (AITaskRun.parent_run_id == run_id)))
            db.execute(delete(Feed).where(Feed.id == feed_id))
            db.commit()
