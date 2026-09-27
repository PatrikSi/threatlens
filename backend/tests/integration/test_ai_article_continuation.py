import uuid
from datetime import datetime, timezone
import pytest
from app.models.ai_task_run import AITaskRun
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.ai_article_continuation import AIArticleContinuation
from app.services.ai_article_continuation import progress_digest
from tests.unit.test_ai_extraction_workflow import extraction_item  # noqa: F401


@pytest.fixture
def continuation(client, auth_headers, db_session, extraction_item, monkeypatch):  # noqa: F811
    item, _ = extraction_item
    original_id = uuid.uuid4()
    db_session.add(AITaskRun(id=original_id, task_type="item_enrichment", trigger_source="manual", status="ready",
        item_id=item.id, metadata_json={"provider_selection": {"provider_id": None, "version": None, "model": None}},
        finished_at=datetime.now(timezone.utc)))
    progress = {"task_run_id": str(original_id), "plan_fingerprint": "a" * 64, "source_hash": "a" * 64,
        "text_chars": 100000, "reserved_tokens": 1000,
        "sections": [{"index": 0, "start": 0, "end": 8000, "status": "completed"}]}
    db_session.add(ItemAIEnrichment(item_id=item.id, status="ready", source_hash="a" * 64, extraction_progress_json=progress))
    db_session.commit()
    monkeypatch.setattr("app.api.routes.ai_article_continuation.publish_accepted_assessment", lambda *_a: None)
    body = {"request_id": str(uuid.uuid4()), "progress_revision": progress_digest(progress)}
    return item, body


def test_continuation_requires_exact_progress_and_reuses_request_identity(client, auth_headers, db_session, continuation):
    item, body = continuation
    response = client.post(f"/ai/articles/{item.id}/continue", json=body, headers=auth_headers["admin"])
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    row = db_session.get(AIArticleContinuation, uuid.UUID(run_id))
    assert row.section_limit == 16 and row.token_budget == 128000
    again = client.post(f"/ai/articles/{item.id}/continue", json=body, headers=auth_headers["admin"])
    assert again.status_code == 202 and again.json()["run_id"] == run_id
    duplicate = client.post(f"/ai/articles/{item.id}/continue", json={**body, "request_id": str(uuid.uuid4())}, headers=auth_headers["admin"])
    assert duplicate.status_code == 409


def test_changed_progress_conflicts_before_accepting_any_calls(client, auth_headers, continuation):
    item, body = continuation
    response = client.post(f"/ai/articles/{item.id}/continue", json={**body, "progress_revision": "b" * 64}, headers=auth_headers["admin"])
    assert response.status_code == 409
    assert "progress changed" in response.text.lower()


def test_changed_feature_does_not_fall_back_to_repeating_first_section(client, auth_headers, db_session, continuation, monkeypatch):
    from app.services.ai_config import get_or_create_ai_settings
    from app.services.ai_integration import run_item_ai_enrichment
    item, body = continuation
    response = client.post(f"/ai/articles/{item.id}/continue", json=body, headers=auth_headers["admin"])
    assert response.status_code == 202
    settings = get_or_create_ai_settings(db_session)
    settings.structured_extraction_enabled = False
    settings.summary_enabled = True
    db_session.commit()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_a, **_kw: pytest.fail("must not run initial article call"))
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=uuid.UUID(response.json()["run_id"]))
    assert result.reason == "extraction_plan_changed"
