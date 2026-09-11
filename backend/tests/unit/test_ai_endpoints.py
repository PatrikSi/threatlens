"""Credential binding is operator-controlled and shared by legacy write/runtime paths."""

from types import SimpleNamespace

import pytest

from app.core.ai_endpoints import (
    DEFAULT_AI_API_KEY_BASE_URL,
    GEMINI_COMPATIBLE_BASE_URL,
    matches_ai_key_origin,
    validate_ai_key_base_url,
)
from app.core.config import get_settings
from app.schemas.ai import AISettingsUpdate
from app.schemas.ai_providers import AIProviderCreate
from app.services.ai_config import resolve_ai_api_key_for_base_url
from app.services.ai_provider_client import (
    AIIntegrationError,
    build_chat_completion_url,
    call_ai_json,
)


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        (
            "https://generativelanguage.googleapis.com",
            f"{GEMINI_COMPATIBLE_BASE_URL}chat/completions",
        ),
        (
            "https://generativelanguage.googleapis.com/v1beta/",
            f"{GEMINI_COMPATIBLE_BASE_URL}chat/completions",
        ),
        (GEMINI_COMPATIBLE_BASE_URL, f"{GEMINI_COMPATIBLE_BASE_URL}chat/completions"),
        (
            f"{GEMINI_COMPATIBLE_BASE_URL}chat/completions",
            f"{GEMINI_COMPATIBLE_BASE_URL}chat/completions",
        ),
        ("https://api.openai.com", "https://api.openai.com/v1/chat/completions"),
        ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
        (
            "https://provider.example/custom/api/",
            "https://provider.example/custom/api/chat/completions",
        ),
    ],
)
def test_compatible_completion_routes(endpoint, expected):
    assert build_chat_completion_url(endpoint) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.openai.com/v1",
        "https://API.OPENAI.COM:443/v1/",
        "https://api.openai.com./custom-path",
    ],
)
def test_default_binding_preserves_openai_credential_destination(endpoint):
    assert matches_ai_key_origin(endpoint, DEFAULT_AI_API_KEY_BASE_URL)
    assert resolve_ai_api_key_for_base_url(endpoint, "synthetic-key") == "synthetic-key"


@pytest.mark.parametrize(
    "endpoint",
    [
        None,
        "",
        "not-a-url",
        "https://[invalid",
        "https://api.openai.com:invalid/v1",
        "https://api.openai.com:0/v1",
        "https://api.openai.com:444/v1",
        "http://api.openai.com/v1",
        "https://api.openai.com.attacker.example/v1",
        "https://attacker.example/api.openai.com",
        "https://api.openai.com@attacker.example",
        "https://user@api.openai.com/v1",
        "https://@api.openai.com/v1",
        "https://api.openai.com/v1?key=secret",
        "https://api.openai.com/v1#fragment",
        "https://api.openai.com/v1?",
        "https://api.openai.com/v1#",
        "https://api.openai.com\\@attacker.example",
        "https://api.openai.com/\npath",
        "https://api.openai.com/%s path",
        "https://%61pi.openai.com/v1",
        GEMINI_COMPATIBLE_BASE_URL,
    ],
)
def test_environment_credential_is_never_resolved_for_untrusted_or_malformed_urls(
    endpoint,
):
    assert not matches_ai_key_origin(endpoint, DEFAULT_AI_API_KEY_BASE_URL)
    assert resolve_ai_api_key_for_base_url(endpoint, "synthetic-key") is None


def test_legacy_binding_can_be_configured_without_expanding_its_trusted_origins(
    monkeypatch,
):
    monkeypatch.setenv("AI_API_KEY", "synthetic-gemini-key")
    monkeypatch.setenv("AI_API_KEY_BASE_URL", GEMINI_COMPATIBLE_BASE_URL)
    get_settings.cache_clear()
    assert (
        AISettingsUpdate(base_url=GEMINI_COMPATIBLE_BASE_URL).base_url
        == GEMINI_COMPATIBLE_BASE_URL
    )
    assert (
        resolve_ai_api_key_for_base_url(
            GEMINI_COMPATIBLE_BASE_URL, "synthetic-gemini-key"
        )
        == "synthetic-gemini-key"
    )
    assert (
        resolve_ai_api_key_for_base_url(
            DEFAULT_AI_API_KEY_BASE_URL, "synthetic-gemini-key"
        )
        is None
    )
    with pytest.raises(ValueError, match="AI_API_KEY_BASE_URL"):
        AISettingsUpdate(base_url=DEFAULT_AI_API_KEY_BASE_URL)
    # A named provider's own credential remains independent of the environment binding.
    assert AIProviderCreate(
        name="OpenAI",
        base_url=DEFAULT_AI_API_KEY_BASE_URL,
        model="model",
        api_key="own-key",
    )


def test_origin_binding_compares_effective_port_and_does_not_restrict_paths():
    assert matches_ai_key_origin(
        "https://provider.example:8443/other", "https://provider.example:8443/v1"
    )
    assert not matches_ai_key_origin(
        "https://provider.example/v1", "https://provider.example:8443/v1"
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://provider.example",
        "https://user:key@provider.example",
        "https://provider.example?key=secret",
        "https://provider.example#fragment",
        "https://{{host}}",
        "https://provider.example:65536",
        "https://provider.example/\npath",
    ],
)
def test_invalid_operator_binding_is_rejected_at_settings_load(monkeypatch, endpoint):
    monkeypatch.setenv("AI_API_KEY_BASE_URL", endpoint)
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        get_settings()


def test_default_operator_binding_is_https():
    assert (
        validate_ai_key_base_url(DEFAULT_AI_API_KEY_BASE_URL)
        == DEFAULT_AI_API_KEY_BASE_URL
    )
    assert get_settings().ai_api_key_base_url == DEFAULT_AI_API_KEY_BASE_URL


@pytest.mark.parametrize(
    "method", ["generateContent", "streamGenerateContent", "%67enerateContent"]
)
def test_native_gemini_endpoints_fail_before_client_creation(method):
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:{method}"
    for schema, kwargs in [
        (AISettingsUpdate, {}),
        (AIProviderCreate, {"name": "Gemini", "model": "model"}),
    ]:
        with pytest.raises(ValueError, match="/v1beta/openai/"):
            schema(base_url=endpoint, **kwargs)
    active = SimpleNamespace(
        ai_enabled=True, ai_configured=True, base_url=endpoint, model="model"
    )
    with pytest.raises(AIIntegrationError, match="/v1beta/openai/") as error:
        call_ai_json(
            active,
            messages=[],
            client_factory=lambda **_: pytest.fail("Must not create a client"),
        )
    assert error.value.retryable is False
    assert error.value.provider_io_outcome == "not_sent"
