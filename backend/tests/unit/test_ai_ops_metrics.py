from datetime import datetime, timezone

import pytest

from app.models.ai_usage_event import AIUsageEvent
from app.services.ai_ops_metrics import _build_endpoint_health


@pytest.mark.parametrize(
    ("latencies", "expected"),
    [([], 0.0), ([None], 0.0), ([None, 20, 40], 30.0), ([0, None], 0.0)],
)
def test_endpoint_health_handles_missing_success_latency(latencies, expected):
    now = datetime.now(timezone.utc)
    events = [
        AIUsageEvent(success=True, latency_ms=value, created_at=now)
        for value in latencies
    ]
    events.append(AIUsageEvent(success=False, latency_ms=999, created_at=now))

    health = _build_endpoint_health(events)

    assert health.median_latency_ms == expected
    assert health.last_success_at == (now if latencies else None)
