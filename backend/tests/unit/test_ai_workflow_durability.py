import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIReprocessMember, AIWorkflowDispatch
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services import ai_ops
from app.services.ai_reprocess import ensure_reprocess_child, freeze_reprocess_selection
from app.services.ai_workflow_dispatch import defer_ai_workflow_run
from app.services.ai_workflow_publication import publish_ai_workflow, claim_publication


def item(db, name):
    feed = Feed(name=name, url=f'https://example.test/{name}.xml', enabled=True, fetch_interval_seconds=300)
    db.add(feed)
    db.flush()
    row = Item(feed_id=feed.id, source_guid=name, url=f'https://example.test/{name}',
               canonical_url=f'https://example.test/{name}', title=name, dedupe_key=name,
               content_hash='a' * 64, status='content_fetched')
    db.add(row)
    db.flush()
    db.add(Article(item_id=row.id, final_url=row.url, http_status=200, text='Synthetic article evidence.'))
    db.commit()
    return row


def parent(db, rows):
    run = ai_ops.queue_ai_task_run(db, task_type='reprocess', trigger_source='manual',
        metadata={'item_ids': [str(row.id) for row in rows], 'effective_limit': 10, 'limit': 10})
    db.commit()
    return run


def test_reprocessing_membership_is_accepted_once_and_duplicate_outcomes_do_not_finish_early(db_session):
    a, b = item(db_session, 'a'), item(db_session, 'b')
    run = parent(db_session, [a, b])
    first = ensure_reprocess_child(db_session, parent_id=run.id, item_id=a.id, model=None)
    repeated = ensure_reprocess_child(db_session, parent_id=run.id, item_id=a.id, model=None)
    assert repeated.id == first.id
    second = ensure_reprocess_child(db_session, parent_id=run.id, item_id=b.id, model=None)
    db_session.commit()
    ai_ops.finish_ai_task_run(db_session, run_id=first.id, status='ready')
    ai_ops.finish_ai_task_run(db_session, run_id=first.id, status='error', reason='duplicate')
    db_session.commit()
    assert run.processed_count == 1
    assert run.finished_at is None
    assert first.status == 'ready'
    ai_ops.finish_ai_task_run(db_session, run_id=second.id, status='ready')
    db_session.commit()
    assert run.status == 'ready'
    assert run.processed_count == run.success_count == 2
    assert len(freeze_reprocess_selection(db_session, run)) == 2


def test_outbox_survives_publish_error_and_reuses_delivery_identity(db_session):
    row = item(db_session, 'outbox')
    run = ai_ops.queue_ai_task_run(db_session, task_type='item_enrichment', trigger_source='manual', item_id=row.id)
    db_session.commit()
    @contextmanager
    def factory():
        with Session(db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint") as db:
            yield db
    calls = []
    def failed(*args, **kwargs):
        calls.append(kwargs['task_id'])
        raise RuntimeError('broker uncertain')
    delivery = publish_ai_workflow(run.id, session_factory=factory, publisher=failed, broker_ids=set())
    db_session.expire_all()
    job = db_session.get(AIWorkflowDispatch, run.id)
    assert run.status == 'queued' and job.state == 'pending'
    job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    publish_ai_workflow(run.id, session_factory=factory, publisher=failed, broker_ids={delivery.id})
    assert calls == [delivery.id]
    db_session.expire_all()
    job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    publish_ai_workflow(run.id, session_factory=factory, publisher=lambda *a, **kw: calls.append(kw['task_id']), broker_ids=set())
    assert calls == [delivery.id, delivery.id]
    db_session.expire_all()
    assert db_session.get(AIWorkflowDispatch, run.id).state == 'published'


def test_duplicate_delivery_cannot_claim_running_work_and_deferral_keeps_identity(db_session):
    row = item(db_session, 'claim')
    run = ai_ops.queue_ai_task_run(db_session, task_type='item_enrichment', trigger_source='manual', item_id=row.id)
    assert ai_ops.start_ai_task_run(db_session, run_id=run.id, celery_task_id='delivery') is not None
    db_session.commit()
    assert ai_ops.start_ai_task_run(db_session, run_id=run.id, celery_task_id='delivery') is None
    assert defer_ai_workflow_run(db_session, run_id=run.id, reason='provider_token_budget', retry_after_seconds=120)
    db_session.commit()
    job = db_session.get(AIWorkflowDispatch, run.id)
    assert run.status == 'queued' and job.delivery_id == 'delivery'
    assert (job.next_attempt_at - datetime.now(timezone.utc)).total_seconds() > 100
    assert ai_ops.start_ai_task_run(db_session, run_id=run.id, celery_task_id='delivery') is not None


def test_queued_backlog_does_not_expire_from_missing_worker_inspection(db_session):
    row = item(db_session, 'backlog')
    run = ai_ops.queue_ai_task_run(db_session, task_type='item_enrichment', trigger_source='manual', item_id=row.id)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    run.created_at = run.updated_at = run.queued_at = old
    db_session.commit()
    assert ai_ops._reconcile_stale_ai_runs(db_session, snapshot_available=True,
        workers=[], active_tasks=[], reserved_tasks=[], scheduled_tasks=[]) == 0
    assert run.status == 'queued' and run.finished_at is None
