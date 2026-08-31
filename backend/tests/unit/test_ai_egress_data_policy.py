from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.models.audit_log import AuditLog
from app.models.data_policy import (
    DataPolicyRoleGrant,
    DataPolicyState,
    HandlingLabel,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.item import Item
from app.models.report import Report
from app.services import ai_egress_data_policy, data_access_policy
from app.services.ai_egress_data_policy import (
    AI_WORKER_PRINCIPAL_ID,
    AIEgressPolicyFence,
    enforce_ai_egress_data_policy,
    mark_ai_egress_provider_io_state,
)
from app.services.ai_provider_client import (
    AI_PROVIDER_IO_NOT_SENT,
    AICompletionResult,
    AIIntegrationError,
)
from app.services.ai_request_runtime import (
    AIProviderAttemptReplayBlockedError,
    run_ai_json_request,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    DATA_ACCESS_RESOURCE_REPORT,
    DataAccessSourceInput,
    merge_data_access_envelope_sources,
)


def test_report_egress_reauthorizes_the_current_owner(
    db_session,
    seed_users,
    monkeypatch,
):
    label = _restricted_label(db_session, seed_users=seed_users)
    _activate_policy(
        db_session,
        seed_users=seed_users,
        monkeypatch=monkeypatch,
        mode="enforced",
    )
    report = _report(db_session, owner_user_id=seed_users["admin"].id)
    _put_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report.id,
        label_id=label.id,
    )
    db_session.commit()

    enforce_ai_egress_data_policy(
        db_session,
        feature_type="report",
        item_id=None,
        daily_brief_id=None,
        report_id=report.id,
    )
    db_session.commit()

    report.owner_user_id = seed_users["viewer"].id
    db_session.add(report)
    db_session.commit()

    with pytest.raises(AIIntegrationError) as captured:
        enforce_ai_egress_data_policy(
            db_session,
            feature_type="report",
            item_id=None,
            daily_brief_id=None,
            report_id=report.id,
        )

    assert captured.value.retryable is False
    assert str(captured.value) == (
        "AI provider request is blocked by the current data access policy."
    )
    logs = _policy_logs(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report.id,
    )
    assert len(logs) == 1
    assert logs[0].action == "data_policy.egress.denied"
    assert logs[0].actor_principal_type == "user"
    assert logs[0].actor_principal_id == seed_users["viewer"].id


def test_daily_brief_audit_uses_unrestricted_ai_worker_and_deduplicates(
    db_session,
    seed_users,
    monkeypatch,
):
    label = _restricted_label(db_session, seed_users=seed_users)
    _activate_policy(
        db_session,
        seed_users=seed_users,
        monkeypatch=monkeypatch,
        mode="audit",
    )
    brief = _daily_brief(db_session)
    _put_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
        label_id=label.id,
    )
    db_session.commit()

    for _attempt in range(2):
        enforce_ai_egress_data_policy(
            db_session,
            feature_type="daily_brief",
            item_id=None,
            daily_brief_id=brief.id,
            report_id=None,
        )

    logs = _policy_logs(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
    )
    assert len(logs) == 1
    log = logs[0]
    assert log.action == "data_policy.egress.would_deny"
    assert log.actor_principal_type == "ai_worker"
    assert log.actor_principal_id == AI_WORKER_PRINCIPAL_ID
    assert log.metadata_json == {
        "decision": "egress_would_deny",
        "surface": "ai_provider.external_io",
        "data_policy_mode": "audit",
        "data_policy_revision": log.metadata_json["data_policy_revision"],
        "data_policy_coverage_version": 1,
        "handling_label_count": 1,
        "handling_label_ids": [str(label.id)],
        "iam_revision": log.metadata_json["iam_revision"],
        "provider_io_state": "not_sent",
        "provider_attempt_count_reserved": 0,
    }


def test_item_enrichment_checks_current_feed_label(
    db_session,
    seed_users,
    monkeypatch,
):
    label = _restricted_label(db_session, seed_users=seed_users)
    _activate_policy(
        db_session,
        seed_users=seed_users,
        monkeypatch=monkeypatch,
        mode="enforced",
    )
    feed = Feed(
        name="Restricted AI feed",
        url=f"https://example.com/{uuid.uuid4()}.xml",
        handling_label_id=label.id,
    )
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        url=f"https://example.com/items/{uuid.uuid4()}",
        title="Sensitive item title",
        dedupe_key=f"ai-egress:{uuid.uuid4()}",
        content_hash=uuid.uuid4().hex * 2,
        first_seen_at=datetime.now(timezone.utc),
    )
    db_session.add(item)
    db_session.commit()

    with pytest.raises(AIIntegrationError) as captured:
        enforce_ai_egress_data_policy(
            db_session,
            feature_type="item_enrichment",
            item_id=item.id,
            daily_brief_id=None,
            report_id=None,
        )

    assert captured.value.retryable is False
    assert "Sensitive item title" not in str(captured.value)
    logs = _policy_logs(
        db_session,
        resource_type="item",
        resource_id=item.id,
    )
    assert len(logs) == 1
    assert logs[0].action == "data_policy.egress.denied"
    assert logs[0].metadata_json["handling_label_ids"] == [str(label.id)]


def test_audit_mode_ineligible_report_owner_is_recorded_as_not_served(
    db_session,
    seed_users,
    monkeypatch,
):
    _activate_policy(
        db_session,
        seed_users=seed_users,
        monkeypatch=monkeypatch,
        mode="audit",
    )
    owner = seed_users["viewer"]
    report = _report(db_session, owner_user_id=owner.id)
    _put_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report.id,
    )
    owner.is_active = False
    db_session.add(owner)
    db_session.commit()

    with pytest.raises(AIIntegrationError) as captured:
        enforce_ai_egress_data_policy(
            db_session,
            feature_type="report",
            item_id=None,
            daily_brief_id=None,
            report_id=report.id,
        )

    assert captured.value.retryable is False
    logs = _policy_logs(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report.id,
    )
    assert len(logs) == 1
    assert logs[0].action == "data_policy.egress.not_served"
    assert logs[0].success is False
    assert logs[0].metadata_json["request_served"] is False
    assert logs[0].metadata_json["provider_io_state"] == "not_sent"


def test_audit_metadata_tracks_reserved_and_completed_provider_io(
    db_session,
    seed_users,
    monkeypatch,
):
    label = _restricted_label(db_session, seed_users=seed_users)
    _activate_policy(
        db_session,
        seed_users=seed_users,
        monkeypatch=monkeypatch,
        mode="audit",
    )
    brief = _daily_brief(db_session)
    _put_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
        label_id=label.id,
    )
    db_session.commit()

    authorization = enforce_ai_egress_data_policy(
        db_session,
        feature_type="daily_brief",
        item_id=None,
        daily_brief_id=brief.id,
        report_id=None,
        request_fingerprint="a" * 64,
    )
    db_session.commit()

    log = db_session.get(AuditLog, authorization.audit_log_id)
    assert log is not None
    assert "request_served" not in log.metadata_json
    assert log.metadata_json["provider_io_state"] == "not_sent"

    mark_ai_egress_provider_io_state(
        db_session,
        authorization=authorization,
        state="reserved",
        attempt_count=1,
    )
    db_session.commit()
    db_session.refresh(log)

    assert "request_served" not in log.metadata_json
    assert log.metadata_json["provider_io_state"] == "reserved"
    assert log.metadata_json["provider_attempt_count_reserved"] == 1

    mark_ai_egress_provider_io_state(
        db_session,
        authorization=authorization,
        state="sent",
        attempt_count=2,
    )
    db_session.commit()
    db_session.refresh(log)

    assert log.metadata_json["request_served"] is True
    assert log.metadata_json["provider_io_state"] == "sent"
    assert log.metadata_json["provider_attempt_count_reserved"] == 2

    for later_state in ("reserved", "ambiguous", "not_sent"):
        mark_ai_egress_provider_io_state(
            db_session,
            authorization=authorization,
            state=later_state,
            attempt_count=3,
        )
        db_session.commit()
        db_session.refresh(log)
        assert log.metadata_json["provider_io_state"] == "sent"
        assert log.metadata_json["request_served"] is True
        assert log.metadata_json["provider_attempt_count_reserved"] == 3


def test_succeeded_receipt_redelivery_does_not_regress_audit_io_truth(
    db_session,
    seed_users,
    monkeypatch,
):
    label = _restricted_label(db_session, seed_users=seed_users)
    _activate_policy(
        db_session,
        seed_users=seed_users,
        monkeypatch=monkeypatch,
        mode="audit",
    )
    brief = _daily_brief(db_session)
    _put_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
        label_id=label.id,
    )
    run = AITaskRun(
        task_type="daily_brief",
        trigger_source="manual",
        status="running",
        daily_brief_id=brief.id,
        metadata_json={},
    )
    db_session.add(run)
    db_session.commit()
    provider_calls = 0

    def provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return AICompletionResult(
            payload={"ok": True},
            provider="openai_compatible",
            model="test-model",
            latency_ms=1,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    _run_daily_brief_provider_request(
        db_session,
        brief_id=brief.id,
        task_run_id=run.id,
        provider=provider,
    )
    log = _policy_logs(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
    )[0]
    assert log.metadata_json["provider_io_state"] == "sent"
    assert log.metadata_json["request_served"] is True

    with pytest.raises(
        AIProviderAttemptReplayBlockedError,
        match="already succeeded",
    ):
        _run_daily_brief_provider_request(
            db_session,
            brief_id=brief.id,
            task_run_id=run.id,
            provider=provider,
        )
    db_session.refresh(log)

    assert provider_calls == 1
    assert log.metadata_json["provider_io_state"] == "sent"
    assert log.metadata_json["request_served"] is True


def test_database_fence_failure_is_retryable_and_rolls_back(
    db_session,
    monkeypatch,
):
    def fail_fence(_db):
        raise OperationalError("SELECT policy", {}, Exception("lock timeout"))

    monkeypatch.setattr(
        ai_egress_data_policy,
        "lock_ai_egress_policy_fence",
        fail_fence,
    )

    with pytest.raises(AIIntegrationError) as captured:
        enforce_ai_egress_data_policy(
            db_session,
            feature_type="report",
            item_id=None,
            daily_brief_id=None,
            report_id=uuid.uuid4(),
        )

    assert captured.value.retryable is True
    assert captured.value.provider_io_outcome == AI_PROVIDER_IO_NOT_SENT
    assert "authorization is unavailable" in str(captured.value)
    assert db_session.scalar(select(text("1"))) == 1


@pytest.mark.parametrize("mode", ["audit", "enforced"])
def test_missing_report_envelope_fails_closed_in_active_modes(
    db_session,
    seed_users,
    monkeypatch,
    mode,
):
    _activate_policy(
        db_session,
        seed_users=seed_users,
        monkeypatch=monkeypatch,
        mode=mode,
    )
    report = _report(db_session, owner_user_id=seed_users["admin"].id)
    db_session.commit()

    with pytest.raises(AIIntegrationError) as captured:
        enforce_ai_egress_data_policy(
            db_session,
            feature_type="report",
            item_id=None,
            daily_brief_id=None,
            report_id=report.id,
        )

    assert captured.value.retryable is True
    assert str(captured.value) == (
        "AI provider request is paused because governed data lineage is missing or "
        "ambiguous. Repair the resource lineage and retry."
    )
    assert str(report.id) not in str(captured.value)


def test_disabled_mode_preserves_missing_lineage_behavior(db_session):
    enforce_ai_egress_data_policy(
        db_session,
        feature_type="report",
        item_id=None,
        daily_brief_id=None,
        report_id=None,
    )


def test_connection_test_acquires_the_policy_fence_without_governed_lineage(
    db_session,
    monkeypatch,
):
    fence_calls = 0

    def lock_fence(_db):
        nonlocal fence_calls
        fence_calls += 1
        return AIEgressPolicyFence(
            iam_revision=7,
            data_policy_revision=9,
            data_policy_mode="audit",
        )

    monkeypatch.setattr(
        ai_egress_data_policy, "lock_ai_egress_policy_fence", lock_fence
    )

    authorization = enforce_ai_egress_data_policy(
        db_session,
        feature_type="connection_test",
        item_id=None,
        daily_brief_id=None,
        report_id=None,
        request_fingerprint="b" * 64,
    )

    assert fence_calls == 1
    assert authorization.iam_revision == 7
    assert authorization.data_policy_revision == 9
    assert authorization.data_policy_mode == "audit"
    assert authorization.request_fingerprint == "b" * 64


def _run_daily_brief_provider_request(
    db_session,
    *,
    brief_id: uuid.UUID,
    task_run_id: uuid.UUID,
    provider,
):
    active = SimpleNamespace(
        request_max_retries=0,
        max_completion_tokens=512,
        provider_type="openai_compatible",
        model="test-model",
    )
    return run_ai_json_request(
        db_session,
        active,
        feature_type="daily_brief",
        messages=[{"role": "user", "content": "governed daily brief"}],
        item_id=None,
        daily_brief_id=brief_id,
        report_id=None,
        task_run_id=task_run_id,
        provider_operation_scope="daily_brief",
        max_completion_tokens=None,
        max_retry_completion_tokens=None,
        max_provider_attempts=None,
        execution_checkpoint=None,
        execution_commit=None,
        enforce_egress_data_policy=enforce_ai_egress_data_policy,
        report_feature_type="report",
        call_ai_json=provider,
        record_task_run_stop_observed=lambda *_args, **_kwargs: None,
        record_usage_event=lambda *_args, **_kwargs: None,
        build_provider_exchange_payload=lambda **_kwargs: {},
        provider_retry_delay_seconds=lambda **_kwargs: 0,
        ai_error_is_retryable=lambda error: error.retryable,
        next_retry_max_completion_tokens=lambda **kwargs: kwargs["current"],
        sleep=lambda _seconds: None,
    )


def _restricted_label(db_session, *, seed_users) -> HandlingLabel:
    label = HandlingLabel(
        key=f"ai-egress-{uuid.uuid4().hex[:12]}",
        name="AI egress restricted",
        description="Restricted label for AI egress tests.",
        color="#B91C1C",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
        created_by_user_id=seed_users["admin"].id,
        updated_by_user_id=seed_users["admin"].id,
    )
    db_session.add(label)
    db_session.flush()
    db_session.add(
        DataPolicyRoleGrant(
            label_id=label.id,
            role_id=SYSTEM_ROLE_IDS["admin"],
            granted_by_user_id=seed_users["admin"].id,
        )
    )
    return label


def _activate_policy(
    db_session,
    *,
    seed_users,
    monkeypatch,
    mode: str,
) -> None:
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.mode = mode
    state.coverage_version = 1
    state.revision += 1
    state.enforced_at = datetime.now(timezone.utc) if mode == "enforced" else None
    state.enforced_by_user_id = seed_users["admin"].id if mode == "enforced" else None
    state.updated_by_user_id = seed_users["admin"].id
    db_session.flush()
    monkeypatch.setattr(
        data_access_policy,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )


def _report(db_session, *, owner_user_id: uuid.UUID) -> Report:
    now = datetime.now(timezone.utc)
    report = Report(
        owner_user_id=owner_user_id,
        title="AI egress report",
        period_start=now - timedelta(days=1),
        period_end=now,
    )
    db_session.add(report)
    db_session.flush()
    return report


def _daily_brief(db_session) -> AIDailyBrief:
    now = datetime.now(timezone.utc)
    brief = AIDailyBrief(
        brief_date=date.today(),
        window_start=now - timedelta(days=1),
        window_end=now,
    )
    db_session.add(brief)
    db_session.flush()
    return brief


def _put_envelope(
    db_session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    label_id: uuid.UUID = UNRESTRICTED_HANDLING_LABEL_ID,
) -> None:
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    merge_data_access_envelope_sources(
        db_session,
        resource_type=resource_type,
        resource_id=resource_id,
        sources=(
            DataAccessSourceInput(
                source_type="test_fixture",
                source_id=str(uuid.uuid4()),
                source_version="1",
                handling_label_id=label_id,
                captured_policy_revision=state.revision,
            ),
        ),
    )


def _policy_logs(
    db_session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
) -> list[AuditLog]:
    return list(
        db_session.scalars(
            select(AuditLog)
            .where(
                AuditLog.resource_type == resource_type,
                AuditLog.resource_id == str(resource_id),
                AuditLog.action.in_(
                    {
                        "data_policy.egress.denied",
                        "data_policy.egress.not_served",
                        "data_policy.egress.would_deny",
                    }
                ),
            )
            .order_by(AuditLog.created_at.asc())
        ).all()
    )
