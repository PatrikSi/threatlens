import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_workflow import AIReportStageArtifact
from app.models.report_generation_lease import ReportGenerationLease
from app.services.ai_report_recovery import (
    prepare_owned_report_resume, report_has_safe_resume_history, requeue_interrupted_report,
)
from app.services.ai_report_stage_artifacts import store_report_stage_completion
from app.services.report_execution import claim_report_generation, invalidate_stale_report_generation
from tests.unit.test_ai_report_stage_artifacts import completion
from tests.unit.test_ai_report_workflow_deferral import owned_report


def receipt(db, report, run, root, *, scope='evidence_batch:0', state='succeeded', attempt=1):
    record = AIProviderAttemptReceipt(operation_id=uuid.uuid5(root, scope), attempt_number=attempt,
        request_fingerprint=('a' if attempt == 1 else 'b')*64, task_run_id_snapshot=run.id,
        feature_type='report', resource_type='report', resource_id=report.id,
        max_attempts=3, requested_max_tokens=1200*attempt, iam_revision=1, data_policy_revision=1,
        data_policy_mode='disabled', state=state,
        io_outcome={'reserved':'reserved', 'ambiguous':'ambiguous', 'voided':'not_sent'}.get(state, 'response_received'),
        retryable=None if state == 'reserved' else state in {'failed', 'voided'},
        settled_at=None if state == 'reserved' else datetime.now(timezone.utc),
        next_max_tokens=2400 if state == 'failed' else None,
        pre_io_failure_count=1 if state == 'voided' else 0,
        last_pre_io_failure_at=datetime.now(timezone.utc) if state == 'voided' else None)
    db.add(record)
    db.flush()
    return record


def resumable_report(db, *, attempts=1):
    report, run, claim = owned_report(db)
    root = uuid.uuid4()
    run.metadata_json = {**run.metadata_json, 'provider_operation_root_id': str(root)}
    db.commit()
    if attempts == 2:
        receipt(db, report, run, root, state='failed')
    receipt(db, report, run, root, attempt=attempts)
    store_report_stage_completion(db, task_run_id=run.id, report_id=report.id,
        operation_scope='evidence_batch:0', request_fingerprint='a'*64,
        completion=replace(completion(), attempt_count=attempts))
    db.commit()
    return report, run, claim, root


def expire_lease(db, report):
    expired = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.get(ReportGenerationLease, report.id).lease_expires_at = expired
    report.generation_lease_expires_at = expired
    db.commit()


def test_crashed_report_resumes_after_fenced_takeover_and_expanded_token_attempt(db_session):
    report, run, claim, root = resumable_report(db_session, attempts=2)
    expire_lease(db_session, report)
    next_claim = claim_report_generation(db_session, report_id=report.id, lease_token='next-owner', lease_seconds=120)
    db_session.commit()
    assert next_claim.status == 'interrupted'
    assert prepare_owned_report_resume(db_session, run_id=run.id, report_id=report.id,
        lease_token='next-owner', generation_fence=next_claim.generation_fence, lease_seconds=120)
    db_session.expire_all()
    assert report.status == 'queued' and report.generation_stage == 'resuming'
    assert run.status == 'running' and run.metadata_json['resumed_after_worker_loss']
    assert db_session.get(ReportGenerationLease, report.id).lease_token == 'next-owner'
    assert len(list(db_session.scalars(select(AIReportStageArtifact).where(AIReportStageArtifact.task_run_id == run.id)))) == 1
    # Saved request fingerprint is the initial1200 budget, successful receipt2400.
    assert len(list(db_session.scalars(select(AIProviderAttemptReceipt).where(AIProviderAttemptReceipt.task_run_id_snapshot == run.id)))) == 2


@pytest.mark.parametrize('fault', ['reserved', 'ambiguous', 'missing_artifact', 'wrong_scope', 'wrong_count', 'canceled'])
def test_report_recovery_requires_proof_for_every_paid_operation(db_session, fault):
    report, run, claim, root = resumable_report(db_session)
    artifact = db_session.get(AIReportStageArtifact, (run.id, 'evidence_batch:0'))
    if fault in {'reserved', 'ambiguous'}:
        receipt(db_session, report, run, root, scope='section:next', state=fault)
    elif fault == 'missing_artifact':
        db_session.delete(artifact)
    elif fault == 'wrong_scope':
        artifact.operation_scope = 'evidence_batch:other'
    elif fault == 'wrong_count':
        artifact.completion_json = {**artifact.completion_json, 'attempt_count': 2}
    else:
        run.metadata_json = {**run.metadata_json, 'cancel_requested_at': datetime.now(timezone.utc).isoformat()}
    db_session.commit()
    assert not report_has_safe_resume_history(db_session, run=run, report=report)
    assert not prepare_owned_report_resume(db_session, run_id=run.id, report_id=report.id,
        lease_token='owner', generation_fence=claim.generation_fence, lease_seconds=120)
    db_session.expire_all()
    assert report.status == run.status == 'running'


def test_reconciler_requeues_safe_report_only_after_expired_lease_is_invalidated(db_session):
    report, run, claim, root = resumable_report(db_session)
    assert not invalidate_stale_report_generation(db_session, report_id=report.id)
    db_session.rollback()
    expire_lease(db_session, report)
    assert invalidate_stale_report_generation(db_session, report_id=report.id)
    assert requeue_interrupted_report(db_session, run=run, report=report)
    db_session.commit()
    assert run.status == report.status == 'queued'
    assert run.dispatch_next_attempt_at is not None
    assert run.celery_task_id == 'report-delivery'


def test_receiptless_legacy_report_does_not_gain_automatic_paid_replay(db_session):
    report, run, claim = owned_report(db_session)
    assert not report_has_safe_resume_history(db_session, run=run, report=report)
    run.metadata_json = {**run.metadata_json, 'report_stage_protocol': 1}
    db_session.commit()
    assert report_has_safe_resume_history(db_session, run=run, report=report)


def test_unaccounted_saved_report_calls_block_automatic_recovery(db_session):
    report, run, claim, root = resumable_report(db_session)
    report.model_calls = 2  # Only one completed provider attempt is retained.
    db_session.commit()
    assert not report_has_safe_resume_history(db_session, run=run, report=report)


def test_report_recovery_validates_one_stage_body_at_a_time(db_session, monkeypatch):
    import weakref

    from sqlalchemy import event
    import app.services.ai_report_recovery as recovery

    report, run, _, root = resumable_report(db_session)
    for index in range(1, 3):
        scope = f'evidence_batch:{index}'
        receipt(db_session, report, run, root, scope=scope)
        store_report_stage_completion(
            db_session, task_run_id=run.id, report_id=report.id,
            operation_scope=scope, request_fingerprint='a' * 64,
            completion=replace(completion(), attempt_count=1, payload={
                'findings': [{'summary': f'Stage {index} ' + 'x' * 262_144, 'citations': ['S1']}],
            }),
        )
    db_session.commit()
    loaded = []
    queries = []
    original_load = recovery.load_report_stage_completion

    def load_one(*args, **kwargs):
        assert all(previous() is None for previous in loaded), 'Previous stage body remained retained'
        result = original_load(*args, **kwargs)
        loaded.append(weakref.ref(result))
        return result

    def capture_query(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith('SELECT') and 'ai_report_stage_artifacts' in statement:
            queries.append(statement)

    monkeypatch.setattr(recovery, 'load_report_stage_completion', load_one)
    engine = db_session.get_bind()
    event.listen(engine, 'before_cursor_execute', capture_query)
    try:
        assert report_has_safe_resume_history(db_session, run=run, report=report)
    finally:
        event.remove(engine, 'before_cursor_execute', capture_query)
    assert len(loaded) == 3
    assert all(previous() is None for previous in loaded)
    assert any('completion_json' not in query for query in queries)
    payload_queries = [query for query in queries if 'completion_json' in query]
    assert len(payload_queries) == 3
    assert all('operation_scope =' in query.split('WHERE', 1)[1] for query in payload_queries)
