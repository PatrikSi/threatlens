import uuid
from datetime import datetime, timedelta, timezone

from app.models.report import Report


def test_analyst_cannot_retry_or_delete_another_users_report(
    client,
    db_session,
    seed_users,
    auth_headers,
):
    now = datetime.now(timezone.utc)
    report = Report(
        id=uuid.uuid4(),
        owner_user_id=seed_users["admin"].id,
        title="Administrator report",
        report_type="custom",
        status="error",
        trigger_source="manual",
        generation_stage="failed",
        period_start=now - timedelta(days=7),
        period_end=now,
        filters_json={},
        prompt_config_json={},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
    )
    db_session.add(report)
    db_session.commit()

    retry_response = client.post(
        f"/reports/{report.id}/retry",
        headers=auth_headers["analyst"],
    )
    delete_response = client.delete(
        f"/reports/{report.id}",
        headers=auth_headers["analyst"],
    )

    assert retry_response.status_code == 403
    assert delete_response.status_code == 403
    assert retry_response.json()["detail"] == "You can only retry or delete reports that you generated."
    assert db_session.get(Report, report.id) is not None
