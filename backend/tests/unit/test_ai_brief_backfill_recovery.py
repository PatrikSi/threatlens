import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIWorkflowDispatch
from app.services.ai_integration import AIDailyBriefGenerationResult
from app.services.ai_ops import (
    _finish_reconciled_stale_run,
    queue_ai_task_run,
    start_ai_task_run,
)
from app.services.ai_ops_common import AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation
from app.tasks import ai_brief_tasks


REFERENCE = datetime(2026, 7, 3, 23, 58, tzinfo=timezone.utc)


def _interrupted_child(db, *, receipt_state=None, ready=False, claim=True, queued_parent=False):
    parent = queue_ai_task_run(
        db, task_type="reprocess", trigger_source="manual", target_count=1,
        metadata={"scope": "daily_brief_backfill", "days": 1,
                  "backfill_reference_time": REFERENCE.isoformat()},
    )
    if not queued_parent:
        start_ai_task_run(db, run_id=parent.id, celery_task_id="lost-worker-delivery")
    child = queue_ai_task_run(
        db, task_type="daily_brief", trigger_source="manual", parent_run_id=parent.id,
        metadata={"scope": "daily_brief_backfill", "brief_date": "2026-07-03",
                  "reference_time": REFERENCE.isoformat(), "attempt": 1,
                  AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY: False},
    )
    start_ai_task_run(db, run_id=child.id, celery_task_id="lost-worker-delivery")
    brief = AIDailyBrief(
        brief_date=REFERENCE.date(), status="ready" if ready else "pending",
        window_start=REFERENCE - timedelta(days=1), window_end=REFERENCE,
        updated_at=REFERENCE, generated_at=REFERENCE if ready else None,
        title="Recovered briefing" if ready else None,
        brief_text="Persisted provider result" if ready else None,
        model="synthetic-model", prompt_tokens=20, completion_tokens=30, total_tokens=50,
    )
    db.add(brief)
    db.flush()
    child.daily_brief_id = brief.id
    if claim:
        child.metadata_json = {
            **child.metadata_json,
            "provider_claim": {"resource_type": "daily_brief", "resource_id": str(brief.id),
                               "updated_at": REFERENCE.isoformat()},
        }
    if receipt_state:
        state = "failed" if receipt_state in {"not_sent", "retryable_response"} else receipt_state
        outcome = ("not_sent" if state == "voided" or receipt_state == "not_sent"
                   else "response_received" if state in {"failed", "succeeded"} else state)
        retryable = None if state == "reserved" else state in {"voided", "failed"}
        db.add(AIProviderAttemptReceipt(
            operation_id=uuid.uuid4(), attempt_number=1, request_fingerprint="a" * 64,
            task_run_id_snapshot=child.id, feature_type="daily_brief", resource_type="daily_brief",
            resource_id=brief.id, max_attempts=3, requested_max_tokens=128,
            iam_revision=1, data_policy_revision=1, data_policy_mode="disabled",
            state=state, io_outcome=outcome, retryable=retryable,
            next_max_tokens=128 if state == "failed" else None,
            settled_at=None if state == "reserved" else REFERENCE,
            pre_io_failure_count=1 if state == "voided" else 0,
            last_pre_io_failure_at=REFERENCE if state == "voided" else None,
        ))
    db.commit()
    return parent, child, brief


def _reconcile_lost(db, run):
    lock_data_policy_revision_for_derivation(db)
    result = _finish_reconciled_stale_run(
        db, run=run, snapshot_available=True, stale_reason="stale_task_lost",
        stale_error="Synthetic worker loss after an interrupted child",
    )
    db.commit()
    return result


def _worker_stubs(db, monkeypatch):
    calls = []

    @contextmanager
    def session_override():
        yield db

    @contextmanager
    def acquired_lock():
        yield True

    def generate(_db, *, reference_time, task_run_id, **_kwargs):
        calls.append((reference_time, task_run_id))
        return AIDailyBriefGenerationResult(
            brief=None, status="ready", reason=None, items_considered=0, items_selected=0,
        )

    monkeypatch.setattr(ai_brief_tasks, "db_session", session_override)
    monkeypatch.setattr(ai_brief_tasks, "daily_ai_brief_lock", acquired_lock)
    monkeypatch.setattr(ai_brief_tasks, "load_active_ai_settings", lambda *_args, **_kwargs: SimpleNamespace(
        ai_enabled=True, ai_configured=True, daily_brief_enabled=True,
        daily_brief_history_limit=7, model="synthetic-model",
    ))
    monkeypatch.setattr(ai_brief_tasks, "run_daily_brief_generation", generate)
    return calls


def _deliver(parent):
    return ai_brief_tasks.backfill_daily_ai_briefs.apply(
        kwargs={"days": 1, "task_run_id": str(parent.id)},
        task_id=parent.celery_task_id or str(uuid.uuid4()), throw=True,
    ).get()


@pytest.mark.parametrize("receipt_state", ["reserved", "ambiguous", "succeeded", "retryable_response", None])
@pytest.mark.parametrize("child_marked_stale", [False, True])
def test_parent_recovery_blocks_interrupted_child_provider_history(
    db_session, monkeypatch, receipt_state, child_marked_stale,
):
    parent, child, _ = _interrupted_child(db_session, receipt_state=receipt_state)
    calls = _worker_stubs(db_session, monkeypatch)
    if child_marked_stale:
        assert _reconcile_lost(db_session, child) == "finished"
        assert child.reason == "stale_task_lost"
    assert _reconcile_lost(db_session, parent) == "finished"
    assert parent.status == "error"
    assert parent.reason == "provider_recovery_blocked"
    assert db_session.get(AIWorkflowDispatch, parent.id).state == "complete"
    _deliver(parent)
    assert calls == []
    assert list(db_session.scalars(select(AITaskRun.id).where(AITaskRun.parent_run_id == parent.id))) == [child.id]


@pytest.mark.parametrize("child_marked_stale", [False, True])
def test_backfill_delivery_rechecks_interrupted_child_before_supersession(db_session, monkeypatch, child_marked_stale):
    parent, child, _ = _interrupted_child(db_session, receipt_state="ambiguous", queued_parent=True)
    calls = _worker_stubs(db_session, monkeypatch)
    if child_marked_stale:
        _reconcile_lost(db_session, child)
    result = _deliver(parent)
    assert result["reason"] == "provider_recovery_blocked"
    assert calls == []
    assert list(db_session.scalars(select(AITaskRun.id).where(AITaskRun.parent_run_id == parent.id))) == [child.id]


@pytest.mark.parametrize("receipt_state,claim", [(None, False), ("voided", True), ("not_sent", True)])
def test_backfill_resumes_original_date_after_proven_no_io(db_session, monkeypatch, receipt_state, claim):
    parent, child, _ = _interrupted_child(db_session, receipt_state=receipt_state, claim=claim)
    calls = _worker_stubs(db_session, monkeypatch)
    assert _reconcile_lost(db_session, parent) == "guarded"
    assert parent.id == child.parent_run_id
    assert parent.celery_task_id != "lost-worker-delivery"
    result = _deliver(parent)
    assert result["status"] == "ready"
    assert result["processed_dates"] == ["2026-07-03"]
    assert len(calls) == 1
    assert calls[0][0] == REFERENCE
    assert calls[0][1] != child.id
    assert child.reason == "superseded_by_redelivery"
    assert parent.processed_count == parent.success_count == 1


@pytest.mark.parametrize("child_marked_stale", [False, True])
def test_backfill_recovers_matching_ready_result_without_new_provider_work(db_session, monkeypatch, child_marked_stale):
    parent, child, brief = _interrupted_child(db_session, receipt_state="succeeded", ready=True)
    calls = _worker_stubs(db_session, monkeypatch)
    if child_marked_stale:
        _reconcile_lost(db_session, child)
    assert _reconcile_lost(db_session, parent) == "guarded"
    result = _deliver(parent)
    assert result["status"] == "ready"
    assert result["processed_dates"] == ["2026-07-03"]
    assert calls == []
    assert child.status == "ready"
    assert child.reason == "completion_recovered"
    assert child.total_tokens == brief.total_tokens == 50
    assert parent.processed_count == parent.success_count == 1
    assert list(db_session.scalars(select(AITaskRun.id).where(AITaskRun.parent_run_id == parent.id))) == [child.id]


def test_ready_brief_from_another_attempt_does_not_prove_safe_backfill_replay(db_session):
    parent, _, brief = _interrupted_child(db_session, receipt_state="succeeded", ready=True)
    brief.updated_at = REFERENCE + timedelta(seconds=1)
    db_session.commit()
    assert _reconcile_lost(db_session, parent) == "finished"
    assert parent.reason == "provider_recovery_blocked"


@pytest.mark.parametrize("ready", [False, True])
def test_backfill_preserves_child_cancellation_during_recovery(db_session, monkeypatch, ready):
    parent, child, _ = _interrupted_child(
        db_session, receipt_state="succeeded" if ready else None, ready=ready, claim=ready,
        queued_parent=True,
    )
    child.metadata_json = {**child.metadata_json, "cancel_requested_at": REFERENCE.isoformat()}
    db_session.commit()
    calls = _worker_stubs(db_session, monkeypatch)
    assert _deliver(parent)["reason"] == "provider_recovery_blocked"
    assert calls == []
    assert child.status != "ready"
    assert list(db_session.scalars(select(AITaskRun.id).where(AITaskRun.parent_run_id == parent.id))) == [child.id]


def test_real_brief_completion_retains_claim_for_backfill_recovery(db_session, monkeypatch):
    from app.core.config import get_settings
    from app.models.article import Article
    from app.models.feed import Feed
    from app.models.item import Item
    from app.schemas.ai import AISettingsUpdate
    from app.services.ai_config import apply_ai_settings_update, get_or_create_ai_settings
    from app.services.ai_integration import run_daily_brief_generation
    from tests.unit.test_ai_integration import _fake_httpx_client_factory, _persist_feed_item

    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")
    get_settings.cache_clear()
    try:
        settings = get_or_create_ai_settings(db_session)
        apply_ai_settings_update(settings, AISettingsUpdate(
            base_url="http://localhost:11434/v1", model="synthetic-model", daily_brief_enabled=True,
        ))
        feed = Feed(id=uuid.uuid4(), name="Synthetic backfill source", url="https://example.com/backfill.xml")
        item = Item(
            id=uuid.uuid4(), feed_id=feed.id, source_guid="backfill-claim",
            url="https://example.com/backfill-claim", canonical_url="https://example.com/backfill-claim",
            title="Synthetic security update", summary="An update needs analyst review.",
            published_at=REFERENCE - timedelta(hours=1), first_seen_at=REFERENCE - timedelta(hours=1),
            dedupe_key="backfill-claim", content_hash="8" * 64, status="content_fetched",
        )
        article = Article(item_id=item.id, final_url=item.url, http_status=200,
                          text="A security update is available for an exposed system.", extraction_method="readable")
        _persist_feed_item(db_session, feed, item, article)
        db_session.commit()
        parent, child, _ = _interrupted_child(db_session, claim=False)
        fake_client = _fake_httpx_client_factory({
            "model": "synthetic-model",
            "choices": [{"finish_reason": "stop", "message": {"content":
                '{"title":"Security update","brief_text":"Review the security update.",'
                '"key_points":["An update is available."],"recommended_actions":["Review the advisory."]}'}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
        })
        monkeypatch.setattr("app.services.ai_integration.build_safe_http_client", fake_client)
        result = run_daily_brief_generation(
            db_session, force=True, reference_time=REFERENCE, task_run_id=child.id, emit_notification=False,
        )
        db_session.commit()
        assert result.status == "ready"
        brief = result.brief
        assert child.metadata_json["provider_claim"]["updated_at"] == brief.updated_at.isoformat()
        assert brief.generated_at != brief.updated_at
        receipt = db_session.scalar(select(AIProviderAttemptReceipt).where(
            AIProviderAttemptReceipt.task_run_id_snapshot == child.id,
        ))
        assert receipt.state == "succeeded"
        calls = _worker_stubs(db_session, monkeypatch)
        assert _reconcile_lost(db_session, parent) == "guarded"
        assert _deliver(parent)["status"] == "ready"
        assert calls == []
        assert child.status == "ready"
        assert child.reason == "completion_recovered"
    finally:
        get_settings.cache_clear()
