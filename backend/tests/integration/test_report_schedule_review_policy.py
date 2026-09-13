"""Editorial policy survives schedule writes, legacy updates and conflicts."""

import uuid

import pytest

from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate


@pytest.mark.parametrize("initial_policy", [None, False, True])
def test_schedule_review_policy_roundtrips_and_legacy_updates_preserve_it(
    client, db_session, auth_headers, initial_policy
):
    template = ReportTemplate(
        name="Editorial policy test",
        visibility="shared",
        sections_json=[{"key": "summary", "title": "Summary", "enabled": True}],
    )
    db_session.add(template)
    db_session.commit()
    payload = {
        "template_id": str(template.id),
        "name": "Editorial policy schedule",
        "enabled": False,
    }
    if initial_policy is not None:
        payload["review_required"] = initial_policy
    created = client.post("/reports/schedules", json=payload, headers=auth_headers["admin"])
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]
    expected = True if initial_policy is None else initial_policy
    assert created.json()["review_required"] is expected
    version = created.json()["resource_version"]

    # Both toggle directions must survive the API response, listing and actual
    # database state. An old client's omitted field must retain either value.
    for required in (False, True, False):
        previous_version = version
        updated = client.put(
            f"/reports/schedules/{schedule_id}",
            json={**payload, "review_required": required},
            headers={**auth_headers["admin"], "If-Match": f'"{version}"'},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["review_required"] is required
        version = updated.json()["resource_version"]
        assert version != previous_version

        stale = client.put(
            f"/reports/schedules/{schedule_id}",
            json={**payload, "review_required": not required},
            headers={**auth_headers["admin"], "If-Match": f'"{previous_version}"'},
        )
        assert stale.status_code == 412, stale.text

        legacy = client.put(
            f"/reports/schedules/{schedule_id}",
            json={key: value for key, value in payload.items() if key != "review_required"},
            headers={**auth_headers["admin"], "If-Match": f'"{version}"'},
        )
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["review_required"] is required
        version = legacy.json()["resource_version"]

        listed = client.get("/reports/schedules", headers=auth_headers["admin"])
        assert listed.status_code == 200, listed.text
        assert next(row for row in listed.json() if row["id"] == schedule_id)["review_required"] is required
        db_session.expire_all()
        assert db_session.get(ReportSchedule, uuid.UUID(schedule_id)).review_required is required
