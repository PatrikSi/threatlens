import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.ai_providers import AIProviderCreate, AIProviderResponse


def test_named_provider_does_not_inherit_environment_key_destination(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "environment-secret")
    get_settings.cache_clear()
    provider = AIProviderCreate(
        name="Independent",
        base_url="https://provider.example/v1",
        model="model",
        api_key="independent-secret",
    )
    assert provider.base_url == "https://provider.example/v1"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com/v1",
        "https://user:secret@example.com/v1",
        "https://example.com/v1?key=secret",
        "https://example.com/v1#fragment",
        "file:///model",
        "https://127.0.0.1/v1",
        "https://100.64.0.1/v1",
        "https://example.com:invalid/v1",
        "https://{{host}}/v1",
    ],
)
def test_provider_rejects_unsafe_endpoint(endpoint):
    with pytest.raises(ValidationError):
        AIProviderCreate(name="Invalid", base_url=endpoint, model="model")


@pytest.mark.parametrize(
    "key", ["", " ", "key\nsecond-line", "key\x00", "keyé", "x" * 16385]
)
def test_provider_rejects_invalid_authorization_header(key):
    with pytest.raises(ValidationError):
        AIProviderCreate(
            name="Invalid",
            base_url="https://example.com/v1",
            model="model",
            api_key=key,
        )


def test_provider_rejects_conflicting_key_actions():
    with pytest.raises(ValidationError, match="either"):
        AIProviderCreate(
            name="Invalid",
            base_url="https://example.com/v1",
            model="model",
            api_key="secret",
            clear_api_key=True,
        )


def test_provider_response_remains_readable_after_network_policy_change():
    now = datetime.now(timezone.utc)
    response = AIProviderResponse(
        id=uuid.uuid4(),
        name="Private",
        base_url="http://localhost:11434/v1",
        model="local",
        version=1,
        api_key_configured=False,
        credential_error=None,
        created_at=now,
        updated_at=now,
    )
    assert response.base_url == "http://localhost:11434/v1"
