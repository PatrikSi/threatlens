import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIReportStageArtifact
from app.models.report import Report
from app.models.report_generation_lease import ReportGenerationLease
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.services import report_generation
from app.services.ai_ops import start_ai_task_run
from app.services.ai_report_stage_artifacts import store_report_stage_completion, load_report_stage_completion
from app.services.ai_report_workflow import defer_report_workflow
from app.services.ai_workflow_dispatch import AIWorkflowDeferred
from app.services.report_execution import claim_report_generation
from tests.unit.test_ai_report_stage_artifacts import setup_report, completion


def owned_report(db):
    report, run = setup_report(db)
    report.status = 'queued'
    db.commit()
    claim = claim_report_generation(db, report_id=report.id, lease_token='owner', lease_seconds=120)
    report.status = 'running'
    db.commit()
    assert claim.status == 'claimed'
    return report, run, claim


def test_deferred_report_keeps_artifacts_and_resumes_same_run_under_new_lease(db_session):
    report, run, claim = owned_report(db_session)
    args = dict(task_run_id=run.id, report_id=report.id, operation_scope='evidence:0', request_fingerprint='a'*64)
    store_report_stage_completion(db_session, **args, completion=completion())
    section = ReportSection(report_id=report.id, section_key='executive_summary', title='Summary', position=1,
                            status='ready', body_markdown='Completed grounded section [S1].')
    db_session.add(section)
    db_session.commit()
    result = defer_report_workflow(db_session, report_id=report.id, run_id=run.id,
        lease_token='owner', generation_fence=claim.generation_fence,
        reason='provider_token_budget', retry_after_seconds=300)
    assert result == {'status': 'queued', 'reason': 'provider_token_budget'}
    db_session.expire_all()
    assert run.status == report.status == 'queued'
    assert section.status == 'ready'
    assert run.finished_at is None
    assert run.celery_task_id == 'report-delivery'
    assert (run.dispatch_next_attempt_at - datetime.now(timezone.utc)).total_seconds() > 280
    assert db_session.get(ReportGenerationLease, report.id).lease_token is None
    assert db_session.get(AIReportStageArtifact, (run.id, 'evidence:0')) is not None
    start_ai_task_run(db_session, run_id=run.id, celery_task_id='report-delivery')
    next_claim = claim_report_generation(db_session, report_id=report.id, lease_token='next-owner', lease_seconds=120)
    assert next_claim.status == 'claimed'
    db_session.commit()
    assert load_report_stage_completion(db_session, **args) == completion()


def test_lost_report_lease_cannot_reset_current_generation_to_queued(db_session):
    report, run, claim = owned_report(db_session)
    result = defer_report_workflow(db_session, report_id=report.id, run_id=run.id,
        lease_token='stale-owner', generation_fence=claim.generation_fence,
        reason='provider_concurrency_limit', retry_after_seconds=30)
    assert result == {'status': 'skipped', 'reason': 'ownership_lost'}
    db_session.expire_all()
    assert run.status == report.status == 'running'
    assert db_session.get(ReportGenerationLease, report.id).lease_token == 'owner'


def test_cancellation_wins_over_report_deferral(db_session):
    report, run, claim = owned_report(db_session)
    run.metadata_json = {**run.metadata_json, 'cancel_requested_at': datetime.now(timezone.utc).isoformat()}
    db_session.commit()
    result = defer_report_workflow(db_session, report_id=report.id, run_id=run.id,
        lease_token='owner', generation_fence=claim.generation_fence,
        reason='provider_token_budget', retry_after_seconds=30)
    assert result['status'] == 'skipped'
    db_session.expire_all()
    assert run.status != 'queued'
    assert run.metadata_json['cancel_requested_at']


def test_generation_does_not_classify_capacity_deferral_as_failure(db_session, monkeypatch):
    report, run = setup_report(db_session)
    report.status = 'queued'
    report.prompt_config_json = {'objective': 'Synthetic report'}
    report.sections_config_json = [{'key': 'executive_summary', 'title': 'Summary'}]
    report.context_window_tokens = 8192
    report.source_count = report.included_source_count = 1
    now = datetime.now(timezone.utc)
    db_session.add(ReportSourceItem(report_id=report.id, citation_key='S1', included=True, rank=1,
        title_snapshot='Synthetic', feed_name_snapshot='Feed', url_snapshot='https://example.test/one',
        first_seen_at_snapshot=now, tags_snapshot_json=[], iocs_snapshot_json=[],
        evidence_text='Synthetic evidence', estimated_tokens=10))
    section = ReportSection(report_id=report.id, section_key='executive_summary', title='Summary',
                            position=1, status='ready', body_markdown='Saved section [S1].')
    db_session.add(section)
    db_session.commit()
    monkeypatch.setattr(report_generation, 'load_active_ai_settings', lambda *a, **kw: SimpleNamespace(
        ai_enabled=True, ai_configured=True, reporting_enabled=True, report_context_window_tokens=8192,
        report_reserved_output_tokens=1200, report_context_safety_percent=15, report_source_token_cap=700,
        report_max_model_calls=20, provider_type='openai_compatible', model='synthetic'))
    def deferred(*args, **kwargs):
        raise AIWorkflowDeferred('provider_token_budget', 300)
    monkeypatch.setattr(report_generation, '_synthesize_evidence_batches', deferred)
    with pytest.raises(AIWorkflowDeferred):
        report_generation.generate_report(db_session, report_id=report.id, task_run_id=run.id)
    db_session.expire_all()
    assert report.status == 'queued'
    assert report.generation_stage == 'waiting_for_capacity'
    assert report.error is None and report.error_code is None
    assert run.finished_at is None
    assert section.status == 'ready'
