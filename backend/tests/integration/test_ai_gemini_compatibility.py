"""Exercise Gemini compatibility through the authenticated settings APIs."""

import json
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.models.ai_provider import AIProviderConfiguration
from app.services import ai_integration
from app.services.ai_config import get_or_create_ai_settings


GEMINI_ORIGIN = "https://generativelanguage.googleapis.com"
GEMINI_BASE = f"{GEMINI_ORIGIN}/v1beta/openai/"
GEMINI_COMPLETIONS = f"{GEMINI_BASE}chat/completions"
LEGACY_KEY = "synthetic-legacy-gemini-key"
NAMED_KEY = "synthetic-named-gemini-key"


@pytest.fixture(autouse=True)
def gemini_environment(monkeypatch, _stabilize_settings_env):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", LEGACY_KEY)
    monkeypatch.setenv("AI_API_KEY_BASE_URL", GEMINI_BASE)
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "false")
    # These API tests have no Celery workers; avoid broker inspection/retry waits.
    monkeypatch.setattr(
        "app.services.ai_ops._load_live_task_snapshot",
        lambda: (True, [], [], [], []),
    )
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture()
def provider_transport(monkeypatch):
    requests = []
    clients_created = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": json.loads(request.content)["model"],
                "choices": [
                    {"message": {"content": '{"ok":true,"message":"ready"}'}}
                ],
            },
        )

    def client_factory(**kwargs):
        clients_created.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(ai_integration, "build_safe_http_client", client_factory)
    return requests, clients_created


def _create_provider(client, headers, **changes):
    response = client.post(
        "/v1/ai/providers",
        headers=headers,
        json={
            "name": "Gemini",
            "base_url": GEMINI_ORIGIN,
            "model": "gemini-flash-latest",
            "api_key": NAMED_KEY,
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    assert NAMED_KEY not in response.text
    return response.json()


@pytest.mark.parametrize("base_url", [GEMINI_BASE, GEMINI_ORIGIN, f"{GEMINI_ORIGIN}/v1beta"])
def test_legacy_gemini_save_and_connection_use_explicit_environment_binding(
    client, auth_headers, provider_transport, base_url
):
    headers = auth_headers["admin"]
    saved = client.put(
        "/v1/ai/settings",
        headers=headers,
        json={"base_url": base_url, "model": "gemini-flash-latest"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["api_key_configured"] is True
    assert saved.json()["ai_configured"] is True
    assert LEGACY_KEY not in saved.text

    tested = client.post("/v1/ai/test-connection", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["success"] is True
    requests, _ = provider_transport
    assert len(requests) == 1
    assert str(requests[0].url) == GEMINI_COMPLETIONS
    assert requests[0].headers["authorization"] == f"Bearer {LEGACY_KEY}"
    assert json.loads(requests[0].content)["model"] == "gemini-flash-latest"
    assert LEGACY_KEY not in tested.text


def test_legacy_endpoint_outside_environment_binding_remains_unauthenticated(
    client, auth_headers, db_session, provider_transport
):
    row = get_or_create_ai_settings(db_session)
    row.base_url = "https://api.openai.com/v1"
    row.model = "legacy-openai-model"
    db_session.commit()
    headers = auth_headers["admin"]
    settings = client.get("/v1/ai/settings", headers=headers)
    assert settings.status_code == 200
    assert settings.json()["ai_configured"] is True
    assert settings.json()["api_key_configured"] is False
    tested = client.post("/v1/ai/test-connection", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["success"] is True
    requests, _ = provider_transport
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.openai.com/v1/chat/completions"
    assert "authorization" not in requests[0].headers
    assert json.loads(requests[0].content)["model"] == "legacy-openai-model"


def test_named_gemini_update_retains_own_key_and_clear_never_inherits_environment_key(
    client, auth_headers, provider_transport
):
    headers = auth_headers["admin"]
    provider = _create_provider(client, headers)
    updated = client.put(
        f"/v1/ai/providers/{provider['id']}",
        headers=headers,
        json={
            "version": provider["version"],
            "name": provider["name"],
            "base_url": GEMINI_COMPLETIONS,
            "model": "gemini-updated-model",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == provider["version"] + 1
    assert updated.json()["api_key_configured"] is True

    tested = client.post(
        f"/v1/ai/providers/{provider['id']}/test-connection",
        headers=headers,
        json={"version": updated.json()["version"]},
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["success"] is True
    requests, _ = provider_transport
    assert len(requests) == 1
    assert str(requests[0].url) == GEMINI_COMPLETIONS
    assert requests[0].headers["authorization"] == f"Bearer {NAMED_KEY}"
    assert json.loads(requests[0].content)["model"] == "gemini-updated-model"
    assert LEGACY_KEY not in tested.text and NAMED_KEY not in tested.text

    cleared = client.put(
        f"/v1/ai/providers/{provider['id']}",
        headers=headers,
        json={
            "version": updated.json()["version"],
            "name": provider["name"],
            "base_url": GEMINI_BASE,
            "model": "gemini-updated-model",
            "clear_api_key": True,
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["api_key_configured"] is False
    tested = client.post(
        f"/v1/ai/providers/{provider['id']}/test-connection",
        headers=headers,
        json={"version": cleared.json()["version"]},
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["success"] is True
    assert len(requests) == 2
    assert str(requests[1].url) == GEMINI_COMPLETIONS
    assert "authorization" not in requests[1].headers


@pytest.mark.parametrize("operation", ["legacy_update", "named_create", "named_update"])
@pytest.mark.parametrize("method", ["generateContent", "streamGenerateContent"])
def test_gemini_native_url_is_rejected_by_both_settings_apis(
    client, auth_headers, provider_transport, operation, method
):
    headers = auth_headers["admin"]
    payload = {
        "base_url": f"{GEMINI_ORIGIN}/v1beta/models/gemini-flash-latest:{method}",
        "model": "gemini-flash-latest",
    }
    if operation == "legacy_update":
        response = client.put("/v1/ai/settings", headers=headers, json=payload)
    elif operation == "named_create":
        response = client.post(
            "/v1/ai/providers", headers=headers,
            json=payload | {"name": "Native Gemini", "api_key": NAMED_KEY},
        )
    else:
        provider = _create_provider(client, headers)
        response = client.put(
            f"/v1/ai/providers/{provider['id']}", headers=headers,
            json=payload | {"name": provider["name"], "version": provider["version"]},
        )
        unchanged = client.get(f"/v1/ai/providers/{provider['id']}", headers=headers)
        assert unchanged.status_code == 200
        assert unchanged.json() == provider
    assert response.status_code == 422, response.text
    assert "/v1beta/openai" in response.text
    assert LEGACY_KEY not in response.text and NAMED_KEY not in response.text
    assert provider_transport == ([], [])


@pytest.mark.parametrize("named", [False, True], ids=["legacy", "named"])
def test_persisted_native_gemini_endpoint_fails_before_provider_io(
    client, auth_headers, db_session, provider_transport, named
):
    headers = auth_headers["admin"]
    native_url = f"{GEMINI_ORIGIN}/v1beta/models/gemini-flash-latest:generateContent"
    if named:
        provider = _create_provider(client, headers)
        row = db_session.get(AIProviderConfiguration, uuid.UUID(provider["id"]))
        row.base_url = native_url
        db_session.commit()
        response = client.post(
            f"/v1/ai/providers/{provider['id']}/test-connection",
            headers=headers, json={"version": provider["version"]},
        )
    else:
        row = get_or_create_ai_settings(db_session)
        row.base_url = native_url
        row.model = "gemini-flash-latest"
        db_session.commit()
        response = client.post("/v1/ai/test-connection", headers=headers)

    assert response.status_code == (422 if named else 200), response.text
    if named:
        assert response.json()["error"]["code"] == "provider_endpoint_invalid"
    else:
        assert response.json()["success"] is False
    assert "/v1beta/openai" in response.text
    assert LEGACY_KEY not in response.text and NAMED_KEY not in response.text
    assert provider_transport == ([], [])


@pytest.mark.parametrize(
    "invalid_url",
    [
        f"{GEMINI_ORIGIN}:invalid/v1beta/openai",
        "https://[2001:db8::1/v1",
    ],
    ids=["bad_port", "malformed_ipv6"],
)
def test_assigned_malformed_provider_stays_readable_and_can_be_repaired(
    client, auth_headers, db_session, provider_transport, invalid_url
):
    headers = auth_headers["admin"]
    provider = _create_provider(client, headers)
    routing = client.get("/v1/ai/provider-routing", headers=headers)
    assert routing.status_code == 200
    assigned = client.put(
        "/v1/ai/provider-routing", headers=headers,
        json=routing.json() | {"default_provider_id": provider["id"]},
    )
    assert assigned.status_code == 200, assigned.text
    row = db_session.get(AIProviderConfiguration, uuid.UUID(provider["id"]))
    row.base_url = invalid_url
    db_session.commit()

    settings = client.get("/v1/ai/settings", headers=headers)
    assert settings.status_code == 200, settings.text
    assert settings.json()["ai_configured"] is False
    detail = client.get(f"/v1/ai/providers/{provider['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["base_url"] == invalid_url
    tested = client.post(
        f"/v1/ai/providers/{provider['id']}/test-connection",
        headers=headers, json={"version": provider["version"]},
    )
    assert tested.status_code == 422, tested.text
    assert tested.json()["error"]["code"] == "provider_endpoint_invalid"
    assert LEGACY_KEY not in tested.text and NAMED_KEY not in tested.text
    assert provider_transport == ([], [])

    unsafe_repair = client.put(
        f"/v1/ai/providers/{provider['id']}", headers=headers,
        json={
            "version": provider["version"],
            "name": provider["name"],
            "base_url": GEMINI_BASE,
            "model": provider["model"],
        },
    )
    assert unsafe_repair.status_code == 409, unsafe_repair.text
    assert unsafe_repair.json()["error"]["code"] == "provider_credential_destination_changed"
    unchanged = client.get(f"/v1/ai/providers/{provider['id']}", headers=headers)
    assert unchanged.status_code == 200
    assert unchanged.json()["base_url"] == invalid_url
    assert unchanged.json()["version"] == provider["version"]
    assert unchanged.json()["api_key_configured"] is True

    repaired = client.put(
        f"/v1/ai/providers/{provider['id']}", headers=headers,
        json={
            "version": provider["version"],
            "name": provider["name"],
            "base_url": GEMINI_BASE,
            "model": provider["model"],
            "clear_api_key": True,
        },
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["api_key_configured"] is False
    assert repaired.json()["version"] == provider["version"] + 1
    settings = client.get("/v1/ai/settings", headers=headers)
    assert settings.status_code == 200
    assert settings.json()["ai_configured"] is True
