import json

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_provider import AIProviderConfiguration
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.services import ai_integration
from app.services.authorization import AuthorizationStateUnavailable


@pytest.fixture()
def provider_api(client, auth_headers, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "legacy-key-must-not-leave")
    get_settings.cache_clear()
    headers = auth_headers["admin"]
    response = client.post(
        "/v1/ai/providers",
        headers=headers,
        json={
            "name": "Independent provider",
            "base_url": "https://ai.example/v1",
            "model": "provider-model",
            "api_key": "profile-only-key",
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), headers


def _transport(monkeypatch, *, error=False):
    calls = []

    def handler(request):
        calls.append(request)
        if error:
            return httpx.Response(
                401,
                json={
                    "error": {
                        "message": "Invalid profile-only-key",
                        "param": "profile-only-key",
                        "code": "profile-only-key",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok":true,"message":"ready"}'}}]
            },
        )

    monkeypatch.setattr(
        ai_integration,
        "build_safe_http_client",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return calls


def test_named_connection_test_uses_saved_profile_and_immutable_version(
    client, provider_api, db_session, monkeypatch
):
    provider, headers = provider_api
    calls = _transport(monkeypatch)
    response = client.post(
        f"/v1/ai/providers/{provider['id']}/test-connection",
        headers=headers,
        json={"version": provider["version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert len(calls) == 1
    assert calls[0].headers["authorization"] == "Bearer profile-only-key"
    payload = json.loads(calls[0].content)
    assert payload["model"] == "provider-model"
    assert payload["max_tokens"] == 128
    assert "article_text" not in calls[0].content.decode()
    run = db_session.scalar(
        select(AITaskRun).where(AITaskRun.task_type == "connection_test")
    )
    assert run.status == "ready"
    assert run.metadata_json["provider_selection"]["provider_id"] == provider["id"]
    assert "profile-only-key" not in json.dumps(run.metadata_json)


def test_named_connection_version_conflict_is_actionable_and_sends_nothing(
    client, provider_api, monkeypatch
):
    provider, headers = provider_api
    calls = _transport(monkeypatch)
    response = client.post(
        f"/v1/ai/providers/{provider['id']}/test-connection",
        headers=headers,
        json={"version": provider["version"] + 1},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "provider_version_changed"
    assert not calls


@pytest.mark.parametrize("state", ["disabled", "unreadable"])
def test_named_connection_configuration_fault_is_actionable(
    client, provider_api, db_session, monkeypatch, state
):
    provider, headers = provider_api
    row = db_session.get(AIProviderConfiguration, provider["id"])
    if state == "disabled":
        row.enabled = False
    else:
        row.api_key_encrypted = "enc:v1:broken"
    db_session.commit()
    calls = _transport(monkeypatch)
    response = client.post(
        f"/v1/ai/providers/{provider['id']}/test-connection",
        headers=headers,
        json={"version": provider["version"]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == (
        "provider_disabled"
        if state == "disabled"
        else "provider_credential_unavailable"
    )
    assert not calls


def test_named_connection_error_redacts_key_and_does_not_retry(
    client, provider_api, monkeypatch, db_session
):
    provider, headers = provider_api
    calls = _transport(monkeypatch, error=True)
    response = client.post(
        f"/v1/ai/providers/{provider['id']}/test-connection",
        headers=headers,
        json={"version": provider["version"]},
    )
    assert response.status_code == 200 and response.json()["success"] is False
    assert "profile-only-key" not in response.text
    assert len(calls) == 1
    run = db_session.scalar(
        select(AITaskRun).where(AITaskRun.task_type == "connection_test")
    )
    assert run.status == "error" and "profile-only-key" not in run.error


def test_named_connection_truncation_explains_fixed_diagnostic_budget(
    client, provider_api, monkeypatch, db_session
):
    provider, headers = provider_api
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": None}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 128, "total_tokens": 148},
            },
        )

    monkeypatch.setattr(
        ai_integration,
        "build_safe_http_client",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    response = client.post(
        f"/v1/ai/providers/{provider['id']}/test-connection",
        headers=headers,
        json={"version": provider["version"]},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["success"] is False
    assert "endpoint responded" in result["error"]
    assert "fixed 128-token" in result["error"]
    assert "does not change this small diagnostic test" in result["error"]
    assert "Feature compatibility remains unverified" in result["error"]
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 128
    run = db_session.scalar(
        select(AITaskRun).where(AITaskRun.task_type == "connection_test")
    )
    assert run.status == "error" and run.error == result["error"]
    receipt = db_session.scalar(select(AIProviderAttemptReceipt))
    assert receipt.state == "failed"
    assert receipt.io_outcome == "response_received"


def test_named_connection_revalidates_request_authorization_before_io(
    client, provider_api, db_session, monkeypatch
):
    provider, headers = provider_api
    calls = _transport(monkeypatch)
    original = ai_integration.fence_authorization_context
    checks = 0

    def revoke_before_final_fence(db, context):
        nonlocal checks
        checks += 1
        if checks > 1:
            raise AuthorizationStateUnavailable("revoked test credential")
        return original(db, context)

    monkeypatch.setattr(
        ai_integration, "fence_authorization_context", revoke_before_final_fence
    )
    response = client.post(
        f"/v1/ai/providers/{provider['id']}/test-connection",
        headers=headers,
        json={"version": provider["version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is False
    assert "permissions changed" in response.json()["error"]
    assert not calls
    receipts = list(db_session.scalars(select(AIProviderAttemptReceipt)))
    assert receipts and all(receipt.io_outcome == "not_sent" for receipt in receipts)


def test_named_connection_requires_admin_and_write_scope(
    client, provider_api, auth_headers, monkeypatch
):
    provider, _headers = provider_api
    calls = _transport(monkeypatch)
    for role in ("viewer", "analyst"):
        response = client.post(
            f"/v1/ai/providers/{provider['id']}/test-connection",
            headers=auth_headers[role],
            json={"version": provider["version"]},
        )
        assert response.status_code == 403
    assert not calls
