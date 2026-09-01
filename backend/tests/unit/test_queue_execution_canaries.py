from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import queue_execution_canaries as canaries


class _Redis:
    def __init__(self, values: dict[str, object] | None = None):
        self.values = values or {}
        self.set_calls: list[tuple[str, str, int]] = []
        self.closed = False

    def set(self, key: str, value: str, *, ex: int):
        self.values[key] = value
        self.set_calls.append((key, value, ex))

    def mget(self, keys: list[str]):
        return [self.values.get(key) for key in keys]

    def close(self):
        self.closed = True


def _settings(**overrides):
    values = {
        "redis_url": "redis://redis.internal/0",
        "ai_enabled": False,
        "beat_heartbeat_ttl_seconds": 180,
        "beat_heartbeat_stale_after_seconds": 180,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _payload(queue: str, observed_at: datetime, worker: str = "worker@node-1") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "queue": queue,
            "heartbeat_at": observed_at.isoformat(),
            "worker_name": worker,
        }
    )


def test_canary_write_validates_actual_routing_key_before_redis(monkeypatch):
    called = False

    def redis_client(*_args, **_kwargs):
        nonlocal called
        called = True
        return _Redis()

    monkeypatch.setattr(canaries, "redis_client_from_url", redis_client)

    result = canaries.write_queue_execution_canary(
        settings=_settings(),
        expected_queue="processing",
        actual_queue="default",
        worker_name="worker@node-1",
    )

    assert result.reason == "invalid"
    assert called is False


def test_canary_write_persists_only_bounded_execution_evidence(monkeypatch):
    client = _Redis()
    monkeypatch.setattr(
        canaries,
        "redis_client_from_url",
        lambda *_args, **_kwargs: client,
    )
    observed_at = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    result = canaries.write_queue_execution_canary(
        settings=_settings(),
        expected_queue="processing",
        actual_queue="processing",
        worker_name="worker /node-1",
        now=observed_at,
    )

    assert result.reason == "fresh"
    assert result.worker_name
    assert len(result.worker_name) <= canaries.MAX_WORKER_NAME_CHARS
    assert client.closed is True
    assert len(client.set_calls) == 1
    key, raw_payload, ttl = client.set_calls[0]
    assert key == "threatlens:health:queue-canary:processing"
    assert ttl == 540
    assert json.loads(raw_payload) == {
        "heartbeat_at": observed_at.isoformat(),
        "queue": "processing",
        "schema_version": 1,
        "worker_name": result.worker_name,
    }


def test_canary_read_distinguishes_fresh_stale_missing_and_invalid(monkeypatch):
    now = datetime(2026, 9, 1, 12, 10, tzinfo=timezone.utc)
    prefix = canaries.QUEUE_CANARY_KEY_PREFIX
    client = _Redis(
        {
            f"{prefix}:ingest": _payload("ingest", now - timedelta(seconds=30)),
            f"{prefix}:processing": _payload(
                "processing",
                now - timedelta(seconds=181),
            ),
            f"{prefix}:maintenance": "not-json",
        }
    )
    monkeypatch.setattr(
        canaries,
        "redis_client_from_url",
        lambda *_args, **_kwargs: client,
    )

    result = canaries.read_queue_execution_canaries(
        settings=_settings(),
        now=now,
    )

    assert result["ingest"].reason == "fresh"
    assert result["ingest"].age_seconds == 30
    assert result["processing"].reason == "stale"
    assert result["processing"].age_seconds == 181
    assert result["notifications"].reason == "missing"
    assert result["maintenance"].reason == "invalid"


def test_canary_read_fails_closed_on_redis_or_mget_shape_failure(monkeypatch):
    monkeypatch.setattr(
        canaries,
        "redis_client_from_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    unavailable = canaries.read_queue_execution_canaries(settings=_settings())
    assert {entry.reason for entry in unavailable.values()} == {"redis_unavailable"}

    client = _Redis()
    client.mget = lambda _keys: []
    monkeypatch.setattr(
        canaries,
        "redis_client_from_url",
        lambda *_args, **_kwargs: client,
    )
    malformed = canaries.read_queue_execution_canaries(settings=_settings())
    assert {entry.reason for entry in malformed.values()} == {"redis_unavailable"}


def test_canary_read_rejects_future_oversized_and_wrong_schema_payloads(
    monkeypatch,
):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    prefix = canaries.QUEUE_CANARY_KEY_PREFIX
    wrong_schema = json.loads(_payload("processing", now))
    wrong_schema["schema_version"] = 2
    client = _Redis(
        {
            f"{prefix}:ingest": _payload("ingest", now + timedelta(seconds=6)),
            f"{prefix}:processing": json.dumps(wrong_schema),
            f"{prefix}:notifications": "x" * (
                canaries.MAX_CANARY_PAYLOAD_BYTES + 1
            ),
        }
    )
    monkeypatch.setattr(
        canaries,
        "redis_client_from_url",
        lambda *_args, **_kwargs: client,
    )

    result = canaries.read_queue_execution_canaries(
        settings=_settings(),
        now=now,
    )

    assert result["ingest"].reason == "future"
    assert result["processing"].reason == "invalid"
    assert result["notifications"].reason == "invalid"
