"""Team privacy is independent of optional handling-label enforcement."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.iam import IAMGroupMembership
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID, UNRESTRICTED_HANDLING_LABEL_ID
from app.services.ai_persistence import record_usage_event
from app.services.ai_telemetry_data_policy import ai_task_run_access_predicate, ai_usage_event_access_predicate
from app.schemas.ai import AILiveStatusResponse, AILiveTaskResponse
from app.services.ai_task_runtime import get_ai_live_status_for_data_access
from tests.integration.test_ai_telemetry_data_policy import _context
from tests.integration.test_team_assessment_worker import accepted_assessment as accepted_assessment


@pytest.mark.parametrize("mode", ["disabled", "audit", "enforced"])
def test_team_task_and_usage_require_current_membership_in_every_policy_mode(
    db_session, accepted_assessment, seed_users, mode,
):
    run = accepted_assessment["run"]
    event = record_usage_event(db_session, feature_type="team_assessment", success=False,
                               provider="openai_compatible", model="test-model", item_id=run.item_id,
                               task_run_id=run.id, error="Private team-related provider diagnostic.")
    db_session.commit()
    context = _context(db_session, mode=mode, principal_id=seed_users["admin"].id,
                       allowed_label_ids=frozenset({QUARANTINE_HANDLING_LABEL_ID, UNRESTRICTED_HANDLING_LABEL_ID}))

    def visible():
        task = db_session.scalar(select(AITaskRun.id).where(AITaskRun.id == run.id, ai_task_run_access_predicate(context)))
        usage = db_session.scalar(select(AIUsageEvent.id).where(AIUsageEvent.id == event.id, ai_usage_event_access_predicate(context)))
        return task, usage

    assert visible() == (run.id, event.id)
    membership = db_session.scalar(select(IAMGroupMembership).where(
        IAMGroupMembership.group_id == accepted_assessment["managers"].id,
        IAMGroupMembership.user_id == seed_users["admin"].id,
    ))
    db_session.delete(membership)
    db_session.commit()
    assert visible() == (None, None)
    context = replace(context, principal_id=seed_users["analyst"].id)
    assert visible() == (run.id, event.id)
    context = replace(context, principal_type="service_account")
    assert visible() == (None, None)


def test_nonmember_admin_cannot_read_team_run_or_failure_metrics(
    client, db_session, accepted_assessment, seed_users, auth_headers,
):
    run = accepted_assessment["run"]
    run.status, run.error, run.finished_at = "error", "Private team diagnostic.", datetime.now(timezone.utc)
    record_usage_event(db_session, feature_type="team_assessment", success=False,
                       provider="openai_compatible", model="test-model", item_id=run.item_id,
                       task_run_id=run.id, error="Private team diagnostic.")
    membership = db_session.scalar(select(IAMGroupMembership).where(
        IAMGroupMembership.group_id == accepted_assessment["managers"].id,
        IAMGroupMembership.user_id == seed_users["admin"].id,
    ))
    db_session.delete(membership)
    db_session.commit()
    assert client.get(f"/ai/ops/runs/{run.id}", headers=auth_headers["admin"]).status_code == 404
    for path in ("/ai/ops/runs", "/ai/ops/overview?days=30", "/ai/ops/runs?only_failures=true", "/ai/usage", "/ai/ops/statistics?days=30"):
        response = client.get(path, headers=auth_headers["admin"])
        assert response.status_code == 200, response.text
        assert "Private team diagnostic" not in response.text
        assert str(run.id) not in response.text


def test_invalid_team_metadata_and_orphaned_usage_fail_closed(db_session, accepted_assessment, seed_users):
    run = accepted_assessment["run"]
    event = record_usage_event(db_session, feature_type="team_assessment", success=False,
                               provider="openai_compatible", model="test-model", item_id=run.item_id, task_run_id=run.id)
    db_session.commit()
    context = _context(db_session, mode="disabled", principal_id=seed_users["analyst"].id)
    run.metadata_json = {**run.metadata_json, "team_id": "not-a-uuid"}
    db_session.commit()
    assert db_session.scalar(select(AITaskRun.id).where(AITaskRun.id == run.id, ai_task_run_access_predicate(context))) is None
    assert db_session.scalar(select(AIUsageEvent.id).where(AIUsageEvent.id == event.id, ai_usage_event_access_predicate(context))) is None
    db_session.delete(run)
    db_session.commit()
    assert db_session.scalar(select(AIUsageEvent.id).where(AIUsageEvent.id == event.id, ai_usage_event_access_predicate(context))) is None


@pytest.mark.parametrize("mode", ["disabled", "audit"])
def test_live_team_metadata_is_filtered_while_legacy_idle_workers_remain_visible(
    db_session, accepted_assessment, seed_users, monkeypatch, mode,
):
    run = accepted_assessment["run"]
    run.celery_task_id = "team-delivery"
    db_session.delete(db_session.scalar(select(IAMGroupMembership).where(
        IAMGroupMembership.group_id == accepted_assessment["managers"].id,
        IAMGroupMembership.user_id == seed_users["admin"].id,
    )))
    db_session.commit()
    tasks = [
        AILiveTaskResponse(worker_name="ai-worker", celery_task_id="team-delivery", task_name="team_assessment",
                           state="active", run_id=run.id, item_id=run.item_id),
        AILiveTaskResponse(worker_name="other-worker", celery_task_id="diagnostic", task_name="connection_test", state="active"),
    ]
    snapshot = AILiveStatusResponse(worker_count=3, workers=["ai-worker", "other-worker", "idle-worker"],
                                    active_tasks=tasks, reserved_tasks=[], scheduled_tasks=[],
                                    active_count=2, reserved_count=0, scheduled_count=0, queued_count=1,
                                    oldest_queued_age_seconds=1)
    monkeypatch.setattr("app.services.ai_ops.get_ai_live_status", lambda _db: snapshot)
    context = _context(db_session, mode=mode, principal_id=seed_users["admin"].id)
    visible = get_ai_live_status_for_data_access(db_session, data_access=context)
    assert [task.task_name for task in visible.active_tasks] == ["connection_test"]
    assert visible.active_count == 1
    assert visible.workers == snapshot.workers
