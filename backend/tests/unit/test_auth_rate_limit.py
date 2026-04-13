import pytest
import redis

from app.services import auth_rate_limit


class _UnavailableRedis:
    def ttl(self, _key: str):
        raise redis.RedisError("redis unavailable")

    def incr(self, _key: str):
        raise redis.RedisError("redis unavailable")

    def expire(self, _key: str, _seconds: int):
        raise redis.RedisError("redis unavailable")

    def set(self, _key: str, _value: str, ex: int, nx: bool):
        raise redis.RedisError("redis unavailable")

    def delete(self, *_keys: str):
        raise redis.RedisError("redis unavailable")


class _MemoryRedis:
    def __init__(self):
        self.values: dict[str, int | str] = {}
        self.ttls: dict[str, int] = {}

    def ttl(self, key: str):
        return self.ttls.get(key, -2)

    def incr(self, key: str):
        current = int(self.values.get(key, 0)) + 1
        self.values[key] = current
        return current

    def expire(self, key: str, seconds: int):
        self.ttls[key] = seconds
        return True

    def set(self, key: str, value: str, ex: int, nx: bool):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = ex
        return True

    def delete(self, *keys: str):
        for key in keys:
            self.values.pop(key, None)
            self.ttls.pop(key, None)
        return len(keys)


class _DeleteFlakyRedis(_MemoryRedis):
    def __init__(self):
        super().__init__()
        self.fail_delete = True

    def delete(self, *keys: str):
        if self.fail_delete:
            raise redis.RedisError("redis unavailable")
        return super().delete(*keys)


@pytest.fixture(autouse=True)
def _reset_emergency_state():
    auth_rate_limit._emergency_clear_login_failures("admin@example.com", "203.0.113.10")
    auth_rate_limit._emergency_clear_login_failures("admin@example.com", "203.0.113.11")
    auth_rate_limit._emergency_clear_login_failures("user@example.com", "203.0.113.10")
    auth_rate_limit._pending_redis_clears.clear()
    yield
    auth_rate_limit._emergency_clear_login_failures("admin@example.com", "203.0.113.10")
    auth_rate_limit._emergency_clear_login_failures("admin@example.com", "203.0.113.11")
    auth_rate_limit._emergency_clear_login_failures("user@example.com", "203.0.113.10")
    auth_rate_limit._pending_redis_clears.clear()


def test_check_login_throttle_uses_local_emergency_state_when_backend_unavailable(monkeypatch):
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")

    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert state.blocked is False
    assert state.backend_available is False


def test_record_login_failure_triggers_local_lockout_when_backend_unavailable(monkeypatch):
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert state.blocked is True
    assert state.retry_after_seconds == 120
    assert state.backend_available is False


def test_clear_login_failures_ignores_backend_unavailability(monkeypatch):
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    auth_rate_limit.clear_login_failures("admin@example.com", "203.0.113.10")
    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert state.blocked is False
    assert state.backend_available is False


def test_login_throttle_blocks_after_repeated_failures(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")

    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert state.blocked is True
    assert state.retry_after_seconds == 120


def test_clear_login_failures_removes_existing_locks(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    blocked_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert blocked_state.blocked is True

    auth_rate_limit.clear_login_failures("admin@example.com", "203.0.113.10")

    cleared_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert cleared_state.blocked is False


def test_successful_login_clear_removes_local_emergency_state(monkeypatch):
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    blocked_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert blocked_state.blocked is True

    auth_rate_limit.clear_login_failures("admin@example.com", "203.0.113.10")
    cleared_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert cleared_state.blocked is False


def test_pending_redis_clear_is_replayed_after_backend_recovers(monkeypatch):
    redis_client = _DeleteFlakyRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    assert auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10").blocked is True

    auth_rate_limit.clear_login_failures("admin@example.com", "203.0.113.10")
    assert auth_rate_limit._pending_redis_clears

    redis_client.fail_delete = False
    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")

    assert state.blocked is False
    assert auth_rate_limit._pending_redis_clears == set()
