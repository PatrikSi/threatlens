import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.services.report_dispatch import (
    claim_report_dispatch,
    initialize_report_dispatch,
    list_due_report_dispatches,
    record_report_dispatch_failure,
    record_report_dispatch_success,
    stable_report_task_id,
)
from app.tasks import report_tasks
from app.tasks.report_tasks import create_report_task_run


def _report() -> Report:
    now = datetime.now(timezone.utc)
    return Report(
        title="Dispatch test",
        report_type="custom",
        status="queued",
        trigger_source="manual",
        generation_stage="queued",
        period_start=now - timedelta(days=1),
        period_end=now,
        filters_json={},
        prompt_config_json={},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
    )


def _queued_run(db_session) -> tuple[Report, AITaskRun]:
    report = _report()
    db_session.add(report)
    db_session.flush()
    run = create_report_task_run(
        db_session,
        report=report,
        actor_user_id=None,
        trigger_source="manual",
    )
    db_session.commit()
    return report, run


def _use_test_session(monkeypatch, db_session) -> None:
    @contextmanager
    def _session():
        yield db_session

    monkeypatch.setattr(report_tasks, "db_session", _session)


def test_enqueue_uses_stable_task_id_and_records_publication(
    db_session,
    monkeypatch,
):
    report, run = _queued_run(db_session)
    _use_test_session(monkeypatch, db_session)
    published = []

    def _publish(*, args, task_id):
        published.append((args, task_id))

    monkeypatch.setattr(
        report_tasks.generate_intelligence_report,
        "apply_async",
        _publish,
    )

    task_id = report_tasks.enqueue_report_task(
        report_id=report.id,
        task_run_id=run.id,
    )

    expected_task_id = stable_report_task_id(run.id)
    assert task_id == expected_task_id
    assert published == [([str(report.id), str(run.id)], expected_task_id)]
    db_session.expire_all()
    stored = db_session.get(AITaskRun, run.id)
    assert stored is not None
    assert stored.celery_task_id == expected_task_id
    assert stored.dispatch_published_at is not None
    assert stored.dispatch_attempt_count == 0
    assert stored.dispatch_next_attempt_at is None
    assert stored.dispatch_claim_token is None
    assert stored.dispatch_error is None


def test_enqueue_failure_keeps_durable_work_due_for_retry(
    db_session,
    monkeypatch,
):
    report, run = _queued_run(db_session)
    _use_test_session(monkeypatch, db_session)

    def _fail_publish(**_kwargs):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(
        report_tasks.generate_intelligence_report,
        "apply_async",
        _fail_publish,
    )
    before = datetime.now(timezone.utc)

    task_id = report_tasks.enqueue_report_task(
        report_id=report.id,
        task_run_id=run.id,
    )

    assert task_id is None
    db_session.expire_all()
    stored_report = db_session.get(Report, report.id)
    stored_run = db_session.get(AITaskRun, run.id)
    assert stored_report is not None and stored_report.status == "queued"
    assert stored_run is not None and stored_run.status == "queued"
    assert stored_run.dispatch_attempt_count == 1
    assert stored_run.dispatch_next_attempt_at is not None
    assert stored_run.dispatch_next_attempt_at >= before + timedelta(seconds=14)
    assert "outcome is unknown" in (stored_run.dispatch_error or "")


def test_published_task_is_recoverable_when_metadata_commit_fails(
    db_session,
    monkeypatch,
):
    report, run = _queued_run(db_session)
    _use_test_session(monkeypatch, db_session)
    published = []
    monkeypatch.setattr(
        report_tasks.generate_intelligence_report,
        "apply_async",
        lambda *, args, task_id: published.append((args, task_id)),
    )
    monkeypatch.setattr(
        report_tasks,
        "record_report_dispatch_success",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ConnectionError("database unavailable after publish")
        ),
    )

    task_id = report_tasks.enqueue_report_task(
        report_id=report.id,
        task_run_id=run.id,
    )

    assert task_id == stable_report_task_id(run.id)
    assert len(published) == 1
    db_session.expire_all()
    stored = db_session.get(AITaskRun, run.id)
    assert stored.celery_task_id is None
    assert stored.dispatch_claim_token is not None
    assert stored.dispatch_claim_expires_at is not None
    assert list_due_report_dispatches(
        db_session,
        now=stored.dispatch_claim_expires_at + timedelta(seconds=1),
    ) == [(report.id, run.id)]


def test_dispatch_failures_remain_durable_after_attempt_counter_reaches_cap(
    db_session,
    monkeypatch,
):
    monkeypatch.setenv("REPORT_DISPATCH_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("REPORT_DISPATCH_RETRY_BACKOFF_SECONDS", "10")
    monkeypatch.setenv("REPORT_DISPATCH_RETRY_MAX_BACKOFF_SECONDS", "100")
    get_settings.cache_clear()
    report, run = _queued_run(db_session)
    started = datetime.now(timezone.utc)

    first = claim_report_dispatch(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        now=started,
    )
    assert first.claimed is True
    db_session.commit()
    record_report_dispatch_failure(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        dispatch_token=first.dispatch_token,
        now=started,
    )
    db_session.commit()
    db_session.expire_all()
    stored_run = db_session.get(AITaskRun, run.id)
    assert stored_run is not None
    assert stored_run.dispatch_next_attempt_at == started + timedelta(seconds=10)

    second_at = started + timedelta(seconds=10)
    second = claim_report_dispatch(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        now=second_at,
    )
    assert second.claimed is True
    db_session.commit()
    record_report_dispatch_failure(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        dispatch_token=second.dispatch_token,
        now=second_at,
    )
    db_session.commit()
    db_session.expire_all()

    stored_report = db_session.get(Report, report.id)
    stored_run = db_session.get(AITaskRun, run.id)
    assert stored_report is not None and stored_report.status == "queued"
    assert stored_run is not None and stored_run.status == "queued"
    assert stored_run.dispatch_attempt_count == 2
    assert stored_run.dispatch_next_attempt_at == second_at + timedelta(seconds=20)
    assert "outcome is unknown" in (stored_run.dispatch_error or "")

    third = claim_report_dispatch(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        now=stored_run.dispatch_next_attempt_at,
    )
    assert third.claimed is True


def test_confirmed_dispatch_is_not_republished_while_waiting_for_worker(db_session):
    report, run = _queued_run(db_session)
    now = datetime.now(timezone.utc)
    run.dispatch_next_attempt_at = now - timedelta(seconds=1)
    db_session.add(run)
    db_session.commit()

    assert list_due_report_dispatches(db_session, now=now) == [(report.id, run.id)]

    claim = claim_report_dispatch(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        now=now,
    )
    assert claim.dispatch_token is not None
    db_session.commit()
    assert record_report_dispatch_success(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        dispatch_token=claim.dispatch_token,
        celery_task_id=stable_report_task_id(run.id),
        now=now,
    )
    db_session.commit()
    assert list_due_report_dispatches(db_session, now=now) == []

    stale_at = now + timedelta(days=1)
    assert list_due_report_dispatches(db_session, now=stale_at) == []


def test_accepted_dispatch_is_not_terminalized_by_redrive_failures(
    db_session,
    monkeypatch,
):
    monkeypatch.setenv("REPORT_DISPATCH_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    report, run = _queued_run(db_session)
    now = datetime.now(timezone.utc)
    run.dispatch_published_at = now - timedelta(minutes=10)
    run.dispatch_attempt_count = 2
    run.dispatch_next_attempt_at = now - timedelta(seconds=1)
    db_session.commit()

    claim = claim_report_dispatch(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        now=now,
    )
    assert claim.claimed is True
    db_session.commit()
    assert record_report_dispatch_failure(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        dispatch_token=claim.dispatch_token,
        now=now,
    )
    db_session.commit()

    db_session.refresh(report)
    db_session.refresh(run)
    assert report.status == "queued"
    assert run.status == "queued"
    assert run.dispatch_attempt_count == 2
    assert run.dispatch_next_attempt_at is not None
    assert run.dispatch_next_attempt_at > now
    assert "outcome is unknown" in (run.dispatch_error or "")


def test_stale_dispatch_claim_cannot_record_publication(db_session, monkeypatch):
    monkeypatch.setenv("REPORT_DISPATCH_CLAIM_SECONDS", "10")
    get_settings.cache_clear()
    report, run = _queued_run(db_session)
    started = datetime.now(timezone.utc)
    first = claim_report_dispatch(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        now=started,
    )
    db_session.commit()

    second = claim_report_dispatch(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        now=started + timedelta(seconds=11),
    )
    assert second.claimed is True
    assert second.dispatch_token != first.dispatch_token
    db_session.commit()
    assert not record_report_dispatch_success(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        dispatch_token=first.dispatch_token,
        celery_task_id=stable_report_task_id(run.id),
        now=started + timedelta(seconds=11),
    )
    assert record_report_dispatch_success(
        db_session,
        report_id=report.id,
        task_run_id=run.id,
        dispatch_token=second.dispatch_token,
        celery_task_id=stable_report_task_id(run.id),
        now=started + timedelta(seconds=11),
    )


def test_pending_dispatch_task_reports_partial_progress(monkeypatch):
    first = (uuid.uuid4(), uuid.uuid4())
    second = (uuid.uuid4(), uuid.uuid4())

    @contextmanager
    def _session():
        yield object()

    monkeypatch.setattr(report_tasks, "db_session", _session)
    monkeypatch.setattr(
        report_tasks,
        "list_due_report_dispatches",
        lambda *_args, **_kwargs: [first, second],
    )
    monkeypatch.setattr(
        report_tasks,
        "enqueue_report_task",
        lambda *, task_run_id, **_kwargs: (
            stable_report_task_id(task_run_id) if task_run_id == first[1] else None
        ),
    )

    result = report_tasks.dispatch_pending_report_tasks.run()

    assert result == {"status": "partial", "dispatched": 1, "deferred": 1}


def test_initialize_dispatch_resets_previous_failure_state():
    run = AITaskRun(
        task_type="report",
        trigger_source="manual",
        status="queued",
        dispatch_attempt_count=4,
        dispatch_error="old failure",
    )
    observed_at = datetime.now(timezone.utc)

    initialize_report_dispatch(run, now=observed_at)

    assert run.dispatch_attempt_count == 0
    assert run.dispatch_next_attempt_at == observed_at
    assert run.dispatch_error is None
