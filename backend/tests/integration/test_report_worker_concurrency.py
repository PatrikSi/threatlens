import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event

from sqlalchemy import delete, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.models.report_generation_lease import ReportGenerationLease
from app.services import ai_ops
from app.services.ai_ops_common import AI_TASK_TYPE_REPORT
from app.tasks import report_tasks


def test_stale_reconciliation_and_exhausted_settlement_share_lock_order(
    database_engine,
    monkeypatch,
):
    report_id = uuid.uuid4()
    run_id = uuid.uuid4()
    owner_token = uuid.uuid4().hex
    session_factory = sessionmaker(
        bind=database_engine,
        autoflush=False,
        autocommit=False,
        class_=Session,
    )
    now = datetime.now(timezone.utc)
    with session_factory.begin() as db:
        report = Report(
            id=report_id,
            title="Concurrent report settlement",
            report_type="custom",
            status="running",
            trigger_source="manual",
            generation_stage="section:findings",
            period_start=now - timedelta(days=1),
            period_end=now,
            filters_json={},
            prompt_config_json={},
            sections_config_json=[],
            metrics_json={},
            coverage_json={},
            generation_lease_token=owner_token,
            generation_lease_expires_at=now - timedelta(seconds=1),
        )
        db.add(report)
        db.flush()
        db.add(
            AITaskRun(
                id=run_id,
                task_type=AI_TASK_TYPE_REPORT,
                trigger_source="manual",
                status="running",
                report_id=report_id,
                celery_task_id="report-task",
                started_at=now - timedelta(minutes=30),
                metadata_json={},
            )
        )
        db.add(
            ReportGenerationLease(
                report_id=report_id,
                generation_fence=4,
                lease_token=owner_token,
                lease_expires_at=now - timedelta(seconds=1),
            )
        )

    guard_reached = Event()
    allow_reconciliation = Event()
    settlement_started = Event()
    original_guard = ai_ops.guard_unfenced_report_generation

    def _paused_guard(*args, **kwargs):
        result = original_guard(*args, **kwargs)
        guard_reached.set()
        if not allow_reconciliation.wait(timeout=5):
            raise TimeoutError("settlement did not reach the competing lock")
        return result

    @contextmanager
    def _settlement_session():
        with session_factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '3s'"))
            settlement_started.set()
            yield db

    monkeypatch.setattr(ai_ops, "guard_unfenced_report_generation", _paused_guard)
    monkeypatch.setattr(report_tasks, "db_session", _settlement_session)

    def _reconcile():
        with session_factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '3s'"))
            run = db.get(AITaskRun, run_id)
            outcome = ai_ops._finish_reconciled_stale_run(
                db,
                run=run,
                snapshot_available=True,
                stale_reason="stale_task_lost",
                stale_error="The report worker disappeared.",
            )
            db.commit()
            return outcome

    def _settle():
        return report_tasks._settle_exhausted_report_infrastructure(
            report_id=report_id,
            run_id=run_id,
            worker_name="exhausted-worker",
            lease_token="different-worker",
            generation_fence=None,
            celery_task_id="report-task",
            retry_count=3,
            phase="starting report generation",
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reconcile_future = executor.submit(_reconcile)
            assert guard_reached.wait(timeout=5)
            settle_future = executor.submit(_settle)
            assert settlement_started.wait(timeout=5)
            time.sleep(0.2)
            allow_reconciliation.set()

            assert reconcile_future.result(timeout=8) == "finished"
            assert settle_future.result(timeout=8)["status"] == "error"

        with session_factory() as db:
            assert db.get(AITaskRun, run_id).status == "error"
            assert db.get(Report, report_id).status == "error"
    finally:
        allow_reconciliation.set()
        with session_factory.begin() as db:
            db.execute(delete(AITaskRun).where(AITaskRun.id == run_id))
            db.execute(delete(Report).where(Report.id == report_id))
