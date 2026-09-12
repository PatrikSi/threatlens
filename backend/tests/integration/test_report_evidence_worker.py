import pytest

from app.models.report_generation_lease import ReportGenerationLease
from app.services import report_generation
from app.tasks import report_tasks
from tests.unit.test_report_tasks import _report, _task_run, _use_test_session


def test_actual_worker_terminalizes_unverified_snapshot_without_provider_io(db_session, monkeypatch):
    report = _report()
    run = _task_run(db_session, report)
    _use_test_session(monkeypatch, db_session)

    def forbidden(*args, **kwargs):
        pytest.fail('unverified source snapshot reached provider settings or generation')

    for name in ('load_active_ai_settings', '_synthesize_evidence_batches', 'request_ai_json_with_usage'):
        monkeypatch.setattr(report_generation, name, forbidden)
    result = report_tasks.generate_intelligence_report.apply(
        args=[str(report.id), str(run.id)], task_id='report-task',
    ).get()
    db_session.expire_all()
    assert result == {'status': 'error', 'reason': 'source_snapshot_requires_rebuild'}
    assert report.status == run.status == 'error'
    assert run.finished_at is not None
    assert run.reason == report.error_code == 'source_snapshot_requires_rebuild'
    assert 'create a new report from current sources' in report.error
    assert db_session.get(ReportGenerationLease, report.id).lease_token is None
