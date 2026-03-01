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


def _reset_fallback_state():
    auth_rate_limit._fallback_failures.clear()
    auth_rate_limit._fallback_locks.clear()


def test_fallback_login_throttle_blocks_after_repeated_failures(monkeypatch):
    _reset_fallback_state()
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 3)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    for _ in range(3):
        auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")

    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert state.blocked is True
    assert state.retry_after_seconds is not None
    assert state.retry_after_seconds > 0


def test_fallback_login_throttle_resets_after_window(monkeypatch):
    _reset_fallback_state()
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 10)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 30)

    clock = {"value": 0.0}
    monkeypatch.setattr(auth_rate_limit.time, "monotonic", lambda: clock["value"])

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    clock["value"] = 11.0
    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")

    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert state.blocked is False


def test_clear_login_failures_clears_fallback_locks(monkeypatch):
    _reset_fallback_state()
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
