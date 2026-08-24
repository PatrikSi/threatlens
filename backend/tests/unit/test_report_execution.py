import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.report import Report
from app.models.report_generation_lease import ReportGenerationLease
from app.services.report_execution import (
    claim_report_generation,
    fence_report_generation,
    invalidate_stale_report_generation,
    release_report_generation,
    renew_report_generation,
)


def _queued_report() -> Report:
    now = datetime.now(timezone.utc)
    return Report(
        id=uuid.uuid4(),
        title="Lease test",
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


def test_report_generation_lease_fences_stale_workers(db_session):
    report = _queued_report()
    db_session.add(report)
    db_session.commit()

    first = claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-one",
        lease_seconds=600,
    )
    assert first.status == "claimed"
    assert first.generation_fence == 1
    db_session.commit()

    busy = claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-two",
        lease_seconds=600,
    )
    assert busy.status == "busy"
    db_session.rollback()

    lease = db_session.get(ReportGenerationLease, report.id)
    lease.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    report.generation_lease_expires_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    db_session.commit()

    second = claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-two",
        lease_seconds=600,
    )
    assert second.status == "claimed"
    assert second.generation_fence == 2
    db_session.commit()
    assert not renew_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-one",
        generation_fence=first.generation_fence,
        lease_seconds=600,
    )
    assert renew_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-two",
        generation_fence=second.generation_fence,
        lease_seconds=600,
    )
    assert not release_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-one",
        generation_fence=first.generation_fence,
    )
    assert release_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-two",
        generation_fence=second.generation_fence,
    )
    db_session.commit()

    db_session.refresh(report)
    assert report.generation_lease_token is None
    assert report.generation_lease_expires_at is None


def test_report_generation_claim_honors_renewed_legacy_worker_lease(db_session):
    report = _queued_report()
    db_session.add(report)
    db_session.commit()
    first = claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="legacy-worker",
        lease_seconds=600,
    )
    db_session.commit()

    lease = db_session.get(ReportGenerationLease, report.id)
    lease.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    report.generation_lease_expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=10
    )
    db_session.commit()

    contender = claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="new-worker",
        lease_seconds=600,
    )

    assert first.generation_fence == 1
    assert contender.status == "busy"
    assert contender.lease_expires_at == report.generation_lease_expires_at


def test_report_generation_claim_guards_unfenced_legacy_worker(db_session):
    report = _queued_report()
    report.status = "running"
    report.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.add(report)
    db_session.commit()

    guarded = claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="new-worker",
        lease_seconds=600,
        legacy_worker_grace_seconds=3600,
    )
    db_session.commit()

    lease = db_session.get(ReportGenerationLease, report.id)
    db_session.refresh(report)
    expected_token = f"legacy-unfenced:{report.id.hex}"
    assert guarded.status == "busy"
    assert guarded.lease_expires_at is not None
    assert lease is not None
    assert lease.lease_token == expected_token
    assert report.generation_lease_token == expected_token

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    lease.lease_expires_at = expired_at
    report.generation_lease_expires_at = expired_at
    db_session.commit()

    takeover = claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="new-worker",
        lease_seconds=600,
        legacy_worker_grace_seconds=3600,
    )
    assert takeover.status == "interrupted"
    assert takeover.generation_fence == guarded.generation_fence + 1


def test_stale_generation_invalidation_skips_active_and_fences_expired_lease(
    db_session,
):
    report = _queued_report()
    db_session.add(report)
    db_session.commit()
    claim = claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-one",
        lease_seconds=600,
    )
    db_session.commit()

    assert not invalidate_stale_report_generation(db_session, report_id=report.id)
    db_session.rollback()

    lease = db_session.get(ReportGenerationLease, report.id)
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    lease.lease_expires_at = expired_at
    report.generation_lease_expires_at = expired_at
    db_session.commit()

    assert invalidate_stale_report_generation(db_session, report_id=report.id)
    db_session.commit()
    db_session.refresh(lease)
    db_session.refresh(report)
    assert lease.generation_fence == claim.generation_fence + 1
    assert lease.lease_token is None
    assert lease.lease_expires_at is None
    assert report.generation_lease_token is None
    assert report.generation_lease_expires_at is None


def test_report_heartbeat_does_not_wait_on_dirty_report_row(database_engine):
    report = _queued_report()
    report_id = report.id
    try:
        with Session(database_engine) as setup:
            setup.add(report)
            setup.commit()
            claim = claim_report_generation(
                setup,
                report_id=report_id,
                lease_token="worker-one",
                lease_seconds=600,
            )
            setup.commit()

        with (
            Session(database_engine) as generation_db,
            Session(database_engine) as heartbeat_db,
        ):
            current = generation_db.get(Report, report_id)
            current.generation_stage = "evidence_synthesis"
            generation_db.flush()

            heartbeat_db.execute(text("SET LOCAL lock_timeout = '1s'"))
            assert renew_report_generation(
                heartbeat_db,
                report_id=report_id,
                lease_token="worker-one",
                generation_fence=claim.generation_fence,
                lease_seconds=600,
            )
            heartbeat_db.commit()

            assert fence_report_generation(
                generation_db,
                report_id=report_id,
                lease_token="worker-one",
                generation_fence=claim.generation_fence,
                lease_seconds=600,
            )
            generation_db.commit()
    finally:
        _delete_report(database_engine, report_id)


def test_stale_report_output_is_rolled_back_after_takeover(database_engine):
    report = _queued_report()
    report_id = report.id
    try:
        with Session(database_engine) as setup:
            setup.add(report)
            setup.commit()
            first = claim_report_generation(
                setup,
                report_id=report_id,
                lease_token="worker-one",
                lease_seconds=600,
            )
            setup.commit()

        with Session(database_engine) as stale_db:
            stale_report = stale_db.get(Report, report_id)
            stale_report.generation_stage = "stale-output"

            with Session(database_engine) as takeover_db:
                lease = takeover_db.get(ReportGenerationLease, report_id)
                lease.lease_expires_at = datetime.now(timezone.utc) - timedelta(
                    seconds=1
                )
                takeover_report = takeover_db.get(Report, report_id)
                takeover_report.generation_lease_expires_at = (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                )
                takeover_db.commit()
                second = claim_report_generation(
                    takeover_db,
                    report_id=report_id,
                    lease_token="worker-two",
                    lease_seconds=600,
                )
                assert second.status == "claimed"
                takeover_db.commit()

            assert not fence_report_generation(
                stale_db,
                report_id=report_id,
                lease_token="worker-one",
                generation_fence=first.generation_fence,
                lease_seconds=600,
            )
            stale_db.rollback()

        with Session(database_engine) as verification:
            assert verification.get(Report, report_id).generation_stage == "queued"
    finally:
        _delete_report(database_engine, report_id)


def _delete_report(database_engine, report_id: uuid.UUID) -> None:
    with Session(database_engine) as cleanup:
        report = cleanup.get(Report, report_id)
        if report is not None:
            cleanup.delete(report)
            cleanup.commit()
