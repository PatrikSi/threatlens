import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.models.user import User
from app.services.report_idempotency import (
    build_report_create_identity,
    find_report_create_replay,
    find_report_retry_replay,
)


class _CreatePayload(BaseModel):
    title: str


def test_create_replay_finds_legacy_raw_idempotency_key(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"report-idempotency-{uuid.uuid4().hex}@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    identity = build_report_create_identity(
        "rolling-upgrade-key",
        payload=_CreatePayload(title="Rolling report"),
    )
    assert identity is not None
    now = datetime.now(timezone.utc)
    report = Report(
        id=uuid.uuid4(),
        owner_user_id=user.id,
        title="Rolling report",
        report_type="custom",
        status="queued",
        trigger_source="manual",
        generation_stage="queued",
        request_idempotency_key=identity.legacy_key,
        request_idempotency_key_hash=None,
        request_fingerprint=identity.fingerprint,
        period_start=now - timedelta(days=7),
        period_end=now,
        filters_json={},
        prompt_config_json={},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
    )
    run = AITaskRun(
        id=uuid.uuid4(),
        task_type="report",
        trigger_source="manual",
        status="queued",
        actor_user_id=user.id,
        report_id=report.id,
        metadata_json={},
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(report)
    db_session.flush()
    db_session.add(run)
    db_session.flush()

    replay = find_report_create_replay(
        db_session,
        user_id=user.id,
        identity=identity,
    )

    assert replay == (report, run)

    run.status = "skipped"
    run.reason = "superseded_for_fenced_dispatch"
    run.finished_at = now
    replacement = AITaskRun(
        id=uuid.uuid4(),
        task_type="report",
        trigger_source="manual",
        status="queued",
        actor_user_id=user.id,
        report_id=report.id,
        metadata_json={"supersedes_task_run_id": str(run.id)},
        created_at=now + timedelta(seconds=1),
        updated_at=now + timedelta(seconds=1),
    )
    db_session.add(replacement)
    db_session.flush()

    assert find_report_create_replay(
        db_session,
        user_id=user.id,
        identity=identity,
    ) == (report, replacement)


def test_retry_replay_without_idempotency_identity_is_not_a_replay(db_session):
    assert (
        find_report_retry_replay(
            db_session,
            user_id=uuid.uuid4(),
            report_id=uuid.uuid4(),
            identity=None,
        )
        is None
    )
