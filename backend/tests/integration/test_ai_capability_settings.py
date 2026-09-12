import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.core.config import get_settings
from app.schemas.ai_provider_capabilities import CAPABILITY_FIELDS
from app.services import ai_integration
from app.services.ai_config import load_active_ai_settings


CAPABILITIES = {
    "request_dialect": "chat_completions_modern", "temperature": None,
    "reasoning_effort": "low", "structured_output_mode": "json_object",
    "model_context_window_tokens": 32768, "model_max_output_tokens": 8192,
}


@pytest.fixture(autouse=True)
def enable_ai(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()


@pytest.mark.parametrize("named", [False, True])
def test_capabilities_roundtrip_and_old_clients_preserve_additive_settings(
    client, auth_headers, db_session, monkeypatch, named,
):
    headers = auth_headers["admin"]
    url = "/ai/providers" if named else "/ai/settings"
    request = {"base_url": "https://example.com/v1", "model": "explicit-model", **CAPABILITIES}
    if named:
        request.update(name="Named", api_key="independent-key")
    response = client.post(url, json=request, headers=headers) if named else client.put(url, json=request, headers=headers)
    assert response.status_code in (200, 201), response.text
    saved = response.json()
    assert all(saved[field] == value for field, value in CAPABILITIES.items())
    if named:
        url += f"/{saved['id']}"
        request = {"version": saved["version"], "name": "Named", "base_url": request["base_url"], "model": "changed"}
    else:
        request = {"base_url": request["base_url"], "model": "changed"}
    response = client.put(url, json=request, headers=headers)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert all(saved[field] == CAPABILITIES[field] for field in CAPABILITY_FIELDS)
    # Old clients still send the legacy temperature default; explicitly omit it
    # again to demonstrate that NULL survives database insertion and update.
    request.update(temperature=None, **{field: CAPABILITIES[field] for field in CAPABILITY_FIELDS})
    if named:
        request["version"] = saved["version"]
    saved = client.put(url, json=request, headers=headers).json()
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    monkeypatch.setattr(ai_integration, "build_safe_http_client", lambda **_kwargs: httpx.Client(transport=httpx.MockTransport(respond)))
    if named:
        result = client.post(f"{url}/test-connection", json={"version": saved["version"]}, headers=headers)
    else:
        result = client.post("/ai/test-connection", headers=headers)
    assert result.status_code == 200 and result.json()["success"] is True, result.text
    assert calls[0]["reasoning_effort"] == "low"
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "temperature" not in calls[0] and "max_tokens" not in calls[0]
    assert calls[0]["max_completion_tokens"] == (128 if named else 5000)


def test_capability_migration_preserves_legacy_defaults(db_session, monkeypatch):
    load_active_ai_settings(db_session)
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0096_ai_provider_capabilities.py"
    spec = importlib.util.spec_from_file_location("migration0096", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db_session.connection())))
    migration.downgrade()
    assert db_session.scalar(text("SELECT temperature FROM ai_settings")) == 0.2
    migration.upgrade()
    assert db_session.scalar(text("SELECT request_dialect FROM ai_settings")) == "chat_completions"
    db_session.execute(text("UPDATE ai_settings SET temperature=NULL"))
    with pytest.raises(RuntimeError, match="legacy AI capabilities"):
        migration.downgrade()
