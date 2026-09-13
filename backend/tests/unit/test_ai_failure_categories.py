from types import SimpleNamespace

import httpx
import pytest

from app.services.ai_failure_categories import provider_usage_identity, transport_failure_category
from app.services.ai_provider_client import AIIntegrationError, call_ai_json
from app.services.outbound_deadline import OutboundDeadlineExceeded, OutboundDNSDeadlineExceeded


@pytest.mark.parametrize("kind,category,outcome", [
    (OutboundDeadlineExceeded, "total_deadline", "ambiguous"),
    (OutboundDNSDeadlineExceeded, "dns_deadline", "not_sent"),
    (httpx.ReadTimeout, "read_timeout", "ambiguous"),
    (httpx.WriteTimeout, "write_timeout", "ambiguous"),
    (httpx.ConnectTimeout, "connect_timeout", "not_sent"),
    (httpx.PoolTimeout, "pool_timeout", "not_sent"),
])
def test_transport_deadlines_use_types_not_error_text(kind, category, outcome):
    def handler(_request):
        raise kind("Arbitrary localized diagnostic without a time-related keyword")

    active = SimpleNamespace(ai_enabled=True, ai_configured=True, provider_type="openai_compatible",
        base_url="https://provider.example/v1", model="test", api_key=None, temperature=0.2,
        max_completion_tokens=500, request_timeout_seconds=5)
    with pytest.raises(AIIntegrationError) as caught:
        call_ai_json(active, messages=[{"role": "user", "content": "Return JSON."}],
            client_factory=lambda **_kwargs: httpx.Client(transport=httpx.MockTransport(handler)))
    assert caught.value.failure_category == category
    assert caught.value.provider_io_outcome == outcome
    assert caught.value.latency_ms is not None


def test_transport_cause_chain_and_expired_total_deadline_take_precedence():
    error = httpx.ConnectError("connection wrapper")
    error.__cause__ = OutboundDNSDeadlineExceeded("resolver")
    assert transport_failure_category(error) == "dns_deadline"
    error = httpx.ReadTimeout("read")
    error._threatlens_deadline_recorded = True
    assert transport_failure_category(error) == "total_deadline"
    assert transport_failure_category(ValueError("timeout appears in unrelated text")) == "transport"


def test_usage_identity_never_mistakes_new_legacy_for_unknown_historical_events():
    assert provider_usage_identity(SimpleNamespace()) == {
        "provider_id": None, "provider_version": None, "provider_name": "Legacy settings",
    }
