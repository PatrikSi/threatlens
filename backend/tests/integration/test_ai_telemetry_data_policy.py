from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_data_access_context
from app.api.routes import ai as ai_routes
from app.core.config import get_settings
from app.main import app
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.audit_log import (
    AuditLog,
    AuditLogDataAccessFeed,
    AuditLogDataAccessLabel,
)
from app.models.data_policy import (
    DataAccessEnvelope,
    DataPolicyState,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.schemas.ai import AILiveStatusResponse, AILiveTaskResponse
from app.services.ai_integration import get_ai_usage_summary
from app.services.ai_ops import (
    AI_STATUS_READY,
    AI_TASK_TYPE_CONNECTION_TEST,
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TRIGGER_MANUAL,
    finish_ai_task_run,
    list_ai_manual_actions,
    list_ai_prompt_history,
    queue_ai_task_run,
)
from app.services.ai_ops_metrics import build_ai_ops_overview
from app.services.ai_persistence import record_usage_event
from app.services.ai_task_runtime import (
    get_ai_db_live_status,
    get_ai_live_status_for_data_access,
)
from app.services.ai_telemetry_data_policy import (
    ai_task_run_would_deny_summary,
    ai_usage_event_would_deny_summary,
    cancel_ai_task_run_for_data_access,
    capture_ai_task_run_data_access,
    get_ai_task_run_detail_for_data_access,
    list_ai_task_runs_for_data_access,
)
from app.services.audit import record_audit
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
    DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
    get_data_access_envelope,
)
from app.services.data_access_policy import (
    DataAccessContext,
    assign_feed_handling_label,
)
from app.services.history_maintenance import prune_application_history


@pytest.fixture()
def ai_enabled_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _context(
    db_session,
    *,
    mode: str,
    principal_id: uuid.UUID,
    eligible: bool = True,
    allowed_label_ids: frozenset[uuid.UUID] | None = None,
) -> DataAccessContext:
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    return DataAccessContext(
        mode=mode,
        policy_revision=state.revision,
        coverage_version=1 if mode != "disabled" else 0,
        principal_type="user",
        principal_id=principal_id,
        principal_eligible=eligible,
        allowed_label_ids=(
            allowed_label_ids
            if allowed_label_ids is not None
            else frozenset({UNRESTRICTED_HANDLING_LABEL_ID})
        ),
    )


@contextmanager
def _override_data_access(context: DataAccessContext) -> Iterator[None]:
    previous = app.dependency_overrides.get(get_data_access_context)
    app.dependency_overrides[get_data_access_context] = lambda: context
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_data_access_context, None)
        else:
            app.dependency_overrides[get_data_access_context] = previous


def _restricted_source(db_session, *, name: str = "Restricted AI source"):
    unique = uuid.uuid4().hex
    label = HandlingLabel(
        key=f"ai-restricted-{unique[:12]}",
        name=f"AI restricted {unique[:8]}",
        description="AI telemetry policy test label.",
        color="#991B1B",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
    )
    db_session.add(label)
    db_session.flush()
    feed = Feed(
        name=name,
        url=f"https://restricted-{unique}.example/feed.xml",
        handling_label_id=label.id,
    )
    db_session.add(feed)
    db_session.flush()
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid=f"ai-policy-{unique}",
        url=f"https://restricted-{unique}.example/item",
        canonical_url=f"https://restricted-{unique}.example/item",
        title="Restricted AI item title",
        summary="Restricted AI item summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key=f"ai-policy:{unique}",
        content_hash=unique.ljust(64, "0")[:64],
        status="content_fetched",
    )
    db_session.add(item)
    db_session.flush()
    return label, feed, item


def _seed_retained_telemetry(db_session, *, actor_user_id: uuid.UUID):
    label, feed, item = _restricted_source(db_session)
    db_session.add_all(
        [
            Article(
                item_id=item.id,
                final_url=item.url,
                http_status=200,
                text="Restricted extracted article text.",
                extraction_method="readable",
            ),
            ItemAIEnrichment(
                item_id=item.id,
                status=AI_STATUS_READY,
                source_hash="a" * 64,
                summary_text="Restricted generated summary.",
                relevance_score=0.95,
                relevance_label="high",
                relevance_reasons_json=["restricted reason"],
                provider="test-provider",
                model="restricted-model",
                generated_at=datetime.now(timezone.utc),
            ),
        ]
    )
    restricted_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=actor_user_id,
        item_id=item.id,
        model="restricted-model",
        metadata={"private_selection": "restricted-run-metadata"},
    )
    restricted_run.celery_task_id = f"restricted-{uuid.uuid4()}"
    db_session.add(restricted_run)
    restricted_usage = record_usage_event(
        db_session,
        feature_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        success=False,
        provider="test-provider",
        model="restricted-model",
        item_id=item.id,
        task_run_id=restricted_run.id,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_ms=25,
        error="restricted provider error",
    )
    manual_audit = record_audit(
        db_session,
        actor_user_id=actor_user_id,
        action="ai.reprocess.queue",
        resource_type="ai_task_run",
        resource_id=str(restricted_run.id),
        metadata={"private_filter": "restricted.example"},
    )
    prompt_audit = record_audit(
        db_session,
        actor_user_id=actor_user_id,
        action="ai.settings.update",
        resource_type="ai_settings",
        metadata={"changed_fields": ["global_instructions"]},
    )

    system_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_CONNECTION_TEST,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=actor_user_id,
        model="system-model",
    )
    system_run.celery_task_id = f"system-{uuid.uuid4()}"
    db_session.add(system_run)
    system_usage = record_usage_event(
        db_session,
        feature_type=AI_TASK_TYPE_CONNECTION_TEST,
        success=True,
        provider="test-provider",
        model="system-model",
        task_run_id=system_run.id,
        total_tokens=1,
        latency_ms=2,
    )
    db_session.commit()
    return {
        "label": label,
        "feed": feed,
        "item": item,
        "restricted_run": restricted_run,
        "restricted_usage": restricted_usage,
        "manual_audit": manual_audit,
        "prompt_audit": prompt_audit,
        "system_run": system_run,
        "system_usage": system_usage,
    }


def _live_snapshot(rows, *, workers=None):
    return True, list(workers or ["worker-policy"]), list(rows), [], []


def test_ai_telemetry_routes_preserve_audit_behavior_and_record_evidence(
    client: TestClient,
    auth_headers,
    seed_users,
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    seeded = _seed_retained_telemetry(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    restricted_run_id = seeded["restricted_run"].id
    system_run_id = seeded["system_run"].id
    audit_context = _context(
        db_session,
        mode="audit",
        principal_id=seed_users["admin"].id,
    )
    live_rows = [
        AILiveTaskResponse(
            worker_name="worker-policy",
            celery_task_id=seeded["restricted_run"].celery_task_id,
            task_name=AI_TASK_TYPE_ITEM_ENRICHMENT,
            state="active",
            run_id=restricted_run_id,
            item_id=seeded["item"].id,
        ),
        AILiveTaskResponse(
            worker_name="worker-policy",
            celery_task_id=seeded["system_run"].celery_task_id,
            task_name=AI_TASK_TYPE_CONNECTION_TEST,
            state="active",
            run_id=system_run_id,
        ),
        AILiveTaskResponse(
            worker_name="worker-policy",
            celery_task_id=seeded["system_run"].celery_task_id,
            task_name=AI_TASK_TYPE_CONNECTION_TEST,
            state="active",
            run_id=system_run_id,
            item_id=seeded["item"].id,
        ),
        AILiveTaskResponse(
            worker_name="worker-policy",
            celery_task_id="unresolved-live",
            task_name=AI_TASK_TYPE_ITEM_ENRICHMENT,
            state="active",
            run_id=None,
            item_id=seeded["item"].id,
        ),
    ]
    snapshot = lambda: _live_snapshot(  # noqa: E731
        live_rows,
        workers=["idle-worker", "worker-policy"],
    )
    monkeypatch.setattr(
        "app.services.ai_task_runtime._load_live_task_snapshot", snapshot
    )
    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", snapshot)

    with _override_data_access(audit_context):
        usage = client.get("/ai/usage", headers=auth_headers["admin"])
        overview = client.get(
            "/ai/ops/overview?days=30", headers=auth_headers["admin"]
        )
        live = client.get("/ai/ops/live", headers=auth_headers["admin"])
        runs = client.get("/ai/ops/runs", headers=auth_headers["admin"])
        detail = client.get(
            f"/ai/ops/runs/{restricted_run_id}", headers=auth_headers["admin"]
        )
        actions = client.get(
            "/ai/ops/manual-actions", headers=auth_headers["admin"]
        )
        prompts = client.get(
            "/ai/ops/prompt-history", headers=auth_headers["admin"]
        )

    for response in (usage, overview, live, runs, detail, actions, prompts):
        assert response.status_code == 200, response.text
    assert usage.json()["total_requests"] == 2
    assert overview.json()["kpis"]["total_requests"] == 2
    assert overview.json()["coverage"]["eligible_items"] == 1
    assert overview.json()["relevance_distribution"]["by_feed"] == [
        {
            "feed_name": seeded["feed"].name,
            "total_items": 1,
            "high_count": 1,
            "medium_count": 0,
            "low_count": 0,
            "average_score": 0.95,
        }
    ]
    assert live.json()["active_count"] == 4
    assert live.json()["workers"] == ["idle-worker", "worker-policy"]
    assert runs.json()["total"] == 2
    assert detail.json()["run"]["metadata"]["private_selection"] == (
        "restricted-run-metadata"
    )
    assert actions.json()[0]["metadata"]["private_filter"] == (
        "restricted.example"
    )
    assert prompts.json()[0]["metadata"]["changed_fields"] == [
        "global_instructions"
    ]

    evidence = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "data_policy.access.would_deny"
            )
        )
    )
    surfaces = {row.metadata_json["surface"] for row in evidence}
    assert {
        "ai.usage.read",
        "ai.ops.overview.read",
        "ai.ops.live.read",
        "ai.ops.runs.read",
        "ai.ops.run_detail.read",
        "ai.ops.manual_actions.read",
    } <= surfaces
    assert all(
        str(seeded["label"].id) in row.metadata_json["handling_label_ids"]
        for row in evidence
    )
    live_evidence = next(
        row
        for row in evidence
        if row.metadata_json["surface"] == "ai.ops.live.read"
    )
    assert str(QUARANTINE_HANDLING_LABEL_ID) in live_evidence.metadata_json[
        "handling_label_ids"
    ]
    assert "ai.ops.prompt_history.read" not in surfaces


def test_ai_telemetry_services_fail_closed_for_enforced_and_ineligible_contexts(
    db_session,
    seed_users,
    monkeypatch: pytest.MonkeyPatch,
):
    seeded = _seed_retained_telemetry(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    restricted_run_id = seeded["restricted_run"].id
    system_run_id = seeded["system_run"].id
    rolling_run = AITaskRun(
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source="auto",
        status="queued",
        item_id=seeded["item"].id,
        metadata_json={"legacy_writer": "missing-lineage"},
    )
    rolling_usage = AIUsageEvent(
        feature_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        success=False,
        item_id=seeded["item"].id,
        error="legacy writer missing lineage",
    )
    db_session.add(rolling_run)
    db_session.flush()
    mismatched_system_usage = AIUsageEvent(
        feature_type=AI_TASK_TYPE_CONNECTION_TEST,
        success=True,
        task_run_id_snapshot=rolling_run.id,
        data_access_scope="system",
    )
    db_session.add_all([rolling_usage, mismatched_system_usage])
    db_session.commit()
    live_rows = [
        AILiveTaskResponse(
            worker_name="worker-policy",
            celery_task_id=seeded["restricted_run"].celery_task_id,
            task_name=AI_TASK_TYPE_ITEM_ENRICHMENT,
            state="active",
            run_id=restricted_run_id,
        ),
        AILiveTaskResponse(
            worker_name="worker-policy",
            celery_task_id=seeded["system_run"].celery_task_id,
            task_name=AI_TASK_TYPE_CONNECTION_TEST,
            state="active",
            run_id=system_run_id,
        ),
        AILiveTaskResponse(
            worker_name="worker-policy",
            celery_task_id="orphan-live",
            task_name=AI_TASK_TYPE_ITEM_ENRICHMENT,
            state="active",
            run_id=None,
        ),
    ]
    monkeypatch.setattr(
        "app.services.ai_task_runtime._load_live_task_snapshot",
        lambda: _live_snapshot(live_rows),
    )

    enforced = _context(
        db_session,
        mode="enforced",
        principal_id=seed_users["admin"].id,
    )
    assert get_ai_usage_summary(db_session, data_access=enforced).total_requests == 1
    run_list = list_ai_task_runs_for_data_access(
        db_session,
        data_access=enforced,
    )
    assert run_list.total == 1
    assert run_list.items[0].id == system_run_id
    assert (
        get_ai_task_run_detail_for_data_access(
            db_session,
            run_id=restricted_run_id,
            data_access=enforced,
        )
        is None
    )
    assert list_ai_manual_actions(db_session, data_access=enforced) == []
    assert [row.id for row in list_ai_prompt_history(db_session, data_access=enforced)] == [
        seeded["prompt_audit"].id
    ]
    enforced_live = get_ai_live_status_for_data_access(
        db_session,
        data_access=enforced,
    )
    assert [task.run_id for task in enforced_live.active_tasks] == [system_run_id]
    overview = build_ai_ops_overview(
        db_session,
        days=30,
        data_access=enforced,
        live_status_loader=lambda session: get_ai_db_live_status(
            session,
            data_access=enforced,
        ),
    )
    assert overview.kpis.total_requests == 1
    assert overview.coverage.eligible_items == 0
    assert overview.relevance_distribution.by_feed == []

    audit_context = _context(
        db_session,
        mode="audit",
        principal_id=seed_users["admin"].id,
    )
    assert ai_task_run_would_deny_summary(
        db_session,
        data_access=audit_context,
    ).handling_label_ids == {
        seeded["label"].id,
        QUARANTINE_HANDLING_LABEL_ID,
    }
    assert ai_usage_event_would_deny_summary(
        db_session,
        data_access=audit_context,
    ).handling_label_ids == {
        seeded["label"].id,
        QUARANTINE_HANDLING_LABEL_ID,
    }

    monkeypatch.setattr(
        "app.services.ai_ops._load_live_task_snapshot",
        lambda: pytest.fail("denied cancellation inspected Celery"),
    )
    assert (
        cancel_ai_task_run_for_data_access(
            db_session,
            run_id=restricted_run_id,
            actor_user_id=seed_users["admin"].id,
            data_access=enforced,
        )
        is None
    )
    visible_feed = Feed(
        name="Visible cancel parent source",
        url=f"https://visible-{uuid.uuid4()}.example/feed.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    db_session.add(visible_feed)
    db_session.flush()
    visible_item = Item(
        feed_id=visible_feed.id,
        url=f"https://visible-{uuid.uuid4()}.example/item",
        title="Visible parent item",
        dedupe_key=f"visible-parent:{uuid.uuid4()}",
        content_hash="b" * 64,
        status="new",
    )
    db_session.add(visible_item)
    db_session.flush()
    parent = queue_ai_task_run(
        db_session,
        task_type="reprocess",
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=seed_users["admin"].id,
    )
    capture_ai_task_run_data_access(
        db_session,
        run_id=parent.id,
        item_ids=(visible_item.id,),
        complete=True,
    )
    inaccessible_child = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=seed_users["admin"].id,
        item_id=seeded["item"].id,
        parent_run_id=parent.id,
    )
    db_session.commit()
    assert (
        cancel_ai_task_run_for_data_access(
            db_session,
            run_id=parent.id,
            actor_user_id=seed_users["admin"].id,
            data_access=enforced,
        )
        is None
    )
    db_session.expire_all()
    assert db_session.get(AITaskRun, restricted_run_id).status == "queued"
    assert db_session.get(AITaskRun, parent.id).status == "queued"
    assert db_session.get(AITaskRun, inaccessible_child.id).status == "queued"

    ineligible = _context(
        db_session,
        mode="audit",
        principal_id=seed_users["admin"].id,
        eligible=False,
        allowed_label_ids=frozenset(),
    )
    assert get_ai_usage_summary(db_session, data_access=ineligible).total_requests == 0
    assert list_ai_task_runs_for_data_access(
        db_session,
        data_access=ineligible,
    ).total == 0
    assert list_ai_manual_actions(db_session, data_access=ineligible) == []
    assert list_ai_prompt_history(db_session, data_access=ineligible) == []
    assert get_ai_live_status_for_data_access(
        db_session,
        data_access=ineligible,
    ).active_tasks == []

    disabled = _context(
        db_session,
        mode="disabled",
        principal_id=seed_users["admin"].id,
    )
    assert get_ai_usage_summary(db_session, data_access=disabled).total_requests == 4
    assert list_ai_task_runs_for_data_access(
        db_session,
        data_access=disabled,
    ).total == 5


@pytest.mark.parametrize("mode", ["disabled", "audit"])
def test_ai_live_compatibility_reconciles_stale_runs_and_keeps_idle_workers(
    db_session,
    seed_users,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    _label, _feed, item = _restricted_source(
        db_session,
        name=f"Compatibility {mode}",
    )
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=seed_users["admin"].id,
        item_id=item.id,
    )
    stale_at = datetime.now(timezone.utc) - timedelta(days=2)
    run.queued_at = stale_at
    run.updated_at = stale_at
    run.celery_task_id = f"stale-{mode}-{uuid.uuid4()}"
    db_session.add(run)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.ai_ops._load_live_task_snapshot",
        lambda: _live_snapshot([], workers=["idle-worker"]),
    )

    response = get_ai_live_status_for_data_access(
        db_session,
        data_access=_context(
            db_session,
            mode=mode,
            principal_id=seed_users["admin"].id,
            allowed_label_ids=frozenset(
                {UNRESTRICTED_HANDLING_LABEL_ID, _label.id}
            ),
        ),
    )

    db_session.expire_all()
    reconciled = db_session.get(AITaskRun, run.id)
    assert reconciled is not None
    assert reconciled.status == "error"
    assert reconciled.reason == "stale_queued_task_unstarted"
    assert response.workers == ["idle-worker"]
    assert response.worker_count == 1
    assert response.queued_count == 0


def test_ai_live_route_refences_policy_after_compatibility_commit(
    client: TestClient,
    auth_headers,
    seed_users,
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    audit_context = _context(
        db_session,
        mode="audit",
        principal_id=seed_users["admin"].id,
    )

    def cross_policy_revision(db, *, data_access):
        _ = data_access
        state = db.get(DataPolicyState, 1)
        assert state is not None
        state.revision += 1
        db.add(state)
        db.commit()
        return AILiveStatusResponse(
            worker_count=0,
            workers=[],
            active_tasks=[],
            reserved_tasks=[],
            scheduled_tasks=[],
            active_count=0,
            reserved_count=0,
            scheduled_count=0,
            queued_count=0,
            oldest_queued_age_seconds=None,
        )

    monkeypatch.setattr(
        "app.api.routes.ai.get_ai_live_status_for_data_access",
        cross_policy_revision,
    )
    with _override_data_access(audit_context):
        response = client.get("/ai/ops/live", headers=auth_headers["admin"])

    assert response.status_code == 409
    assert "authorization changed" in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "loader_name"),
    [
        ("/ai/usage", "get_ai_usage_summary"),
        ("/ai/ops/overview?days=30", "build_ai_ops_overview"),
        ("/ai/ops/manual-actions", "list_ai_manual_actions"),
        ("/ai/ops/prompt-history", "list_ai_prompt_history"),
    ],
)
def test_ai_read_routes_refence_enforced_policy_after_query(
    client: TestClient,
    auth_headers,
    seed_users,
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    loader_name: str,
):
    enforced = _context(
        db_session,
        mode="enforced",
        principal_id=seed_users["admin"].id,
    )
    original_loader = getattr(ai_routes, loader_name)

    def cross_policy_revision(db, *args, **kwargs):
        response = original_loader(db, *args, **kwargs)
        state = db.get(DataPolicyState, 1)
        assert state is not None
        state.revision += 1
        db.add(state)
        db.commit()
        return response

    monkeypatch.setattr(ai_routes, loader_name, cross_policy_revision)
    with _override_data_access(enforced):
        response = client.get(path, headers=auth_headers["admin"])

    assert response.status_code == 409
    assert "authorization changed" in response.json()["detail"]


def test_ai_cancel_route_denies_before_celery_and_audits_allowed_audit_mode(
    client: TestClient,
    auth_headers,
    seed_users,
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    seeded = _seed_retained_telemetry(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    run = seeded["restricted_run"]
    run.celery_task_id = None
    db_session.add(run)
    db_session.commit()
    enforced = _context(
        db_session,
        mode="enforced",
        principal_id=seed_users["admin"].id,
    )
    monkeypatch.setattr(
        "app.services.ai_ops._load_live_task_snapshot",
        lambda: pytest.fail("denied cancellation inspected Celery"),
    )
    with _override_data_access(enforced):
        denied = client.post(
            f"/ai/ops/runs/{run.id}/cancel",
            headers=auth_headers["admin"],
        )
    assert denied.status_code == 404
    db_session.expire_all()
    assert db_session.get(AITaskRun, run.id).status == "queued"

    monkeypatch.setattr(
        "app.services.ai_ops._load_live_task_snapshot",
        lambda: _live_snapshot([]),
    )
    audit_context = _context(
        db_session,
        mode="audit",
        principal_id=seed_users["admin"].id,
    )
    with _override_data_access(audit_context):
        canceled = client.post(
            f"/ai/ops/runs/{run.id}/cancel",
            headers=auth_headers["admin"],
        )
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "skipped"
    assert canceled.json()["reason"] == "canceled"

    cancel_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "ai.run.cancel",
            AuditLog.resource_id == str(run.id),
        )
    )
    assert cancel_audit is not None
    assert set(
        db_session.scalars(
            select(AuditLogDataAccessLabel.label_id).where(
                AuditLogDataAccessLabel.audit_log_id == cancel_audit.id
            )
        )
    ) == {seeded["label"].id}
    cancel_evidence = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "data_policy.access.would_deny",
            AuditLog.metadata_json["surface"].as_string() == "ai.ops.run.cancel",
        )
    )
    assert cancel_evidence is not None
    assert cancel_evidence.metadata_json["affected_count"] == 1
    assert cancel_evidence.metadata_json["handling_label_ids"] == [
        str(seeded["label"].id)
    ]


def test_ai_telemetry_runtime_quarantines_ambiguous_shapes_and_taints_relabels(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    original_label, feed, item = _restricted_source(db_session)
    valid_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=seed_users["admin"].id,
        item_id=item.id,
    )
    valid_audit = record_audit(
        db_session,
        actor_user_id=seed_users["admin"].id,
        action="ai.reprocess.queue",
        resource_type="ai_task_run",
        resource_id=str(valid_run.id),
        metadata={"private_filter": "relabel-sensitive.example"},
    )
    invalid_run = queue_ai_task_run(
        db_session,
        task_type="legacy_unknown",
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=seed_users["admin"].id,
        item_id=item.id,
        metadata={"private": "ambiguous"},
    )
    finish_ai_task_run(
        db_session,
        run_id=invalid_run.id,
        status=AI_STATUS_READY,
    )
    invalid_usage = record_usage_event(
        db_session,
        feature_type="legacy_unknown",
        success=True,
        provider="test-provider",
        model="test-model",
        item_id=item.id,
        task_run_id=invalid_run.id,
    )
    manual_audit = record_audit(
        db_session,
        actor_user_id=seed_users["admin"].id,
        action="ai.reprocess.queue",
        resource_type="ai_task_run",
        resource_id=str(invalid_run.id),
        metadata={"private": "ambiguous"},
    )
    db_session.commit()

    run_envelope = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        resource_id=invalid_run.id,
    )
    usage_envelope = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
        resource_id=invalid_usage.id,
    )
    assert run_envelope is not None
    assert usage_envelope is not None
    assert run_envelope.label_ids == {
        original_label.id,
        QUARANTINE_HANDLING_LABEL_ID,
    }
    assert usage_envelope.label_ids == {
        original_label.id,
        QUARANTINE_HANDLING_LABEL_ID,
    }

    replacement_label = HandlingLabel(
        key=f"ai-relabel-{uuid.uuid4().hex[:12]}",
        name="AI relabel target",
        description="Second policy test label.",
        color="#1D4ED8",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
    )
    db_session.add(replacement_label)
    db_session.flush()
    policy_state = db_session.get(DataPolicyState, 1)
    assert policy_state is not None
    assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=replacement_label.id,
        expected_policy_revision=policy_state.revision,
        actor_user_id=seed_users["admin"].id,
    )
    db_session.commit()

    relabeled_run = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        resource_id=invalid_run.id,
    )
    assert relabeled_run is not None
    assert relabeled_run.label_ids == {
        original_label.id,
        replacement_label.id,
        QUARANTINE_HANDLING_LABEL_ID,
    }
    assert set(
        db_session.scalars(
            select(AuditLogDataAccessLabel.label_id).where(
                AuditLogDataAccessLabel.audit_log_id == manual_audit.id
            )
        )
    ) == {
        original_label.id,
        replacement_label.id,
        QUARANTINE_HANDLING_LABEL_ID,
    }
    assert set(
        db_session.scalars(
            select(AuditLogDataAccessFeed.source_feed_id_snapshot).where(
                AuditLogDataAccessFeed.audit_log_id == manual_audit.id
            )
        )
    ) == {feed.id}

    relabel_restricted = _context(
        db_session,
        mode="enforced",
        principal_id=seed_users["admin"].id,
        allowed_label_ids=frozenset({original_label.id}),
    )
    with _override_data_access(relabel_restricted):
        audit_list = client.get(
            "/audit-logs?action=ai.reprocess.queue&resource_id="
            f"{valid_run.id}",
            headers=auth_headers["admin"],
        )
        audit_export = client.get(
            "/audit-logs/export?action=ai.reprocess.queue&resource_id="
            f"{valid_run.id}",
            headers=auth_headers["admin"],
        )
    assert audit_list.status_code == 200, audit_list.text
    assert audit_export.status_code == 200, audit_export.text
    for response in (audit_list, audit_export):
        projected = response.json()["logs"][0]
        assert projected["id"] == str(valid_audit.id)
        assert projected["resource_id"] is None
        assert projected["data_access_redacted"] is True
        assert "relabel-sensitive.example" not in response.text

    db_session.delete(feed)
    db_session.commit()
    retained_run = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        resource_id=invalid_run.id,
    )
    assert retained_run is not None
    assert retained_run.label_ids == relabeled_run.label_ids
    assert set(
        db_session.scalars(
            select(AuditLogDataAccessLabel.label_id).where(
                AuditLogDataAccessLabel.audit_log_id == manual_audit.id
            )
        )
    ) == relabeled_run.label_ids


def test_ai_telemetry_retention_prunes_normalized_lineage(
    db_session,
    seed_users,
    monkeypatch: pytest.MonkeyPatch,
):
    _label, _feed, item = _restricted_source(db_session)
    old = datetime.now(timezone.utc) - timedelta(days=5)
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=seed_users["admin"].id,
        item_id=item.id,
    )
    finish_ai_task_run(
        db_session,
        run_id=run.id,
        status=AI_STATUS_READY,
    )
    usage = record_usage_event(
        db_session,
        feature_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        success=True,
        provider="test-provider",
        model="test-model",
        item_id=item.id,
        task_run_id=run.id,
    )
    audit = record_audit(
        db_session,
        actor_user_id=seed_users["admin"].id,
        action="ai.reprocess.queue",
        resource_type="ai_task_run",
        resource_id=str(run.id),
    )
    run.created_at = old
    run.finished_at = old
    usage.created_at = old
    audit.created_at = old
    db_session.add_all([run, usage, audit])
    db_session.commit()
    run_id = run.id
    usage_id = usage.id
    audit_id = audit.id
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        resource_id=run_id,
    ) is not None
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
        resource_id=usage_id,
    ) is not None

    monkeypatch.setattr(
        "app.services.history_maintenance.settings.ai_task_history_retention_days",
        1,
    )
    monkeypatch.setattr(
        "app.services.history_maintenance.settings.ai_usage_retention_days",
        1,
    )
    monkeypatch.setattr(
        "app.services.history_maintenance.settings.audit_log_retention_days",
        1,
    )
    result = prune_application_history(
        db_session,
        now=datetime.now(timezone.utc),
        batch_size=100,
    )
    assert result.ai_task_runs_deleted == 1
    assert result.ai_usage_events_deleted == 1
    assert result.audit_logs_deleted == 1
    assert db_session.get(AITaskRun, run_id) is None
    assert db_session.get(AIUsageEvent, usage_id) is None
    assert db_session.get(AuditLog, audit_id) is None
    assert db_session.scalar(
        select(DataAccessEnvelope.id).where(
            DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_AI_TASK_RUN,
            DataAccessEnvelope.resource_id == run_id,
        )
    ) is None
    assert db_session.scalar(
        select(DataAccessEnvelope.id).where(
            DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
            DataAccessEnvelope.resource_id == usage_id,
        )
    ) is None
    assert db_session.scalar(
        select(AuditLogDataAccessLabel.audit_log_id).where(
            AuditLogDataAccessLabel.audit_log_id == audit_id
        )
    ) is None
    assert db_session.scalar(
        select(AuditLogDataAccessFeed.audit_log_id).where(
            AuditLogDataAccessFeed.audit_log_id == audit_id
        )
    ) is None


def test_ai_telemetry_retention_keeps_then_prunes_copied_run_ancestor(
    db_session,
    seed_users,
    monkeypatch: pytest.MonkeyPatch,
):
    _label, _feed, item = _restricted_source(db_session)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=5)
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=seed_users["admin"].id,
        item_id=item.id,
    )
    finish_ai_task_run(
        db_session,
        run_id=run.id,
        status=AI_STATUS_READY,
    )
    usage = record_usage_event(
        db_session,
        feature_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        success=True,
        provider="test-provider",
        model="test-model",
        item_id=item.id,
        task_run_id=run.id,
    )
    run.created_at = old
    run.finished_at = old
    usage.created_at = now
    db_session.add_all([run, usage])
    db_session.commit()
    run_id = run.id
    usage_id = usage.id

    monkeypatch.setattr(
        "app.services.history_maintenance.settings.ai_task_history_retention_days",
        1,
    )
    monkeypatch.setattr(
        "app.services.history_maintenance.settings.ai_usage_retention_days",
        30,
    )
    first = prune_application_history(db_session, now=now, batch_size=100)
    assert first.ai_task_runs_deleted == 1
    assert first.ai_usage_events_deleted == 0
    assert db_session.get(AITaskRun, run_id) is None
    assert db_session.get(AIUsageEvent, usage_id) is not None
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        resource_id=run_id,
    ) is not None

    retained_usage = db_session.get(AIUsageEvent, usage_id)
    assert retained_usage is not None
    retained_usage.created_at = old
    db_session.add(retained_usage)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.history_maintenance.settings.ai_usage_retention_days",
        1,
    )
    second = prune_application_history(db_session, now=now, batch_size=100)
    assert second.ai_usage_events_deleted == 1
    assert db_session.get(AIUsageEvent, usage_id) is None
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
        resource_id=usage_id,
    ) is None
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        resource_id=run_id,
    ) is None
