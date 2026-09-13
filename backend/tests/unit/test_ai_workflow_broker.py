import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.ai_workflow import AIWorkflowDispatch
from app.services import ai_ops, ai_workflow_publication as publication
from tests.unit.test_ai_workflow_durability import item


def test_broker_probe_includes_prefetched_unacked_delivery(monkeypatch):
    class Broker:
        def llen(self, key):
            return 0
        def hlen(self, key):
            assert key == 'unacked'
            return 1
        def hvals(self, key):
            return [json.dumps([{'headers': {'id': 'reserved-delivery'}}, '', 'ai'])]
        def close(self):
            pass
    monkeypatch.setattr(publication, 'redis_client_from_url', lambda *a, **kw: Broker())
    assert publication.queued_ai_delivery_ids() == {'reserved-delivery'}


@pytest.mark.parametrize('mode', ['oversized', 'broken'])
def test_unknown_broker_state_never_authorizes_duplicate_publication(monkeypatch, mode):
    class Broker:
        def llen(self, key):
            if mode == 'broken':
                raise RuntimeError('unavailable')
            return 0
        def hlen(self, key):
            return publication.BROKER_SCAN_LIMIT + 1
        def hvals(self, key):
            raise AssertionError('oversized state must not be loaded')
        def close(self):
            pass
    monkeypatch.setattr(publication, 'redis_client_from_url', lambda *a, **kw: Broker())
    assert publication.queued_ai_delivery_ids() is None


def test_uncertain_published_work_still_counts_toward_admission(db_session, monkeypatch):
    monkeypatch.setattr(publication, 'WORKFLOW_MAX_OUTSTANDING_PUBLICATIONS', 1)
    row = item(db_session, 'uncertain-slot')
    first = ai_ops.queue_ai_task_run(db_session, task_type='item_enrichment', trigger_source='manual', item_id=row.id)
    second = ai_ops.queue_ai_task_run(db_session, task_type='item_enrichment', trigger_source='manual', item_id=row.id)
    db_session.commit()
    now = datetime.now(timezone.utc)
    claim = publication.claim_publication(db_session, run_id=first.id, now=now, broker_ids=set())
    db_session.commit()
    publication.settle_publication(db_session, run_id=first.id, claim_token=claim[3], now=now,
                                  error='broker_publication_unavailable')
    db_session.commit()
    assert publication.claim_publication(db_session, run_id=second.id, now=now, broker_ids=set()) is None
    assert db_session.get(AIWorkflowDispatch, second.id).error == 'queue_admission_wait'
    # The original logical delivery can retry; it already occupies its slot.
    assert publication.claim_publication(db_session, run_id=first.id, now=now+timedelta(seconds=60), broker_ids=set())


def test_api_handoff_is_once_and_cannot_take_inline_provider_work(db_session):
    row = item(db_session, 'api-handoff')
    run = ai_ops.queue_ai_task_run(db_session, task_type='item_enrichment', trigger_source='manual', item_id=row.id)
    ai_ops.start_ai_task_run(db_session, run_id=run.id, worker_name='api')
    db_session.commit()
    assert ai_ops.start_ai_task_run(db_session, run_id=run.id, worker_name='worker', celery_task_id='delivery')
    db_session.commit()
    assert ai_ops.start_ai_task_run(db_session, run_id=run.id, worker_name='worker', celery_task_id='delivery') is None
    inline = ai_ops.queue_ai_task_run(db_session, task_type='daily_brief', trigger_source='manual', metadata={'inline_execution': True})
    ai_ops.start_ai_task_run(db_session, run_id=inline.id, worker_name='api')
    db_session.commit()
    assert ai_ops.start_ai_task_run(db_session, run_id=inline.id, worker_name='worker', celery_task_id='other') is None
