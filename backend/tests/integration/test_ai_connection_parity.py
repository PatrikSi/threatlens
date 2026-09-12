"""Exercise both diagnostic routes through real authorization and receipt writes."""
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.api_token import ApiToken
from app.services import ai_integration
from app.services.authorization import bump_iam_policy_revision


@pytest.fixture(params=["legacy", "named"])
def diagnostic(request, client, auth_headers, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (True, [], [], [], []))
    monkeypatch.setattr("app.services.ai_workflow_publication.queued_ai_delivery_ids", lambda: set())
    settings = {"base_url": "https://example.com/v1", "model": "synthetic", "max_completion_tokens": 65536,
                "request_timeout_seconds": 300, "request_max_retries": 5}
    headers = auth_headers["admin"]
    if request.param == "legacy":
        assert client.put("/ai/settings", headers=headers, json=settings).status_code == 200
        path, body = "/ai/test-connection", None
    else:
        created = client.post("/ai/providers", headers=headers, json={"name": "Synthetic provider", **settings})
        assert created.status_code == 201
        provider = created.json()
        path, body = f"/ai/providers/{provider['id']}/test-connection", {"version": provider["version"]}
    return lambda: client.post(path, headers=headers, json=body)


def _transport(monkeypatch, *, status=200, finish_reason="stop"):
    calls, options = [], []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(status, json={
            "model": "synthetic", "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 128, "total_tokens": 148},
        })

    def client_factory(**kwargs):
        options.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ai_integration, "build_safe_http_client", client_factory)
    return calls, options


@pytest.mark.parametrize("status, finish_reason", [(200, "stop"), (200, "length"), (503, "stop")])
def test_diagnostics_share_bounded_requests_and_never_retry(diagnostic, monkeypatch, status, finish_reason):
    calls, options = _transport(monkeypatch, status=status, finish_reason=finish_reason)
    response = diagnostic()
    assert response.status_code == 200
    assert response.json()["success"] is (status == 200 and finish_reason == "stop")
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 128
    assert options[0]["timeout"].read == 30
    if finish_reason == "length":
        assert "fixed 128-token" in response.json()["error"]
        assert "does not change this small diagnostic test" in response.json()["error"]


@pytest.mark.parametrize("fence_number", [1, 2])
def test_diagnostics_stop_revoked_actor_before_each_io_fence(
    diagnostic, db_session, seed_users, monkeypatch, fence_number,
):
    calls, _options = _transport(monkeypatch)
    original_fence = ai_integration.fence_authorization_context
    checked = 0

    def revoke_at_fence(db, context):
        nonlocal checked
        checked += 1
        if checked == fence_number:
            for token in db.scalars(select(ApiToken).where(ApiToken.user_id == seed_users["admin"].id)):
                token.revoked_at = datetime.now(timezone.utc)
            bump_iam_policy_revision(db)
            db.commit()
        return original_fence(db, context)

    monkeypatch.setattr(ai_integration, "fence_authorization_context", revoke_at_fence)
    response = diagnostic()
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "permissions changed" in response.json()["error"]
    assert calls == []
    receipts = list(db_session.scalars(select(AIProviderAttemptReceipt)))
    assert all(receipt.io_outcome == "not_sent" for receipt in receipts)
    if fence_number == 2:
        assert receipts


def test_connection_service_rejects_missing_actor_context(db_session, monkeypatch):
    from app.services.ai_provider_client import AIIntegrationError

    calls, _options = _transport(monkeypatch)
    with pytest.raises(AIIntegrationError, match="authorization context") as caught:
        ai_integration.test_ai_connection(db_session, request_authorization=None)
    assert caught.value.provider_io_outcome == "not_sent"
    assert calls == []
