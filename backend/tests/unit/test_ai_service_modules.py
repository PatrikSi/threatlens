from __future__ import annotations

from typing import Any

import app.services.ai_integration as ai_integration
from app.services import ai_normalization, ai_prompting, ai_provider_client


def test_ai_integration_preserves_extracted_symbol_import_paths() -> None:
    assert ai_integration.AIIntegrationError is ai_provider_client.AIIntegrationError
    assert ai_integration.AICompletionResult is ai_provider_client.AICompletionResult
    assert ai_integration._build_chat_completion_url is ai_provider_client.build_chat_completion_url
    assert ai_integration._parse_ai_json_content is ai_provider_client.parse_ai_json_content
    assert ai_integration._build_daily_brief_messages is ai_prompting.build_daily_brief_messages
    assert ai_integration._normalize_string_list is ai_normalization.normalize_string_list


def test_legacy_provider_wrapper_uses_legacy_client_factory(monkeypatch) -> None:
    active = object()
    client_factory = object()
    expected = object()
    captured: dict[str, Any] = {}

    def fake_provider_call(active_arg, **kwargs):
        captured["active"] = active_arg
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(ai_integration, "build_safe_http_client", client_factory)
    monkeypatch.setattr(ai_integration, "_provider_call_ai_json", fake_provider_call)

    result = ai_integration._call_ai_json(
        active,  # type: ignore[arg-type]
        messages=[{"role": "user", "content": "{}"}],
        max_completion_tokens=321,
    )

    assert result is expected
    assert captured == {
        "active": active,
        "messages": [{"role": "user", "content": "{}"}],
        "max_completion_tokens": 321,
        "client_factory": client_factory,
    }


def test_provider_parser_and_normalization_remain_available_in_focused_modules() -> None:
    assert ai_provider_client.parse_ai_json_content("```json\n{\"ok\": true}\n```") == {"ok": True}
    assert ai_normalization.normalize_string_list(["first", {"text": "second"}, "first"]) == ["first", "second"]
