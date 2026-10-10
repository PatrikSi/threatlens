from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import func, select

from app.api.routes import team_assessments as routes
from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIWorkflowDispatch
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.iam import IAMGroupMembership
from app.models.investigation import (
    Investigation,
    InvestigationEvidence,
    InvestigationNote,
)
from app.models.team_item_assessment import TeamAssessmentRevision, TeamItemAssessment
from app.services.ai_config import get_or_create_ai_settings
from app.services.secret_storage import decrypt_json
from app.services import team_assessments
from tests.integration.test_investigations_api import _create_item
from tests.integration.test_data_policy_read_coverage import _enable_enforcement
from tests.integration.test_team_ai_context_api import _credential
from tests.integration.test_teams_api import _team


@pytest.fixture()
def assessment_setup(client, db_session, seed_users, auth_headers, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    settings = get_or_create_ai_settings(db_session)
    settings.base_url = "https://api.openai.com"
    settings.model = "fixture-model"
    settings.hunt_suggestions_enabled = True
    db_session.commit()
    team, members, managers = _team(client, db_session, seed_users, auth_headers)
    item = _create_item(db_session)
    monkeypatch.setattr(routes, "publish_ai_workflow", lambda *args, **kwargs: None)
    return team, item, members, managers


def _queue(client, setup, headers, *, version=0):
    team, item, *_ = setup
    response = client.post(
        f"/items/{item.id}/team-assessment",
        json={"team_id": team["id"], "expected_version": version},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return response.json()


def _ready(db, response):
    row = db.get(TeamItemAssessment, uuid.UUID(response["assessment"]["id"]))
    run = db.get(AITaskRun, row.task_run_id)
    run.status = "ready"
    run.finished_at = datetime.now(timezone.utc)
    db.get(AIWorkflowDispatch, run.id).state = "complete"
    row.generated_at = run.finished_at
    row.result_context_version = row.context_version
    row.result_source_encrypted = row.source_encrypted
    row.result_source_version = row.source_version
    row.result_article_id = row.article_id
    row.result_article_retrieved_at = row.article_retrieved_at
    row.result_json = {
        "relevance_score": 0.6,
        "relevance_reasons": ["The source discusses the team's identity priorities."],
        "information_gaps": ["Local activity is unconfirmed."],
        "hunts": [
            {
                "id": uuid.uuid4().hex,
                "title": "Review identity telemetry",
                "hypothesis": "Relevant identity behavior may warrant analyst review.",
                "rationale": "Identity protection is a team priority.",
                "required_logs": ["Authentication logs"],
                "benign_explanations": ["Approved account administration"],
                "information_gaps": ["Log availability has not been confirmed."],
                "evidence": [
                    {
                        "source": "summary",
                        "quote": "A bounded source summary for the investigation snapshot.",
                    }
                ],
                "attack_technique_ids": [],
                "detection_strategy_ids": [],
                "review_status": "suggested",
                "review_note": None,
                "investigation_id": None,
            }
        ],
    }
    db.commit()
    return row, row.result_json["hunts"][0]["id"]


def _review(
    client,
    setup,
    headers,
    hunt_id,
    *,
    version,
    status="accepted",
    note="Checked the primary evidence.",
):
    team, item, *_ = setup
    return client.patch(
        f"/items/{item.id}/team-assessment/hunts/{hunt_id}",
        json={
            "team_id": team["id"],
            "expected_version": version,
            "status": status,
            "note": note,
        },
        headers=headers,
    )


def test_assessment_queue_persists_bounded_delivery_and_credential_snapshots(
    client, db_session, auth_headers, assessment_setup, monkeypatch
):
    def unavailable(*args, **kwargs):
        raise OSError("broker unavailable")

    monkeypatch.setattr(routes, "publish_ai_workflow", unavailable)
    response = _queue(client, assessment_setup, auth_headers["analyst"])
    assessment = response["assessment"]
    assert assessment["version"] == 1
    assert assessment["status"] == "queued"
    assert assessment["result"] is None
    assert response["can_generate"] is True
    row = db_session.get(TeamItemAssessment, uuid.UUID(assessment["id"]))
    run = db_session.get(AITaskRun, row.task_run_id)
    assert run.metadata_json["assessment_id"] == str(row.id)
    assert run.metadata_json["assessment_version"] == row.version
    assert run.metadata_json["hunt_suggestions_enabled"] is True
    assert "technology_stack" not in run.metadata_json
    assert run.data_access_lineage_complete is True
    dispatch = db_session.get(AIWorkflowDispatch, run.id)
    assert (
        dispatch.task_name == "app.tasks.team_assessment_tasks.generate_team_assessment"
    )
    assert set(dispatch.payload_json) == {"task_run_id", "actor_user_id"}
    assert dispatch.state == "pending"
    captured = decrypt_json(row.authorization_encrypted)
    assert captured["credential_kind"] == "api_token"
    assert "write:teams" in captured["permissions"]
    assert decrypt_json(row.source_encrypted)[0][0] == str(assessment_setup[1].id)
    assert len(db_session.scalars(select(TeamItemAssessment)).all()) == 1
    duplicate = client.post(
        f"/items/{row.item_id}/team-assessment",
        json={"team_id": str(row.team_id), "expected_version": 1},
        headers=auth_headers["analyst"],
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "team_assessment_in_progress"


def test_hunts_toggle_off_keeps_team_relevance_generation_enabled(
    client, db_session, auth_headers, assessment_setup
):
    settings = get_or_create_ai_settings(db_session)
    settings.hunt_suggestions_enabled = False
    db_session.commit()
    response = _queue(client, assessment_setup, auth_headers["analyst"])
    assert response["ai_enabled"] is True
    assert response["configured"] is True
    assert response["hunt_suggestions_enabled"] is False
    row = db_session.get(TeamItemAssessment, uuid.UUID(response["assessment"]["id"]))
    assert (
        db_session.get(AITaskRun, row.task_run_id).metadata_json[
            "hunt_suggestions_enabled"
        ]
        is False
    )


def test_assessment_read_and_generation_require_membership_and_credential_scopes(
    client, db_session, auth_headers, seed_users, assessment_setup
):
    team, item, members, managers = assessment_setup
    _queue(client, assessment_setup, auth_headers["analyst"])
    path = f"/items/{item.id}/team-assessment"
    viewer = client.get(
        path, params={"team_id": team["id"]}, headers=auth_headers["viewer"]
    )
    assert viewer.status_code == 200, viewer.text
    assert viewer.json()["can_generate"] is False
    limited = _credential(
        db_session, seed_users["analyst"], ["read:items", "read:teams"]
    )
    assert (
        client.get(path, params={"team_id": team["id"]}, headers=limited).json()[
            "can_generate"
        ]
        is False
    )
    assert (
        client.post(
            path, json={"team_id": team["id"], "expected_version": 1}, headers=limited
        ).status_code
        == 403
    )
    for role, group in (("analyst", members), ("admin", managers)):
        membership = db_session.scalar(
            select(IAMGroupMembership).where(
                IAMGroupMembership.group_id == group.id,
                IAMGroupMembership.user_id == seed_users[role].id,
            )
        )
        db_session.delete(membership)
    db_session.commit()
    assert (
        client.get(
            path, params={"team_id": team["id"]}, headers=auth_headers["analyst"]
        ).status_code
        == 404
    )
    assert (
        client.get(
            path, params={"team_id": team["id"]}, headers=auth_headers["admin"]
        ).status_code
        == 404
    )


def test_review_preserves_prior_revision_and_stays_available_with_ai_and_hunts_off(
    client, db_session, auth_headers, assessment_setup, monkeypatch
):
    response = _queue(client, assessment_setup, auth_headers["analyst"])
    row, hunt_id = _ready(db_session, response)
    saved = _review(
        client, assessment_setup, auth_headers["analyst"], hunt_id, version=1
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["assessment"]["version"] == 2
    assert (
        saved.json()["assessment"]["result"]["hunts"][0]["review_status"] == "accepted"
    )
    old = db_session.get(TeamAssessmentRevision, (row.id, 1))
    assert old.result_json["hunts"][0]["review_status"] == "suggested"
    stale = _review(
        client,
        assessment_setup,
        auth_headers["analyst"],
        hunt_id,
        version=1,
        status="rejected",
    )
    assert stale.status_code == 409, stale.text
    settings = get_or_create_ai_settings(db_session)
    settings.hunt_suggestions_enabled = False
    db_session.commit()
    monkeypatch.setenv("AI_ENABLED", "false")
    get_settings.cache_clear()
    reviewed = _review(
        client,
        assessment_setup,
        auth_headers["analyst"],
        hunt_id,
        version=2,
        status="rejected",
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["ai_enabled"] is False
    assert reviewed.json()["hunt_suggestions_enabled"] is False
    assert reviewed.json()["can_generate"] is True
    assert (
        db_session.get(TeamAssessmentRevision, (row.id, 2)).result_json["hunts"][0][
            "review_status"
        ]
        == "accepted"
    )
    event = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "ai.team_assessment.hunt_rejected")
    )
    assert "note" not in event.metadata_json


def test_regeneration_keeps_prior_result_provenance_and_rejects_stale_reviews(
    client, db_session, auth_headers, assessment_setup
):
    response = _queue(client, assessment_setup, auth_headers["analyst"])
    row, hunt_id = _ready(db_session, response)
    team, item, *_ = assessment_setup
    changed = client.patch(
        f"/teams/{team['id']}/ai-context",
        headers=auth_headers["admin"],
        json={
            "expected_version": 0,
            "technology_stack": ["Linux"],
            "priorities": [],
            "available_telemetry": [],
            "relevance_criteria": "Changed priorities",
        },
    )
    assert changed.status_code == 200, changed.text
    current = client.get(
        f"/items/{item.id}/team-assessment",
        params={"team_id": team["id"]},
        headers=auth_headers["analyst"],
    )
    assert current.json()["assessment"]["stale"] is True
    assert current.json()["assessment"]["context_version"] == 0
    assert (
        _review(
            client, assessment_setup, auth_headers["analyst"], hunt_id, version=1
        ).status_code
        == 409
    )
    queued = _queue(client, assessment_setup, auth_headers["analyst"], version=1)
    assert queued["assessment"]["stale"] is True
    assert queued["assessment"]["context_version"] == 0
    assert queued["assessment"]["result"]["hunts"][0]["id"] == hunt_id
    db_session.refresh(row)
    assert row.context_version == 1
    assert row.result_context_version == 0
    assert (
        _review(
            client, assessment_setup, auth_headers["analyst"], hunt_id, version=2
        ).status_code
        == 409
    )


def test_assessment_admission_is_bounded_while_consumers_are_paused(
    client, db_session, auth_headers, assessment_setup, monkeypatch
):
    monkeypatch.setattr(team_assessments, "MAX_ACTIVE_ASSESSMENTS", 1)
    _queue(client, assessment_setup, auth_headers["analyst"])
    team, *_ = assessment_setup
    item = _create_item(db_session)
    response = client.post(
        f"/items/{item.id}/team-assessment",
        headers=auth_headers["analyst"],
        json={"team_id": team["id"], "expected_version": 0},
    )
    assert response.status_code == 429, response.text
    assert response.headers["Retry-After"] == "30"
    assert db_session.scalar(select(func.count()).select_from(TeamItemAssessment)) == 1


def test_expiring_credential_during_acceptance_rolls_back_work_and_delivery(
    client, db_session, auth_headers, seed_users, assessment_setup, monkeypatch
):
    queue = team_assessments.queue_ai_task_run

    def expire_after_queue(db, **kwargs):
        result = queue(db, **kwargs)
        token = db.scalar(
            select(ApiToken).where(ApiToken.user_id == seed_users["analyst"].id)
        )
        token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.flush()
        return result

    monkeypatch.setattr(team_assessments, "queue_ai_task_run", expire_after_queue)
    team, item, *_ = assessment_setup
    response = client.post(
        f"/items/{item.id}/team-assessment",
        headers=auth_headers["analyst"],
        json={"team_id": team["id"], "expected_version": 0},
    )
    assert response.status_code == 403, response.text
    assert db_session.scalar(select(func.count()).select_from(TeamItemAssessment)) == 0
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AITaskRun)
            .where(AITaskRun.task_type == "team_assessment")
        )
        == 0
    )


@pytest.mark.parametrize("long_hunt", [False, True])
def test_accepted_hunt_creates_one_team_investigation_with_evidence_and_review_history(
    client, db_session, auth_headers, assessment_setup, long_hunt
):
    response = _queue(client, assessment_setup, auth_headers["analyst"])
    row, hunt_id = _ready(db_session, response)
    if long_hunt:
        from copy import deepcopy

        result = deepcopy(row.result_json)
        hunt = result["hunts"][0]
        hunt["hypothesis"] = "Hypothesis " + "h" * 1589
        hunt["rationale"] = "Rationale " + "r" * 1590
        for field in ("required_logs", "benign_explanations", "information_gaps"):
            hunt[field] = [f"{field} {index} " + "x" * 365 for index in range(8)]
        row.result_json = result
        db_session.commit()
    team, item, *_ = assessment_setup
    path = f"/items/{item.id}/team-assessment/hunts/{hunt_id}/investigation"
    missing_review = client.post(
        path,
        headers=auth_headers["analyst"],
        json={"team_id": team["id"], "expected_version": 1},
    )
    assert missing_review.status_code == 409, missing_review.text
    reviewed = _review(
        client, assessment_setup, auth_headers["analyst"], hunt_id, version=1
    )
    assert reviewed.status_code == 200, reviewed.text
    created = client.post(
        path,
        headers=auth_headers["analyst"],
        json={"team_id": team["id"], "expected_version": 2},
    )
    assert created.status_code == 200, created.text
    investigation_id = uuid.UUID(
        created.json()["assessment"]["result"]["hunts"][0]["investigation_id"]
    )
    investigation = db_session.get(Investigation, investigation_id)
    assert investigation.team_id == uuid.UUID(team["id"])
    evidence = db_session.scalar(
        select(InvestigationEvidence).where(
            InvestigationEvidence.investigation_id == investigation_id
        )
    )
    assert evidence.source_id == item.id
    notes = db_session.scalars(
        select(InvestigationNote).where(
            InvestigationNote.investigation_id == investigation_id
        )
    ).all()
    assert all(len(note.body) <= 10_000 for note in notes)
    bodies = "\n".join(note.body for note in notes)
    assert "Checked the primary evidence." in bodies
    assert "A bounded source summary for the investigation snapshot." in bodies
    if long_hunt:
        assert len(notes) > 1
        for field in ("required_logs", "benign_explanations", "information_gaps"):
            assert all(entry in bodies for entry in hunt[field])
        assert hunt["hypothesis"] in bodies and hunt["rationale"] in bodies
    repeat = client.post(
        path,
        headers=auth_headers["analyst"],
        json={"team_id": team["id"], "expected_version": 3},
    )
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["assessment"]["version"] == 3
    assert db_session.scalar(select(func.count()).select_from(Investigation)) == 1
    _queue(client, assessment_setup, auth_headers["analyst"], version=3)
    historical = db_session.get(TeamAssessmentRevision, (row.id, 3))
    assert historical.result_json["hunts"][0]["investigation_id"] == str(
        investigation_id
    )


def test_team_membership_does_not_bypass_article_handling_policy(
    client, db_session, auth_headers, seed_users, assessment_setup, monkeypatch
):
    from app.models.feed import Feed

    response = _queue(client, assessment_setup, auth_headers["analyst"])
    _ready(db_session, response)
    team, item, *_ = assessment_setup
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    db_session.get(Feed, item.feed_id).handling_label_id = restricted.id
    db_session.commit()
    path = f"/items/{item.id}/team-assessment"
    assert (
        client.get(
            path, params={"team_id": team["id"]}, headers=auth_headers["analyst"]
        ).status_code
        == 404
    )
    denied = client.post(
        path,
        json={"team_id": team["id"], "expected_version": 1},
        headers=auth_headers["analyst"],
    )
    assert denied.status_code == 404, denied.text
    administrator = client.get(
        path, params={"team_id": team["id"]}, headers=auth_headers["admin"]
    )
    assert administrator.status_code == 200, administrator.text
    assert administrator.json()["assessment"]["result"] is not None


def test_relabel_and_regeneration_do_not_publish_prior_restricted_result(
    client, db_session, auth_headers, seed_users, assessment_setup, monkeypatch
):
    from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
    from app.models.feed import Feed

    team, item, *_ = assessment_setup
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    feed = db_session.get(Feed, item.feed_id)
    feed.handling_label_id = restricted.id
    db_session.commit()
    response = _queue(client, assessment_setup, auth_headers["admin"])
    row, hunt_id = _ready(db_session, response)
    prior_run = db_session.get(AITaskRun, row.task_run_id)
    prior_run.status = "error"
    prior_run.error = "A sensitive provider diagnostic about prior source evidence."
    feed.handling_label_id = UNRESTRICTED_HANDLING_LABEL_ID
    db_session.commit()
    path = f"/items/{item.id}/team-assessment"
    hidden = client.get(
        path, params={"team_id": team["id"]}, headers=auth_headers["analyst"]
    )
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["assessment"]["result"] is None
    assert hidden.json()["assessment"]["stale"] is True
    assert hidden.json()["assessment"]["error"] is None
    visible = client.get(
        path, params={"team_id": team["id"]}, headers=auth_headers["admin"]
    )
    assert visible.json()["assessment"]["result"] is not None
    assert (
        _review(
            client, assessment_setup, auth_headers["analyst"], hunt_id, version=1
        ).status_code
        == 409
    )
    queued = _queue(client, assessment_setup, auth_headers["analyst"], version=1)
    assert queued["assessment"]["result"] is None
    assert queued["assessment"]["stale"] is True
    db_session.refresh(row)
    assert decrypt_json(row.source_encrypted)[0][2] == str(
        UNRESTRICTED_HANDLING_LABEL_ID
    )
    assert decrypt_json(row.result_source_encrypted)[0][2] == str(restricted.id)
    history = db_session.get(TeamAssessmentRevision, (row.id, 1))
    assert decrypt_json(history.result_source_encrypted)[0][2] == str(restricted.id)
    _ready(db_session, queued)
    current = client.get(
        path, params={"team_id": team["id"]}, headers=auth_headers["analyst"]
    )
    assert current.json()["assessment"]["result"] is not None
    assert current.json()["assessment"]["stale"] is False


@pytest.mark.parametrize("snapshot", [None, [], [["not-a-uuid", "invalid", "source"]]])
def test_missing_or_invalid_result_source_snapshot_fails_closed(
    client, db_session, auth_headers, assessment_setup, snapshot
):
    from app.services.secret_storage import encrypt_json

    response = _queue(client, assessment_setup, auth_headers["analyst"])
    row, _ = _ready(db_session, response)
    row.result_source_encrypted = (
        encrypt_json(snapshot) if snapshot is not None else None
    )
    db_session.commit()
    team, item, *_ = assessment_setup
    hidden = client.get(
        f"/items/{item.id}/team-assessment",
        params={"team_id": team["id"]},
        headers=auth_headers["analyst"],
    )
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["assessment"]["result"] is None
    assert hidden.json()["assessment"]["stale"] is True
