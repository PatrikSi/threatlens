from tests.unit.ai_workflow_test_support import (
    cleanup_ai_workflow_probe as cleanup_ai_workflow_probe,
)
import uuid
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from types import SimpleNamespace
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.ai_task_run import AITaskRun
from app.models.item import Item
from app.models.ai_workflow import AIWorkflowDispatch
from app.services import ai_ops
from app.tasks import feed_task_runtime
from app.tasks.item_ai_tasks import run_generate_item_ai_enrichment
from app.tasks.feed_task_dependencies import ItemAIDependencies
from tests.unit.test_ai_workflow_durability import item


def test_late_started_worker_cannot_defer_recovered_generation(
    database_engine, monkeypatch
):
    with Session(database_engine) as db:
        article = item(db, "late-worker-" + uuid.uuid4().hex)
        item_id = article.id
        run = ai_ops.queue_ai_task_run(
            db, task_type="item_enrichment", trigger_source="manual", item_id=item_id
        )
        run_id = run.id
        db.commit()
    original_claim = feed_task_runtime.claim_item_processing_target
    new_delivery = []

    def pause_before_item_claim(db, **kwargs):
        with Session(database_engine) as reconcile:
            run = reconcile.get(AITaskRun, run_id)
            assert run.status == "running" and run.celery_task_id == "old-delivery"
            run.updated_at = datetime.now(timezone.utc) - timedelta(minutes=20)
            reconcile.commit()
            ai_ops._reconcile_stale_ai_runs(
                reconcile,
                snapshot_available=True,
                workers=[],
                active_tasks=[],
                reserved_tasks=[],
                scheduled_tasks=[],
            )
            run = reconcile.get(AITaskRun, run_id)
            assert run.status == "queued" and run.celery_task_id != "old-delivery"
            new_delivery.append(run.celery_task_id)
            assert ai_ops.start_ai_task_run(
                reconcile,
                run_id=run_id,
                worker_name="replacement",
                celery_task_id=run.celery_task_id,
            )
            reconcile.commit()
        with Session(database_engine) as replacement:
            replacement.scalar(select(Item).where(Item.id == item_id).with_for_update())
            return original_claim(db, **kwargs)

    monkeypatch.setattr(
        feed_task_runtime, "claim_item_processing_target", pause_before_item_claim
    )

    @contextmanager
    def factory():
        with Session(database_engine) as db:
            yield db

    dependencies = ItemAIDependencies(
        db_session=factory,
        queue_ai_enrichment=lambda **kw: True,
        ai_run_stop_reason=lambda run_id: None,
    )
    task = SimpleNamespace(
        request=SimpleNamespace(id="old-delivery", hostname="old-worker")
    )
    result = run_generate_item_ai_enrichment(
        task, str(item_id), task_run_id=str(run_id), dependencies=dependencies
    )
    with Session(database_engine) as db:
        run = db.get(AITaskRun, run_id)
        assert run.celery_task_id == new_delivery[0]
        assert run.status == "running" and run.worker_name == "replacement"
        assert db.get(AIWorkflowDispatch, run_id).state == "running"
        assert result["reason"] == "item_busy"


def test_old_execution_cannot_finish_or_claim_provider_work(database_engine):
    from app.services.ai_execution_ownership import ai_worker_execution
    from app.services.ai_integration import _prepare_provider_claim
    from app.services.ai_workflow_dispatch import defer_ai_workflow_run
    from app.services.ai_provider_attempts import (
        reserve_ai_provider_attempt,
        AIProviderTaskBindingError,
    )
    from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
    import pytest

    with Session(database_engine) as db:
        row = item(db, "continuation-" + uuid.uuid4().hex)
        item_id = row.id
        run = ai_ops.queue_ai_task_run(
            db, task_type="item_enrichment", trigger_source="manual", item_id=item_id
        )
        run_id = run.id
        db.commit()

    @ai_worker_execution
    def worker(task, task_run_id):
        with Session(database_engine) as db:
            ai_ops.start_ai_task_run(
                db, run_id=run_id, celery_task_id="original", worker_name="original"
            )
            db.commit()
        with Session(database_engine) as replacement:
            run = replacement.get(AITaskRun, run_id)
            run.updated_at = datetime.now(timezone.utc) - timedelta(minutes=20)
            replacement.commit()
            ai_ops._reconcile_stale_ai_runs(
                replacement,
                snapshot_available=True,
                workers=[],
                active_tasks=[],
                reserved_tasks=[],
                scheduled_tasks=[],
            )
            run = replacement.get(AITaskRun, run_id)
            # A separate worker invocation has a separate execution context.
            run.status = "running"
            run.worker_name = "replacement"
            replacement.get(AIWorkflowDispatch, run_id).state = "running"
            replacement.commit()
        with Session(database_engine) as db:
            assert (
                _prepare_provider_claim(
                    db,
                    task_run_id=run_id,
                    stage="regression",
                    resource_type="item_ai_enrichment",
                    resource_id=item_id,
                    claim_updated_at=datetime.now(timezone.utc),
                )
                == "superseded_delivery"
            )
            with pytest.raises(AIProviderTaskBindingError, match="superseded"):
                reserve_ai_provider_attempt(
                    db,
                    task_run_id=run_id,
                    feature_type="item_enrichment",
                    item_id=item_id,
                    daily_brief_id=None,
                    report_id=None,
                    operation_scope="item_enrichment",
                    attempt_number=1,
                    max_attempts=2,
                    requested_max_tokens=128,
                    request_fingerprint="a" * 64,
                    iam_revision=1,
                    data_policy_revision=1,
                    data_policy_mode="disabled",
                )
            assert not defer_ai_workflow_run(
                db, run_id=run_id, reason="old_failure", retry_after_seconds=30
            )
            ai_ops.finish_ai_task_run(
                db, run_id=run_id, status="error", reason="old_failure"
            )
            db.commit()
            assert db.get(AITaskRun, run_id).status == "running"
            assert (
                db.scalar(
                    select(AIProviderAttemptReceipt.id).where(
                        AIProviderAttemptReceipt.task_run_id_snapshot == run_id
                    )
                )
                is None
            )

    worker(SimpleNamespace(request=SimpleNamespace(id="original")), str(run_id))
    # Scope cleanup is essential: system reconciliation is not the old worker.
    with Session(database_engine) as db:
        ai_ops.finish_ai_task_run(db, run_id=run_id, status="ready")
        db.commit()
        assert db.get(AITaskRun, run_id).status == "ready"
