import pytest

from app.core.config import get_settings
from app.schemas.ai import AISettingsUpdate


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_ai_settings_allow_private_network_http_for_local_endpoints(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")

    payload = AISettingsUpdate(base_url="http://localhost:11434/v1")

    assert payload.base_url == "http://localhost:11434/v1"


def test_ai_settings_reject_public_http_even_when_private_network_ai_is_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")

    with pytest.raises(ValueError, match="publicly routable hosts"):
        AISettingsUpdate(base_url="http://example.com/v1")
