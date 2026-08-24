import uuid
from datetime import datetime, timedelta, timezone

from app.models.report import Report
from app.services.report_execution import (
    claim_report_generation,
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

    assert claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-one",
        lease_seconds=600,
    )
    db_session.commit()
    assert not claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-two",
        lease_seconds=600,
    )
    db_session.rollback()

    report = db_session.get(Report, report.id)
    report.generation_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    assert claim_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-two",
        lease_seconds=600,
    )
    db_session.commit()
    assert not renew_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-one",
        lease_seconds=600,
    )
    assert renew_report_generation(
        db_session,
        report_id=report.id,
        lease_token="worker-two",
        lease_seconds=600,
    )
    assert not release_report_generation(
        db_session, report_id=report.id, lease_token="worker-one"
    )
    assert release_report_generation(
        db_session, report_id=report.id, lease_token="worker-two"
    )
    db_session.commit()

    db_session.refresh(report)
    assert report.generation_lease_token is None
    assert report.generation_lease_expires_at is None
