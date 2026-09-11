from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from sqlalchemy import select

from app.models.ai_task_run import AITaskRun
from app.services import ai_ops
from app.tasks import ai_brief_tasks, item_ai_tasks
from app.tasks.feed_task_dependencies import ItemAIDependencies


@pytest.fixture
def worker_context(db_session, monkeypatch):
    @contextmanager
    def session():
        yield db_session

    @contextmanager
    def lock():
        yield True

    enqueue = Mock(side_effect=AssertionError("invalid configuration must not queue work"))
    generate = Mock(side_effect=AssertionError("invalid configuration must not call a provider"))
    monkeypatch.setattr(ai_brief_tasks, "db_session", session)
    monkeypatch.setattr(ai_brief_tasks, "daily_ai_brief_lock", lock)
    monkeypatch.setattr(ai_brief_tasks, "run_daily_brief_generation", generate)
    return SimpleNamespace(
        task=SimpleNamespace(request=SimpleNamespace(hostname="test-worker", id=None)),
        dependencies=ItemAIDependencies(
            db_session=session,
            queue_ai_enrichment=enqueue,
            ai_run_stop_reason=lambda _: None,
        ),
        enqueue=enqueue,
        generate=generate,
    )


@pytest.mark.parametrize("worker", ["item_reprocess", "brief", "brief_backfill"])
@pytest.mark.parametrize(
    ("enabled", "code", "expected_status", "expected_reason"),
    [
        (True, "provider_missing", "error", "provider_missing"),
        (True, "provider_disabled", "error", "provider_disabled"),
        (True, "provider_version_changed", "error", "provider_version_changed"),
        (True, "provider_credential_unavailable", "error", "provider_credential_unavailable"),
        (True, "provider_selection_invalid", "error", "provider_selection_invalid"),
        (True, None, "skipped", "ai_not_configured"),
        (False, "provider_disabled", "skipped", "ai_disabled"),
    ],
)
def test_worker_preflight_records_provider_fault_without_starting_work(
    db_session, monkeypatch, worker_context, worker, enabled, code, expected_status, expected_reason
):
    safe_error = "Review the selected provider in AI settings and start a new task."
    active = SimpleNamespace(
        ai_enabled=enabled,
        ai_configured=False,
        daily_brief_enabled=True,
        configuration_error_code=code,
        configuration_error=safe_error if code else None,
    )
    run = ai_ops.queue_ai_task_run(
        db_session,
        task_type=ai_ops.AI_TASK_TYPE_DAILY_BRIEF if worker == "brief" else ai_ops.AI_TASK_TYPE_REPROCESS,
        trigger_source=ai_ops.AI_TRIGGER_MANUAL,
        metadata={"scope": ai_ops.AI_DAILY_BRIEF_BACKFILL_SCOPE} if worker == "brief_backfill" else {},
    )
    db_session.commit()
    expected_feature = "item_enrichment" if worker == "item_reprocess" else "daily_brief"

    def load_settings(_db, *, feature_type, task_run_id):
        assert feature_type == expected_feature
        assert task_run_id == run.id
        return active

    monkeypatch.setattr(item_ai_tasks.ai_config, "load_active_ai_settings", load_settings)
    monkeypatch.setattr(ai_brief_tasks, "load_active_ai_settings", load_settings)
    if worker == "item_reprocess":
        result = item_ai_tasks.run_reprocess_recent_ai_items(
            worker_context.task, 7, 10, task_run_id=str(run.id), dependencies=worker_context.dependencies
        )
        assert result["queued"] == 0
    elif worker == "brief":
        result = ai_brief_tasks.dispatch_daily_ai_brief_generation.run(force=True, task_run_id=str(run.id))
    else:
        result = ai_brief_tasks.backfill_daily_ai_briefs.run(3, task_run_id=str(run.id))

    db_session.expire_all()
    saved = db_session.get(AITaskRun, run.id)
    assert saved.status == expected_status
    assert saved.reason == expected_reason
    assert saved.error == (safe_error if expected_status == "error" else None)
    assert saved.finished_at is not None
    assert result["reason"] == expected_reason
    if expected_status == "error" or worker != "item_reprocess":
        assert result["status"] == expected_status
    assert db_session.scalar(select(AITaskRun.id).where(AITaskRun.parent_run_id == run.id)) is None
    worker_context.enqueue.assert_not_called()
    worker_context.generate.assert_not_called()


def test_item_worker_keeps_error_status_and_message_without_an_enrichment(
    db_session, monkeypatch, worker_context
):
    run = ai_ops.queue_ai_task_run(
        db_session, task_type=ai_ops.AI_TASK_TYPE_ITEM_ENRICHMENT, trigger_source=ai_ops.AI_TRIGGER_MANUAL
    )
    db_session.commit()
    item_id = uuid.uuid4()
    safe_error = "The selected AI provider changed after this task was queued. Start a new task."
    monkeypatch.setattr(
        item_ai_tasks.feed_task_runtime, "claim_item_processing_target", lambda *args, **kwargs: (None, None)
    )
    monkeypatch.setattr(
        item_ai_tasks.ai_integration,
        "run_item_ai_enrichment",
        lambda *args, **kwargs: SimpleNamespace(
            enrichment=None,
            status="error",
            reason="provider_version_changed",
            error=safe_error,
            input_text_chars=0,
            prompt_char_count=None,
            response_char_count=None,
        ),
    )
    result = item_ai_tasks.run_generate_item_ai_enrichment(
        worker_context.task, str(item_id), task_run_id=str(run.id), dependencies=worker_context.dependencies
    )
    db_session.expire_all()
    saved = db_session.get(AITaskRun, run.id)
    assert saved.status == "error"
    assert saved.reason == "provider_version_changed"
    assert saved.error == safe_error
    assert result == {"status": "error", "reason": "provider_version_changed", "item_id": str(item_id)}


def test_daily_brief_worker_keeps_late_configuration_error_without_a_brief(
    db_session, monkeypatch, worker_context
):
    run = ai_ops.queue_ai_task_run(
        db_session, task_type=ai_ops.AI_TASK_TYPE_DAILY_BRIEF, trigger_source=ai_ops.AI_TRIGGER_MANUAL
    )
    db_session.commit()
    monkeypatch.setattr(
        ai_brief_tasks,
        "load_active_ai_settings",
        lambda *args, **kwargs: SimpleNamespace(ai_enabled=True, ai_configured=True, daily_brief_enabled=True, model="test-model"),
    )
    safe_error = "The selected AI provider is disabled. Review AI settings."
    worker_context.generate.side_effect = None
    worker_context.generate.return_value = SimpleNamespace(
        brief=None,
        status="error",
        reason="provider_disabled",
        error=safe_error,
        items_considered=0,
        items_selected=0,
        prompt_char_count=None,
        response_char_count=None,
        integration_event_id=None,
    )
    result = ai_brief_tasks.dispatch_daily_ai_brief_generation.run(force=True, task_run_id=str(run.id))
    db_session.expire_all()
    saved = db_session.get(AITaskRun, run.id)
    assert saved.status == "error"
    assert saved.reason == "provider_disabled"
    assert saved.error == safe_error
    assert result == {"status": "error", "reason": "provider_disabled"}
