"""Cancellation must not turn uncertain paid work into a safe-to-retry receipt."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.ai_workflow import AIWorkflowDispatch
from app.services import ai_ops
from app.services.ai_provider_attempts import reserve_ai_provider_attempt, settle_ai_provider_attempt
from app.services.ai_task_cancellation import reconcile_canceled_ai_tasks
from app.services.ai_telemetry_data_policy import (
    cancel_ai_task_run_for_data_access,
    capture_ai_usage_event_data_access,
)
from app.services.ai_workflow_dispatch import defer_ai_workflow_run
from app.services.ai_workflow_publication import claim_publication
from tests.unit.test_ai_cancellation_recovery import access
from tests.unit.test_ai_workflow_durability import item


def test_queued_cancellation_preserves_ambiguous_receipt_and_usage(db_session, monkeypatch):
    db = db_session
    row = item(db, "cancel-ambiguous-" + uuid.uuid4().hex)
    run = ai_ops.queue_ai_task_run(
        db, task_type="item_enrichment", trigger_source="manual", item_id=row.id
    )
    run_id = run.id
    ai_ops.start_ai_task_run(db, run_id=run_id, celery_task_id="ambiguous-delivery")
    context = access(db)
    reservation = reserve_ai_provider_attempt(
        db, task_run_id=run_id, feature_type="item_enrichment", item_id=row.id,
        daily_brief_id=None, report_id=None, operation_scope="item_enrichment",
        attempt_number=1, max_attempts=3, requested_max_tokens=4096,
        request_fingerprint="a" * 64, iam_revision=1,
        data_policy_revision=context.policy_revision, data_policy_mode=context.mode,
    )
    receipt = settle_ai_provider_attempt(
        db, receipt_id=reservation.receipt_id, request_fingerprint="a" * 64,
        state="ambiguous", io_outcome="ambiguous", retryable=False,
        reservation_generation=reservation.reservation_generation,
    )
    usage = AIUsageEvent(
        feature_type="item_enrichment", item_id=row.id, task_run_id_snapshot=run_id,
        success=False, provider="openai_compatible", model="synthetic",
        provider_id=uuid.uuid4(), provider_version=2, provider_name="Synthetic provider",
        failure_category="request_deadline", provider_io_outcome="ambiguous",
        prompt_tokens=17, completion_tokens=None, total_tokens=None,
        latency_ms=30000, error="Synthetic deadline after request transmission",
    )
    db.add(usage)
    capture_ai_usage_event_data_access(db, event=usage, task_run_id=run_id)
    # Legacy/restored queued history may already contain an uncertain paid attempt.
    assert defer_ai_workflow_run(db, run_id=run_id, reason="legacy_recovery", retry_after_seconds=30)
    db.commit()
    receipt_id, usage_id = receipt.id, usage.id
    before_receipt = dict(db.execute(select(AIProviderAttemptReceipt.__table__).where(
        AIProviderAttemptReceipt.id == receipt_id
    )).mappings().one())
    before_usage = dict(db.execute(select(AIUsageEvent.__table__).where(
        AIUsageEvent.id == usage_id
    )).mappings().one())
    monkeypatch.setattr(ai_ops, "_load_live_task_snapshot", lambda: (False, [], [], [], []))
    revocations = []
    monkeypatch.setattr(ai_ops.celery_app.control, "revoke", lambda delivery, **kwargs: revocations.append(delivery))

    cancel_ai_task_run_for_data_access(db, run_id=run_id, actor_user_id=None, data_access=context)
    db.commit()
    assert reconcile_canceled_ai_tasks(db) == 0
    assert claim_publication(db, run_id=run_id, now=datetime.now(timezone.utc), broker_ids=set()) is None
    db.commit()
    db.expire_all()

    run = db.get(AITaskRun, run_id)
    assert run.status == "skipped" and run.reason == "canceled" and run.finished_at is not None
    assert db.get(AIWorkflowDispatch, run_id).state == "complete"
    assert revocations == ["ambiguous-delivery"]
    assert dict(db.execute(select(AIProviderAttemptReceipt.__table__).where(
        AIProviderAttemptReceipt.id == receipt_id
    )).mappings().one()) == before_receipt
    assert dict(db.execute(select(AIUsageEvent.__table__).where(
        AIUsageEvent.id == usage_id
    )).mappings().one()) == before_usage
