from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.ai_provider import AIProviderConfiguration
from app.models.ai_settings import AISettings
from app.schemas.ai import AISettingsUpdate
from app.schemas.ai_providers import AIProviderCreate, AIProviderUpdate
from app.schemas.ai_provider_admission import admission_limit_values
from app.services.ai_config import apply_ai_settings_update
from app.services.ai_provider_protocol import provider_capability_snapshot
from app.services.ai_providers import _apply_provider_fields


@pytest.mark.parametrize("schema", [AISettingsUpdate, AIProviderCreate])
@pytest.mark.parametrize("field, value", [
    ("max_concurrent_requests", -1), ("max_concurrent_requests", 1001),
    ("hourly_token_budget", -1), ("hourly_token_budget", 1_000_000_000_001),
    ("hourly_token_budget", float("nan")), ("hourly_token_budget", 1.5),
])
def test_admission_limits_reject_invalid_values(schema, field, value):
    with pytest.raises(ValidationError):
        schema.model_validate({"name": "Provider", "base_url": "https://example.com", "model": "model", field: value})


@pytest.mark.parametrize("named", [False, True])
def test_old_client_omission_preserves_limits_and_zero_explicitly_clears_them(named):
    model = AIProviderConfiguration if named else AISettings
    settings = model(max_concurrent_requests=5, hourly_token_budget=1_000_000_000_000)
    payload_type = AIProviderUpdate if named else AISettingsUpdate
    values = {"name": "Provider", "version": 1, "base_url": "https://example.com", "model": "model"}
    apply = _apply_provider_fields if named else apply_ai_settings_update
    apply(settings, payload_type.model_validate(values))
    assert admission_limit_values(settings) == {"max_concurrent_requests": 5, "hourly_token_budget": 1_000_000_000_000}
    apply(settings, payload_type.model_validate({**values, "max_concurrent_requests": 0, "hourly_token_budget": 0}))
    assert admission_limit_values(settings) == {"max_concurrent_requests": 0, "hourly_token_budget": 0}


def test_admission_settings_do_not_change_wire_capability_fingerprints():
    active = SimpleNamespace(max_concurrent_requests=2, hourly_token_budget=10000)
    assert provider_capability_snapshot(active) == {}
    assert admission_limit_values(SimpleNamespace()) == {"max_concurrent_requests": 0, "hourly_token_budget": 0}
