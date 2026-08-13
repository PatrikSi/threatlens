import uuid
from datetime import datetime, timedelta, timezone

from app.models.report import Report
from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate


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


def test_admin_can_manage_report_schedule_with_default_filters(
    client,
    db_session,
    auth_headers,
):
    template = ReportTemplate(
        id=uuid.uuid4(),
        builtin_key="schedule-api-test",
        name="Schedule API template",
        description="Integration test template",
        report_type="weekly",
        visibility="shared",
        audience="security_team",
        objective="Summarize material security developments.",
        tone="analytical",
        detail_level="standard",
        use_company_context=True,
        focus_topics_json=[],
        excluded_topics_json=[],
        sections_json=[
            {"key": "executive_summary", "title": "Executive Summary", "enabled": True}
        ],
        default_filters_json={},
    )
    db_session.add(template)
    db_session.commit()
    payload = {
        "template_id": str(template.id),
        "name": "Weekly schedule API test",
        "enabled": False,
        "cadence": "weekly",
        "day_of_week": 1,
        "hour": 8,
        "minute": 15,
        "timezone": "Europe/Prague",
        "window_type": "previous_complete_week",
    }

    create_response = client.post(
        "/reports/schedules", json=payload, headers=auth_headers["admin"]
    )

    assert create_response.status_code == 201
    schedule_id = create_response.json()["id"]
    assert create_response.json()["filters"]["sort"] == "published_at_desc"
    assert create_response.json()["timezone"] == "Europe/Prague"

    payload["name"] = "Updated weekly schedule API test"
    update_response = client.put(
        f"/reports/schedules/{schedule_id}",
        json=payload,
        headers=auth_headers["admin"],
    )
    list_response = client.get(
        "/reports/schedules", headers=auth_headers["admin"]
    )

    assert update_response.status_code == 200
    assert update_response.json()["name"] == payload["name"]
    assert list_response.status_code == 200
    assert any(entry["id"] == schedule_id for entry in list_response.json())

    delete_response = client.delete(
        f"/reports/schedules/{schedule_id}", headers=auth_headers["admin"]
    )

    assert delete_response.status_code == 204
    assert db_session.get(ReportSchedule, uuid.UUID(schedule_id)) is None
