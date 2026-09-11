"""Persist larger budgets through the authenticated legacy and provider APIs."""

from app.core.config import get_settings
from app.services.ai_config import load_active_ai_settings


def test_report_budget_survives_settings_reload_and_provider_routing(
    client, auth_headers, db_session, monkeypatch
):
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    headers = auth_headers["admin"]
    saved = client.put(
        "/v1/ai/settings",
        headers=headers,
        json={
            "base_url": "https://provider.example/v1",
            "model": "default-model",
            "max_completion_tokens": 5000,
            "report_reserved_output_tokens": 32_768,
            "report_context_window_tokens": 131_072,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["max_completion_tokens"] == 5000
    assert saved.json()["report_reserved_output_tokens"] == 32_768
    provider = client.post(
        "/v1/ai/providers",
        headers=headers,
        json={
            "name": "Report provider",
            "base_url": "https://provider.example/v1",
            "model": "report-model",
            "max_completion_tokens": 2000,
        },
    )
    assert provider.status_code == 201, provider.text
    routing = client.get("/v1/ai/provider-routing", headers=headers)
    assert routing.status_code == 200, routing.text
    routed = client.put(
        "/v1/ai/provider-routing",
        headers=headers,
        json={**routing.json(), "report_provider_id": provider.json()["id"]},
    )
    assert routed.status_code == 200, routed.text
    db_session.expire_all()
    active = load_active_ai_settings(db_session, feature_type="report")
    assert str(active.provider_id) == provider.json()["id"]
    assert active.max_completion_tokens == 2000
    assert active.report_reserved_output_tokens == 32_768
    reloaded = client.get("/v1/ai/settings", headers=headers)
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["report_reserved_output_tokens"] == 32_768
    assert reloaded.json()["max_completion_tokens"] == 5000


def test_high_provider_limit_is_versioned_and_overflow_does_not_persist(
    client, auth_headers, monkeypatch
):
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    headers = auth_headers["admin"]
    fields = {
        "name": "Large outputs",
        "base_url": "https://provider.example/v1",
        "model": "model",
        "max_completion_tokens": 131_072,
    }
    created = client.post("/v1/ai/providers", headers=headers, json=fields)
    assert created.status_code == 201, created.text
    provider = created.json()
    assert provider["max_completion_tokens"] == 131_072
    rejected = client.put(
        f"/v1/ai/providers/{provider['id']}",
        headers=headers,
        json={
            **fields,
            "version": provider["version"],
            "max_completion_tokens": 131_073,
        },
    )
    assert rejected.status_code == 422, rejected.text
    current = client.get(f"/v1/ai/providers/{provider['id']}", headers=headers).json()
    assert current["version"] == provider["version"]
    assert current["max_completion_tokens"] == 131_072
    changed = client.put(
        f"/v1/ai/providers/{provider['id']}",
        headers=headers,
        json={
            **fields,
            "version": provider["version"],
            "max_completion_tokens": 65_536,
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["max_completion_tokens"] == 65_536
    assert changed.json()["version"] == provider["version"] + 1
