"""Run accepted work through real authorization, provider receipts and publish."""

import copy
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.ai_workflow import AIWorkflowDispatch
from app.models.api_token import ApiToken
from app.models.article import Article
from app.models.feed import Feed
from app.models.iam import IAMGroupMembership
from app.models.item import Item
from app.models.team_ai_context import TeamAIContext
from app.models.team_item_assessment import TeamItemAssessment
from app.schemas.ai import AISettingsUpdate
from app.services.ai_config import apply_ai_settings_update, get_or_create_ai_settings
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.secret_storage import decrypt_json, encrypt_json
from app.tasks.team_assessment_tasks import generate_team_assessment
from tests.integration.test_teams_api import _team


EVIDENCE = "Researchers reported unusual PowerShell activity on managed endpoints."


def provider_payload(*, hunts=True):
    return {
        "relevance_score": 0.75,
        "relevance_reasons": ["The team operates managed Windows endpoints."],
        "information_gaps": ["No local observations were supplied."],
        "hunts": [{
            "title": "Review reported endpoint behavior", "hypothesis": "The reported behavior may warrant review in available endpoint telemetry.",
            "rationale": "The article describes behavior relevant to the team's Windows systems.",
            "required_logs": ["Endpoint process events"], "benign_explanations": ["Approved administrative maintenance."],
            "information_gaps": ["No local process observations were provided."],
            "evidence": [{"source": "article_text", "quote": EVIDENCE}],
            "attack_technique_ids": ["T1059.001"], "detection_strategy_ids": [],
        }] if hunts else [],
    }


def _completion(*, hunts=True):
    return AICompletionResult(
        payload=provider_payload(hunts=hunts), provider="openai_compatible", model="test-model",
        latency_ms=10, prompt_tokens=500, completion_tokens=200, total_tokens=700,
    )


@pytest.fixture()
def accepted_assessment(client, db_session, seed_users, auth_headers, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.api.routes.team_assessments.publish_accepted_assessment", lambda *_args: None)
    team, members, managers = _team(client, db_session, seed_users, auth_headers)
    team_id = uuid.UUID(team["id"])
    context = TeamAIContext(team_id=team_id, technology_stack=["Windows"], priorities=["Endpoint integrity"],
                            available_telemetry=["Endpoint process events"], relevance_criteria="Protect managed endpoints.", version=1)
    db_session.add(context)
    feed = Feed(name="Assessment source", url=f"https://example.org/{uuid.uuid4()}")
    db_session.add(feed)
    db_session.flush()
    item = Item(feed_id=feed.id, title="Endpoint report", summary="Source report.", url="https://example.org/report",
                dedupe_key=str(uuid.uuid4()), content_hash="a" * 64, status="content_fetched")
    db_session.add(item)
    db_session.flush()
    article = Article(item_id=item.id, final_url=item.url, http_status=200, text=EVIDENCE)
    db_session.add(article)
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(settings, AISettingsUpdate(base_url="http://localhost:11434/v1", model="test-model",
                                                      request_max_retries=0, hunt_suggestions_enabled=True))
    db_session.commit()
    response = client.post(f"/items/{item.id}/team-assessment", json={"team_id": str(team_id), "expected_version": 0},
                           headers=auth_headers["analyst"])
    assert response.status_code == 202, response.text
    row = db_session.scalar(select(TeamItemAssessment).where(TeamItemAssessment.item_id == item.id))
    run = db_session.get(AITaskRun, row.task_run_id)
    return {"row": row, "run": run, "item": item, "article": article, "context": context,
            "settings": settings, "team_id": team_id, "members": members, "managers": managers}


def invoke_worker(db_session, monkeypatch, run_id, *, delivery=None):
    @contextmanager
    def session():
        yield db_session

    monkeypatch.setattr("app.tasks.team_assessment_tasks.SessionLocal", session)
    generate_team_assessment.push_request(id=delivery or str(uuid.uuid4()), hostname="test-worker")
    try:
        return generate_team_assessment.run(str(run_id))
    finally:
        generate_team_assessment.pop_request()


def test_worker_generates_with_team_context_receipts_and_official_references(
    db_session, accepted_assessment, monkeypatch, client, auth_headers,
):
    sent = []

    def provider(_active, **kwargs):
        prompt = json.loads(kwargs["messages"][1]["content"])
        sent.append(prompt)
        assert prompt["team_context"]["technology_stack"] == ["Windows"]
        assert "company_context" not in prompt
        return _completion()

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    result = invoke_worker(db_session, monkeypatch, accepted_assessment["run"].id)
    assert result == {"status": "ready"}
    assert len(sent) == 1
    row = accepted_assessment["row"]
    db_session.refresh(row)
    assert row.result_json["hunts"][0]["detection_strategy_ids"] == ["DET0455"]
    assert row.result_json["hunts"][0]["review_status"] == "suggested"
    assert row.result_source_version == accepted_assessment["item"].classification_required_version
    assert row.result_source_encrypted == row.source_encrypted
    assert db_session.scalar(select(AIProviderAttemptReceipt.state)) == "succeeded"
    assert db_session.scalar(select(AIUsageEvent.feature_type)) == "team_assessment"
    assert db_session.get(AIWorkflowDispatch, row.task_run_id).state == "complete"
    response = client.get(f"/items/{row.item_id}/team-assessment?team_id={row.team_id}", headers=auth_headers["viewer"])
    assert response.status_code == 200, response.text
    assert response.json()["assessment"]["result"]["hunts"][0]["evidence"][0]["quote"] == EVIDENCE


@pytest.mark.parametrize("change", ["credential", "membership", "context", "source", "version", "snapshot", "source_feed", "hunt_toggle"])
def test_queued_changes_block_provider_io(db_session, accepted_assessment, seed_users, monkeypatch, change):
    state = accepted_assessment
    if change == "credential":
        snapshot = decrypt_json(state["row"].authorization_encrypted)
        db_session.get(ApiToken, uuid.UUID(snapshot["credential_id"])).revoked_at = datetime.now(timezone.utc)
    elif change == "membership":
        membership = db_session.scalar(select(IAMGroupMembership).where(
            IAMGroupMembership.group_id == state["members"].id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        ))
        db_session.delete(membership)
    elif change == "context":
        state["context"].version += 1
    elif change == "source":
        state["item"].classification_required_version += 1
    elif change == "version":
        state["row"].version += 1
    elif change == "snapshot":
        state["row"].source_encrypted = encrypt_json([])
    elif change == "source_feed":
        snapshot = decrypt_json(state["row"].source_encrypted)
        snapshot[0][1] = str(uuid.uuid4())
        state["row"].source_encrypted = encrypt_json(snapshot)
    elif change == "hunt_toggle":
        state["settings"].hunt_suggestions_enabled = False
    db_session.commit()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Changed authorization or source must prevent provider I/O")

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", forbidden)
    result = invoke_worker(db_session, monkeypatch, state["run"].id)
    assert result["status"] == "error"
    db_session.refresh(state["row"])
    assert state["row"].result_json is None
    assert db_session.scalar(select(AIProviderAttemptReceipt.id)) is None


def test_ambiguous_provider_delivery_is_retained_and_not_replayed(db_session, accepted_assessment, monkeypatch):
    calls = []

    def ambiguous(*_args, **_kwargs):
        calls.append(True)
        raise AIIntegrationError("Provider outcome is uncertain.", provider_io_outcome="ambiguous", retryable=True)

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", ambiguous)
    run_id = accepted_assessment["run"].id
    assert invoke_worker(db_session, monkeypatch, run_id)["status"] == "error"
    assert db_session.scalar(select(AIProviderAttemptReceipt.state)) == "ambiguous"
    assert invoke_worker(db_session, monkeypatch, run_id)["status"] == "skipped"
    assert len(calls) == 1


def test_superseded_worker_cannot_publish_or_replace_newer_run_state(db_session, accepted_assessment, monkeypatch):
    state = accepted_assessment
    delivery = str(uuid.uuid4())

    def supersede(*_args, **_kwargs):
        run = db_session.get(AITaskRun, state["run"].id)
        run.celery_task_id = "replacement-worker"
        run.metadata_json = {**run.metadata_json, "replacement_marker": True}
        db_session.flush()
        return _completion()

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", supersede)
    result = invoke_worker(db_session, monkeypatch, state["run"].id, delivery=delivery)
    assert result["status"] == "skipped"
    db_session.refresh(state["run"])
    db_session.refresh(state["row"])
    assert state["run"].celery_task_id == "replacement-worker"
    assert state["run"].status == "running"
    assert state["row"].result_json is None


def test_failed_retry_preserves_prior_published_team_result(db_session, accepted_assessment, monkeypatch):
    row, run = accepted_assessment["row"], accepted_assessment["run"]
    previous = provider_payload(hunts=False)
    row.result_json = copy.deepcopy(previous)
    row.result_context_version = row.context_version
    row.result_source_version = row.source_version
    row.result_source_encrypted = row.source_encrypted
    row.result_article_id = row.article_id
    row.result_article_retrieved_at = row.article_retrieved_at
    row.generated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    def invalid(*_args, **_kwargs):
        output = _completion()
        output.payload["hunts"][0]["evidence"][0]["quote"] = "An invented supporting passage."
        return output

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", invalid)
    assert invoke_worker(db_session, monkeypatch, run.id)["status"] == "error"
    db_session.refresh(row)
    assert row.result_json == previous
    assert db_session.scalar(select(AIUsageEvent.success)) is False
    assert db_session.scalar(select(AIProviderAttemptReceipt.state)) == "failed"


def test_hunt_toggle_off_requests_relevance_without_hunts(db_session, accepted_assessment, monkeypatch):
    state = accepted_assessment
    state["settings"].hunt_suggestions_enabled = False
    state["run"].metadata_json = {**state["run"].metadata_json, "hunt_suggestions_enabled": False}
    db_session.commit()

    def provider(_active, *, messages):
        assert json.loads(messages[1]["content"])["hunts_enabled"] is False
        return _completion(hunts=False)

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    assert invoke_worker(db_session, monkeypatch, state["run"].id) == {"status": "ready"}
    assert state["row"].result_json["hunts"] == []


def test_revocation_between_receipt_reservation_and_provider_send_prevents_io(
    db_session, accepted_assessment, monkeypatch,
):
    from app.services import team_assessment_generation

    state = accepted_assessment
    checkpoint = team_assessment_generation.fence_team_assessment
    credential_id = uuid.UUID(decrypt_json(state["row"].authorization_encrypted)["credential_id"])
    revoked = []

    def revoke_after_reservation(db, *, run_id):
        receipt = db.scalar(select(AIProviderAttemptReceipt))
        if receipt is not None and receipt.state == "reserved" and not revoked:
            db.get(ApiToken, credential_id).revoked_at = datetime.now(timezone.utc)
            db.flush()
            revoked.append(True)
        return checkpoint(db, run_id=run_id)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Revoked credentials must never reach the provider")

    monkeypatch.setattr(team_assessment_generation, "fence_team_assessment", revoke_after_reservation)
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", forbidden)
    result = invoke_worker(db_session, monkeypatch, state["run"].id)
    assert revoked == [True]
    assert result["status"] == "error"
    assert state["row"].result_json is None
    receipt = db_session.scalar(select(AIProviderAttemptReceipt))
    assert receipt.state == "voided"


def test_cancellation_after_provider_response_does_not_publish(db_session, accepted_assessment, monkeypatch):
    state = accepted_assessment

    def cancel_during_response(*_args, **_kwargs):
        run = db_session.get(AITaskRun, state["run"].id)
        run.metadata_json = {**run.metadata_json, "cancel_requested_at": datetime.now(timezone.utc).isoformat()}
        db_session.flush()
        return _completion()

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", cancel_during_response)
    invoke_worker(db_session, monkeypatch, state["run"].id)
    db_session.refresh(state["run"])
    db_session.refresh(state["row"])
    assert state["run"].status == "skipped"
    assert state["run"].reason == "canceled"
    assert state["row"].result_json is None


def test_published_evidence_selection_matches_budgeted_prompt_after_removing_late_quotes(
    db_session, accepted_assessment, monkeypatch, client, auth_headers,
):
    from app.models.item_ai_enrichment import ItemAIEnrichment
    from app.services.ai_extraction import build_verified_extraction
    from tests.unit.test_ai_extraction import ARTICLE, extraction_payload, source_messages

    state = accepted_assessment
    article, item = state["article"], state["item"]
    article.text = EVIDENCE + " " + "Introduction. " * 7000 + ARTICLE
    state["settings"].model_context_window_tokens = 4096
    state["settings"].max_completion_tokens = 1024
    extraction = build_verified_extraction(extraction_payload(), messages=source_messages(), article_id=article.id,
        article_retrieved_at=article.retrieved_at, source_version=item.classification_required_version,
        source_hash="a" * 64, article_text_length=len(article.text))
    offset = len(article.text) - len(ARTICLE)
    for entry in [*extraction["entities"], *extraction["relationships"]]:
        for evidence in entry["evidence"]:
            evidence["start"] += offset
            evidence["end"] += offset
    db_session.add(ItemAIEnrichment(item_id=item.id, status="ready", source_hash="a" * 64,
        structured_extraction_json=extraction))
    db_session.commit()
    sent = []

    def provider(_active, **kwargs):
        sent.append(json.loads(kwargs["messages"][-1]["content"]))
        return _completion()

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    result = invoke_worker(db_session, monkeypatch, state["run"].id)
    assert result == {"status": "ready"}
    assert sent[0]["evidence_selection"]["selected_passages"] == []
    assert sent[0]["evidence_selection"]["selection"] == "article_prefix"
    assert EVIDENCE in sent[0]["item"]["article_text"]
    db_session.refresh(state["row"])
    assert state["row"].result_json["evidence_selection"] == sent[0]["evidence_selection"]
    response = client.get(f"/items/{item.id}/team-assessment?team_id={state['team_id']}", headers=auth_headers["viewer"])
    assert response.status_code == 200, response.text
    assert response.json()["assessment"]["result"]["evidence_selection"]["selected_passages"] == []
