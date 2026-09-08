from datetime import datetime, timedelta, timezone

import pytest

from app.models.ai_usage_event import AIUsageEvent
from app.services.ai_ops_metrics import _build_endpoint_health


@pytest.mark.parametrize(
    ("latencies", "expected"),
    [([], 0.0), ([None], 0.0), ([None, 20, 40], 30.0), ([0, None], 0.0)],
)
def test_endpoint_health_handles_missing_success_latency(
    db_session, latencies, expected
):
    now = datetime.now(timezone.utc)
    events = [
        AIUsageEvent(
            feature_type="summary", success=True, latency_ms=value, created_at=now
        )
        for value in latencies
    ]
    events.append(
        AIUsageEvent(
            feature_type="summary", success=False, latency_ms=999, created_at=now
        )
    )

    db_session.add_all(events)
    db_session.flush()
    health = _build_endpoint_health(db_session, since=now - timedelta(days=1), now=now)

    assert health.median_latency_ms == expected
    assert health.last_success_at == (now if latencies else None)
