import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIReprocessMember, AIWorkflowDispatch
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.services import ai_ops
from app.services.ai_reprocess import ensure_reprocess_child, recalculate_reprocess_progress
from app.services.ai_workflow_recovery import adopt_legacy_workflows, recover_stale_workflow
from tests.unit.test_ai_workflow_durability import item, parent


def test_legacy_selection_uses_saved_membership_and_prefers_ready_duplicate(db_session):
    a, b = item(db_session, 'legacy-a'), item(db_session, 'legacy-b')
    run = parent(db_session, [a, b])
    db_session.execute(delete(AIWorkflowDispatch).where(AIWorkflowDispatch.run_id == run.id))
    db_session.execute(delete(AIReprocessMember).where(AIReprocessMember.parent_run_id == run.id))
    run.metadata_json = {'days': 7, 'limit': 2}
    now = datetime.now(timezone.utc)
    children = [AITaskRun(task_type='item_enrichment', trigger_source='manual', parent_run_id=run.id,
        item_id=row.id, status=status, finished_at=now if status != 'queued' else None,
        queued_at=now, metadata_json={}) for row, status in [(a, 'ready'), (a, 'error'), (b, 'queued')]]
    db_session.add_all(children)
    db_session.commit()
    assert adopt_legacy_workflows(db_session, limit=20) >= 1
    recalculate_reprocess_progress(db_session, parent=run)
    db_session.commit()
    assert run.target_count == 2
    assert run.processed_count == run.success_count == 1
    assert run.finished_at is None
    assert ensure_reprocess_child(db_session, parent_id=run.id, item_id=a.id, model=None).id == children[0].id
    assert ensure_reprocess_child(db_session, parent_id=run.id, item_id=b.id, model=None).id == children[2].id
    ai_ops.finish_ai_task_run(db_session, run_id=children[2].id, status='ready')
    db_session.commit()
    assert run.status == 'ready' and run.processed_count == 2


@pytest.mark.parametrize('state,retryable,expected', [
    ('reserved', None, None), ('ambiguous', False, None), ('succeeded', False, None),
    ('failed', False, None), ('failed', True, 'guarded'), ('voided', True, 'guarded'),
])
def test_worker_recovery_never_repeats_unknown_or_successful_provider_io(db_session, state, retryable, expected):
    row = item(db_session, 'receipt-' + state + str(retryable))
    run = ai_ops.queue_ai_task_run(db_session, task_type='item_enrichment', trigger_source='manual', item_id=row.id)
    ai_ops.start_ai_task_run(db_session, run_id=run.id, celery_task_id='old-delivery')
    now = datetime.now(timezone.utc)
    receipt = AIProviderAttemptReceipt(operation_id=uuid.uuid4(), attempt_number=1,
        request_fingerprint='a'*64, task_run_id_snapshot=run.id, feature_type='item_enrichment',
        resource_type='item', resource_id=row.id, max_attempts=3, requested_max_tokens=1000,
        iam_revision=1, data_policy_revision=1, data_policy_mode='disabled',
        state=state, io_outcome={'reserved':'reserved', 'ambiguous':'ambiguous', 'voided':'not_sent'}.get(state, 'response_received'),
        retryable=retryable, settled_at=None if state == 'reserved' else now,
        next_max_tokens=1000 if state == 'failed' and retryable else None,
        pre_io_failure_count=1 if state == 'voided' else 0,
        last_pre_io_failure_at=now if state == 'voided' else None)
    db_session.add(receipt)
    db_session.commit()
    result = recover_stale_workflow(db_session, run)
    db_session.commit()
    assert result == expected
    if expected == 'guarded':
        assert run.status == 'queued' and run.celery_task_id != 'old-delivery'
    else:
        assert run.status == 'running' and run.celery_task_id == 'old-delivery'
    assert receipt.state == state


def test_deleted_child_history_is_not_recreated_under_a_new_paid_identity(db_session):
    row = item(db_session, 'retained-child-identity')
    run = parent(db_session, [row])
    child = ensure_reprocess_child(db_session, parent_id=run.id, item_id=row.id, model=None)
    child_id = child.id
    db_session.commit()
    db_session.delete(child)
    db_session.commit()
    assert ensure_reprocess_child(db_session, parent_id=run.id, item_id=row.id, model=None) is None
    db_session.commit()
    member = db_session.get(AIReprocessMember, (run.id, row.id))
    assert member.child_run_id == child_id
    assert member.outcome == 'error' and member.reason == 'child_history_unavailable'
    assert run.status == 'error'


def test_parent_recovers_known_child_completion_without_worker_inspection(db_session):
    row = item(db_session, 'completion-repair')
    run = parent(db_session, [row])
    child = ensure_reprocess_child(db_session, parent_id=run.id, item_id=row.id, model=None)
    ai_ops.start_ai_task_run(db_session, run_id=run.id, celery_task_id='parent')
    child.status, child.finished_at = 'ready', datetime.now(timezone.utc)
    db_session.commit()
    ai_ops._reconcile_stale_ai_runs(db_session, snapshot_available=False,
        workers=[], active_tasks=[], reserved_tasks=[], scheduled_tasks=[])
    db_session.expire_all()
    assert run.status == 'ready'
    assert run.processed_count == run.success_count == 1
