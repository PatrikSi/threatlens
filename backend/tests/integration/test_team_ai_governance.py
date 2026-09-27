"""Queued destinations remain pinned; approval revocations stop unsent work."""

import uuid
import pytest
from sqlalchemy import delete
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.iam import IAMGroupMembership
from app.models.team_ai_governance import TeamAIGovernance
from app.services.ai_config import load_active_ai_settings
from app.services.ai_provider_client import AIIntegrationError
from app.services.ai_provider_selection import provider_selection_metadata
from app.services.team_ai_destination_runtime import enforce_task_team_destination
from tests.integration.test_team_assessments_api import (
    assessment_setup as setup_fixture,
    _queue,
)

assessment_setup = setup_fixture


def approve(client, setup, headers, **changes):
    return client.put(
        f"/ai/team-governance/{setup[0]['id']}",
        headers=headers,
        json={
            "expected_version": 0,
            "approved_provider_keys": ["legacy"],
            "selected_provider_key": "legacy",
            **changes,
        },
    )


def test_unconfigured_team_preserves_defaults_without_side_effect(
    client, db_session, auth_headers, assessment_setup
):
    response = client.get(
        f"/teams/{assessment_setup[0]['id']}/ai-governance",
        headers=auth_headers["viewer"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 0 and not response.json()["configured"]
    assert (
        db_session.get(TeamAIGovernance, uuid.UUID(assessment_setup[0]["id"])) is None
    )
    queued = _queue(client, assessment_setup, auth_headers["analyst"])
    from app.models.team_item_assessment import TeamItemAssessment

    row = db_session.get(TeamItemAssessment, uuid.UUID(queued["assessment"]["id"]))
    enforce_task_team_destination(
        db_session, task_run_id=row.task_run_id, provider_key="legacy"
    )


def test_approval_requires_admin_and_selection_requires_manager(
    client, auth_headers, assessment_setup
):
    assert approve(client, assessment_setup, auth_headers["analyst"]).status_code == 403
    saved = approve(client, assessment_setup, auth_headers["admin"])
    assert saved.status_code == 200, saved.text
    url = f"/teams/{assessment_setup[0]['id']}/ai-governance"
    assert (
        client.patch(
            url,
            headers=auth_headers["analyst"],
            json={"expected_version": 1, "selected_provider_key": None},
        ).status_code
        == 404
    )
    selected = client.patch(
        url,
        headers=auth_headers["admin"],
        json={"expected_version": 1, "selected_provider_key": None},
    )
    assert selected.status_code == 200 and selected.json()["version"] == 2
    assert (
        client.patch(
            url,
            headers=auth_headers["admin"],
            json={"expected_version": 1, "selected_provider_key": None},
        ).status_code
        == 409
    )


def test_selected_provider_snapshot_is_not_rerouted(
    client, db_session, auth_headers, assessment_setup
):
    providers = []
    for name in ("First", "Second"):
        created = client.post(
            "/ai/providers",
            headers=auth_headers["admin"],
            json={"name": name, "base_url": "https://ai.example/v1", "model": name},
        )
        assert created.status_code == 201, created.text
        providers.append(created.json()["id"])
    keys = [f"profile:{value}" for value in providers]
    assert (
        approve(
            client,
            assessment_setup,
            auth_headers["admin"],
            approved_provider_keys=keys,
            selected_provider_key=keys[0],
        ).status_code
        == 200
    )
    queued = _queue(client, assessment_setup, auth_headers["analyst"])
    from app.models.team_item_assessment import TeamItemAssessment

    row = db_session.get(TeamItemAssessment, uuid.UUID(queued["assessment"]["id"]))
    assert (
        str(
            load_active_ai_settings(
                db_session, feature_type="team_assessment", task_run_id=row.task_run_id
            ).provider_id
        )
        == providers[0]
    )
    db_session.rollback()
    assert (
        approve(
            client,
            assessment_setup,
            auth_headers["admin"],
            expected_version=1,
            approved_provider_keys=keys,
            selected_provider_key=keys[1],
        ).status_code
        == 200
    )
    assert (
        str(
            load_active_ai_settings(
                db_session, feature_type="team_assessment", task_run_id=row.task_run_id
            ).provider_id
        )
        == providers[0]
    )
    assert (
        provider_selection_metadata(
            db_session,
            task_type="team_assessment",
            metadata={"team_id": assessment_setup[0]["id"]},
            parent_run_id=None,
        )["provider_selection"]["provider_id"]
        == providers[1]
    )


@pytest.mark.parametrize("denied_source", ["captured", "current"])
def test_revocation_and_current_label_policy_block_before_io(
    client, db_session, auth_headers, assessment_setup, denied_source
):
    assert approve(client, assessment_setup, auth_headers["admin"]).status_code == 200
    queued = _queue(client, assessment_setup, auth_headers["analyst"])
    from app.models.team_item_assessment import TeamItemAssessment

    row = db_session.get(TeamItemAssessment, uuid.UUID(queued["assessment"]["id"]))
    run_id = row.task_run_id
    enforce_task_team_destination(db_session, task_run_id=run_id, provider_key="legacy")
    db_session.rollback()
    from app.models.feed import Feed
    from app.services.export_job_access import load_export_sources

    captured = load_export_sources(row)[0].captured_label_id
    feed = db_session.get(Feed, assessment_setup[1].feed_id)
    feed.handling_label_id = UNRESTRICTED_HANDLING_LABEL_ID
    db_session.commit()
    denied_label = (
        captured if denied_source == "captured" else UNRESTRICTED_HANDLING_LABEL_ID
    )
    saved = approve(
        client,
        assessment_setup,
        auth_headers["admin"],
        expected_version=1,
        label_destinations={str(denied_label): []},
    )
    assert saved.status_code == 200, saved.text
    with pytest.raises(
        AIIntegrationError, match="current AI destination policy"
    ) as error:
        enforce_task_team_destination(
            db_session, task_run_id=run_id, provider_key="legacy"
        )
    assert error.value.provider_io_outcome == "not_sent"


def test_removed_team_members_cannot_read_policy(
    client, db_session, auth_headers, seed_users, assessment_setup
):
    db_session.execute(
        delete(IAMGroupMembership).where(
            IAMGroupMembership.group_id == assessment_setup[2].id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        )
    )
    db_session.commit()
    assert (
        client.get(
            f"/teams/{assessment_setup[0]['id']}/ai-governance",
            headers=auth_headers["analyst"],
        ).status_code
        == 404
    )


def test_invalid_provider_label_and_selection_are_actionable(
    client, auth_headers, assessment_setup
):
    unknown = f"profile:{uuid.uuid4()}"
    result = approve(
        client,
        assessment_setup,
        auth_headers["admin"],
        approved_provider_keys=[unknown],
        selected_provider_key=unknown,
    )
    assert result.status_code == 422 and "team_ai_destination_missing" in result.text
    result = approve(
        client, assessment_setup, auth_headers["admin"], selected_provider_key=unknown
    )
    assert result.status_code == 422 and "team_ai_destination_unapproved" in result.text
    result = approve(
        client,
        assessment_setup,
        auth_headers["admin"],
        label_destinations={str(uuid.uuid4()): []},
    )
    assert result.status_code == 422


def test_team_override_is_ready_even_when_installation_provider_is_unconfigured(
    client, db_session, auth_headers, assessment_setup
):
    from app.services.ai_config import get_or_create_ai_settings

    created = client.post(
        "/ai/providers",
        headers=auth_headers["admin"],
        json={
            "name": "Approved team model",
            "base_url": "https://ai.example/v1",
            "model": "team-model",
        },
    )
    assert created.status_code == 201, created.text
    key = f"profile:{created.json()['id']}"
    assert (
        approve(
            client,
            assessment_setup,
            auth_headers["admin"],
            approved_provider_keys=[key],
            selected_provider_key=key,
        ).status_code
        == 200
    )
    settings = get_or_create_ai_settings(db_session)
    settings.model = None
    db_session.commit()
    response = _queue(client, assessment_setup, auth_headers["analyst"])
    assert response["assessment"]["status"] == "queued"


def test_empty_approval_stops_new_work_with_explanation(
    client, auth_headers, assessment_setup
):
    assert (
        approve(
            client,
            assessment_setup,
            auth_headers["admin"],
            approved_provider_keys=[],
            selected_provider_key=None,
        ).status_code
        == 200
    )
    response = client.post(
        f"/items/{assessment_setup[1].id}/team-assessment",
        headers=auth_headers["analyst"],
        json={"team_id": assessment_setup[0]["id"], "expected_version": 0},
    )
    assert response.status_code == 409 and "destination policy" in response.text
