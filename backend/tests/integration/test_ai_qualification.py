"""Qualification uses real authorization and provider receipts with fake network I/O."""
import uuid
import json
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from app.models.ai_task_run import AITaskRun
from app.models.ai_qualification import AIQualification
from app.models.ai_workflow import AIWorkflowDispatch
from app.models.api_token import ApiToken
from app.services.ai_ops import start_ai_task_run
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_egress_data_policy import AIEgressPolicyError
from app.services.ai_qualification import generate_qualification
from app.services.ai_workflow_dispatch import AIWorkflowDeferred
from app.services.encrypted_data_inventory import scan_encrypted_data_inventory
from tests.integration.test_ai_provider_api import _create, enable_ai  # noqa: F401


@pytest.fixture
def setup_qualification(client, auth_headers, db_session, monkeypatch):
    monkeypatch.setattr("app.api.routes.ai_qualification.publish_accepted_assessment", lambda *_args: None)
    provider = _create(client, auth_headers, max_completion_tokens=1024)
    body = {"request_id": str(uuid.uuid4()), "provider_version": provider["version"],
        "features": ["report"], "token_budget": 24000, "authorize_provider_calls": True}
    response = client.post(f"/ai/providers/{provider['id']}/qualifications", json=body, headers=auth_headers["admin"])
    assert response.status_code == 202, response.text
    return provider, body, uuid.UUID(response.json()["run_id"])


def _start(db, run_id):
    start_ai_task_run(db, run_id=run_id, worker_name="test-worker")
    db.commit()
    assert db.get(AITaskRun, run_id).status == "running"


def test_qualification_is_idempotent_bounded_and_uses_durable_outbox(client, auth_headers, db_session, setup_qualification):
    provider, body, run_id = setup_qualification
    again = client.post(f"/ai/providers/{provider['id']}/qualifications", json=body, headers=auth_headers["admin"])
    assert again.status_code == 202 and again.json()["run_id"] == str(run_id)
    changed = client.post(f"/ai/providers/{provider['id']}/qualifications", json={**body, "features": ["hunt"]}, headers=auth_headers["admin"])
    assert changed.status_code == 409
    assert db_session.get(AIWorkflowDispatch, run_id).task_name == "app.tasks.ai_qualification_tasks.generate_ai_qualification"
    listing = client.get(f"/ai/providers/{provider['id']}/qualifications", headers=auth_headers["admin"])
    assert listing.status_code == 200
    assert "authorization_encrypted" not in listing.text
    assert not listing.json()[0]["semantic_quality_approved"]
    inventory = scan_encrypted_data_inventory(db_session)
    assert inventory.durable_ai_authorizations.encrypted_fields == 1
    assert inventory.durable_ai_authorizations.unreadable_fields == 0


def test_revoked_accepting_credential_blocks_provider_calls(db_session, setup_qualification, monkeypatch):
    _, _, run_id = setup_qualification
    _start(db_session, run_id)
    owner = db_session.get(AIQualification, run_id).principal_id
    for token in db_session.scalars(select(ApiToken).where(ApiToken.user_id == owner)):
        token.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_a, **_kw: pytest.fail("revoked caller must not send"))
    with pytest.raises(AIEgressPolicyError, match="expired or lost access"):
        generate_qualification(db_session, run_id=run_id)


def test_completed_contract_probe_survives_redelivery_without_another_call(db_session, setup_qualification, monkeypatch):
    _, _, run_id = setup_qualification
    _start(db_session, run_id)
    calls = []
    from app.services.ai_qualification_cases import SOURCE
    def provider(_active, **kwargs):
        calls.append(kwargs)
        if "section" in json.loads(kwargs["messages"][-1]["content"]):
            return AICompletionResult(payload={"body_markdown": "The source reports scheduled task persistence. [S1]", "citations": ["S1"], "key_points": []},
                provider="openai_compatible", model="fixture", latency_ms=10, prompt_tokens=100, completion_tokens=50, total_tokens=150)
        return AICompletionResult(payload={"findings": [{"text": "The source reports scheduled task persistence.", "citations": ["S1"],
            "evidence_quotes": [{"citation": "S1", "quote": SOURCE}]}]}, provider="openai_compatible", model="fixture",
            latency_ms=10, prompt_tokens=100, completion_tokens=50, total_tokens=150)
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    generate_qualification(db_session, run_id=run_id)
    assert len(calls) == 2
    row = db_session.get(AIQualification, run_id)
    assert row.results_json[0]["contract_passed"]
    assert row.reserved_tokens <= row.token_budget
    assert db_session.get(AITaskRun, run_id).status == "ready"
    assert not db_session.get(AITaskRun, run_id).metadata_json["semantic_quality_approved"]
    with pytest.raises(AIEgressPolicyError, match="canceled or replaced"):
        generate_qualification(db_session, run_id=run_id)
    assert len(calls) == 2


def test_admission_deferral_does_not_leave_unsent_stage_stuck(db_session, setup_qualification, monkeypatch):
    _, _, run_id = setup_qualification
    _start(db_session, run_id)
    def defer(*_args, **_kwargs):
        raise AIWorkflowDeferred("quota", 10)
    monkeypatch.setattr("app.services.ai_qualification.request_ai_json_with_usage", defer)
    with pytest.raises(AIWorkflowDeferred):
        generate_qualification(db_session, run_id=run_id)
    row = db_session.get(AIQualification, run_id)
    assert row.results_json == [] and row.reserved_tokens == 0


def test_unresolved_stage_cannot_be_automatically_repeated(db_session, setup_qualification, monkeypatch):
    _, _, run_id = setup_qualification
    _start(db_session, run_id)
    row = db_session.get(AIQualification, run_id)
    row.results_json = [{"feature": "report", "state": "started"}]
    db_session.commit()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_a, **_kw: pytest.fail("ambiguous delivery must not replay"))
    with pytest.raises(AIIntegrationError, match="automatic replay is blocked"):
        generate_qualification(db_session, run_id=run_id)


def test_changed_probe_plan_blocks_queued_calls(db_session, setup_qualification, monkeypatch):
    _, _, run_id = setup_qualification
    _start(db_session, run_id)
    monkeypatch.setattr("app.services.ai_qualification.qualification_plan_fingerprint", lambda _features: "changed")
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_a, **_kw: pytest.fail("changed prompts must not send"))
    with pytest.raises(AIIntegrationError, match="prompts changed"):
        generate_qualification(db_session, run_id=run_id)


def test_exhausted_budget_blocks_unsent_probe(db_session, setup_qualification, monkeypatch):
    _, _, run_id = setup_qualification
    _start(db_session, run_id)
    row = db_session.get(AIQualification, run_id)
    row.token_budget = 1
    db_session.commit()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_a, **_kw: pytest.fail("budget must bound provider calls"))
    with pytest.raises(AIIntegrationError, match="token budget"):
        generate_qualification(db_session, run_id=run_id)
    assert db_session.get(AIQualification, run_id).reserved_tokens == 0
