import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ai_workflow import AIReportStageArtifact
from app.models.report import Report
from app.services.ai_ops import queue_ai_task_run, start_ai_task_run
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_report_stage_artifacts import (
    load_report_stage_completion, store_report_stage_completion, has_report_stage_completion,
)


def setup_report(db):
    now = datetime.now(timezone.utc)
    report = Report(title='Synthetic report', report_type='custom', status='running',
        trigger_source='manual', generation_stage='evidence_synthesis', period_start=now-timedelta(days=7),
        period_end=now, filters_json={}, prompt_config_json={}, sections_config_json=[])
    db.add(report)
    db.flush()
    run = queue_ai_task_run(db, task_type='report', trigger_source='manual', report_id=report.id)
    start_ai_task_run(db, run_id=run.id, celery_task_id='report-delivery')
    db.commit()
    return report, run


def completion():
    return AICompletionResult(payload={'findings': [{'summary': 'Synthetic', 'citations': ['S1']}]},
        provider='openai_compatible', model='synthetic', latency_ms=100, prompt_tokens=80,
        completion_tokens=20, total_tokens=100, attempt_count=2, prompt_char_count=200, response_char_count=80)


def test_stage_commit_replays_exact_completion_and_usage(db_session):
    report, run = setup_report(db_session)
    args = dict(task_run_id=run.id, report_id=report.id, operation_scope='evidence:0', request_fingerprint='a'*64)
    assert not has_report_stage_completion(db_session, task_run_id=run.id, operation_scope='evidence:0')
    assert load_report_stage_completion(db_session, **args) is None
    store_report_stage_completion(db_session, **args, completion=completion())
    db_session.commit()
    assert load_report_stage_completion(db_session, **args) == completion()
    assert has_report_stage_completion(db_session, task_run_id=run.id, operation_scope='evidence:0')
    store_report_stage_completion(db_session, **args, completion=completion())
    db_session.commit()
    assert len(list(db_session.scalars(select(AIReportStageArtifact)))) == 1


@pytest.mark.parametrize('mutation', ['fingerprint', 'report', 'canceled', 'terminal'])
def test_stage_replay_rechecks_binding_status_and_original_inputs(db_session, mutation):
    report, run = setup_report(db_session)
    args = dict(task_run_id=run.id, report_id=report.id, operation_scope='evidence:0', request_fingerprint='a'*64)
    store_report_stage_completion(db_session, **args, completion=completion())
    db_session.commit()
    if mutation == 'fingerprint':
        args['request_fingerprint'] = 'b'*64
    elif mutation == 'report':
        args['report_id'] = uuid.uuid4()
    elif mutation == 'canceled':
        run.metadata_json = {**run.metadata_json, 'cancel_requested_at': datetime.now(timezone.utc).isoformat()}
    else:
        run.status = 'ready'
        run.finished_at = datetime.now(timezone.utc)
    db_session.commit()
    with pytest.raises(AIIntegrationError) as caught:
        load_report_stage_completion(db_session, **args)
    assert caught.value.provider_io_outcome == 'not_sent'
    assert not caught.value.retryable


def test_artifact_rollback_never_claims_completion(db_session):
    report, run = setup_report(db_session)
    args = dict(task_run_id=run.id, report_id=report.id, operation_scope='evidence:0', request_fingerprint='a'*64)
    store_report_stage_completion(db_session, **args, completion=completion())
    db_session.rollback()
    assert load_report_stage_completion(db_session, **args) is None


@pytest.mark.parametrize('field,value', [('attempt_count', 0), ('attempt_count', None),
    ('latency_ms', None), ('prompt_char_count', None), ('response_char_count', -1),
    ('total_tokens', 2_147_483_648), ('completion_tokens', True)])
def test_corrupt_stage_usage_never_understates_attempts(db_session, field, value):
    report, run = setup_report(db_session)
    args = dict(task_run_id=run.id, report_id=report.id, operation_scope='evidence:0', request_fingerprint='a'*64)
    store_report_stage_completion(db_session, **args, completion=completion())
    db_session.commit()
    artifact = db_session.get(AIReportStageArtifact, (run.id, 'evidence:0'))
    artifact.completion_json = {**artifact.completion_json, field: value}
    db_session.commit()
    with pytest.raises(AIIntegrationError, match='unavailable'):
        load_report_stage_completion(db_session, **args)


def test_nullable_model_and_optional_usage_are_valid(db_session):
    from dataclasses import replace
    report, run = setup_report(db_session)
    args = dict(task_run_id=run.id, report_id=report.id, operation_scope='evidence:0', request_fingerprint='a'*64)
    saved = replace(completion(), model=None, prompt_tokens=None, completion_tokens=None, total_tokens=None)
    store_report_stage_completion(db_session, **args, completion=saved)
    db_session.commit()
    assert load_report_stage_completion(db_session, **args) == saved


def test_loaded_stage_with_unstorable_unicode_blocks_replay(db_session):
    report, run = setup_report(db_session)
    args = dict(task_run_id=run.id, report_id=report.id, operation_scope='evidence:0', request_fingerprint='a'*64)
    store_report_stage_completion(db_session, **args, completion=completion())
    db_session.commit()
    artifact = db_session.get(AIReportStageArtifact, (run.id, 'evidence:0'))
    # Simulate unsafe loaded history; current pruning triggers also reject a new
    # NUL-bearing artifact. Do not disable that independent database protection.
    artifact.completion_json = {**artifact.completion_json, 'payload': {'findings': [], 'extra': '\x00'}}
    with db_session.no_autoflush, pytest.raises(AIIntegrationError, match='unavailable') as caught:
        load_report_stage_completion(db_session, **args)
    assert caught.value.provider_io_outcome == 'not_sent'
    assert caught.value.retryable is False
