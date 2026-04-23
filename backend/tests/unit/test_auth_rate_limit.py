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

    def sadd(self, _key: str, *_values: str):
        raise redis.RedisError("redis unavailable")

    def scard(self, _key: str):
        raise redis.RedisError("redis unavailable")

    def delete(self, *_keys: str):
        raise redis.RedisError("redis unavailable")


class _MemoryRedis:
    def __init__(self):
        self.values: dict[str, object] = {}
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

    def sadd(self, key: str, *values: str):
        current = self.values.get(key)
        members = set(current) if isinstance(current, set) else set()
        added = 0
        for value in values:
            if value in members:
                continue
            members.add(value)
            added += 1
        self.values[key] = members
        return added

    def scard(self, key: str):
        current = self.values.get(key)
        if isinstance(current, set):
            return len(current)
        return 0

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


class _MemoryRedisWithoutSets(_MemoryRedis):
    sadd = None
    scard = None


@pytest.fixture(autouse=True)
def _reset_emergency_state():
    for email, ip in [
        ("admin@example.com", "203.0.113.10"),
        ("admin@example.com", "203.0.113.11"),
        ("admin@example.com", "203.0.113.12"),
        ("admin@example.com", "203.0.113.99"),
        ("user@example.com", "203.0.113.10"),
    ]:
        auth_rate_limit._emergency_clear_login_failures(email, ip)
        auth_rate_limit._emergency_clear_password_verification_failures(email, ip)
    auth_rate_limit._pending_redis_clears.clear()
    auth_rate_limit._emergency_account_ip_sets.clear()
    yield
    for email, ip in [
        ("admin@example.com", "203.0.113.10"),
        ("admin@example.com", "203.0.113.11"),
        ("admin@example.com", "203.0.113.12"),
        ("admin@example.com", "203.0.113.99"),
        ("user@example.com", "203.0.113.10"),
    ]:
        auth_rate_limit._emergency_clear_login_failures(email, ip)
        auth_rate_limit._emergency_clear_password_verification_failures(email, ip)
    auth_rate_limit._pending_redis_clears.clear()
    auth_rate_limit._emergency_account_ip_sets.clear()


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


def test_password_verification_throttle_uses_a_separate_namespace(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_password_verification_failure("admin@example.com", "203.0.113.10")

    verification_state = auth_rate_limit.check_password_verification_throttle("admin@example.com", "203.0.113.10")
    login_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")

    assert verification_state.blocked is True
    assert login_state.blocked is False


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


def test_login_throttle_email_lock_is_scoped_to_source_ip(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")

    blocked_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    other_ip_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.11")

    assert blocked_state.blocked is True
    assert other_ip_state.blocked is False


def test_login_throttle_blocks_distributed_account_spray(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    for ip in ("203.0.113.10", "203.0.113.11", "203.0.113.12"):
        auth_rate_limit.record_login_failure("admin@example.com", ip)
        auth_rate_limit.record_login_failure("admin@example.com", ip)

    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.99")

    assert state.blocked is True
    assert state.retry_after_seconds == 120


def test_login_throttle_does_not_globally_lock_account_before_spray_threshold(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    for ip in ("203.0.113.10", "203.0.113.11"):
        auth_rate_limit.record_login_failure("admin@example.com", ip)
        auth_rate_limit.record_login_failure("admin@example.com", ip)

    blocked_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    fresh_ip_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.99")

    assert blocked_state.blocked is True
    assert fresh_ip_state.blocked is False


def test_login_throttle_degrades_cleanly_without_redis_set_operations(monkeypatch):
    redis_client = _MemoryRedisWithoutSets()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    for ip in ("203.0.113.10", "203.0.113.11", "203.0.113.12"):
        auth_rate_limit.record_login_failure("admin@example.com", ip)
        auth_rate_limit.record_login_failure("admin@example.com", ip)

    blocked_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    fresh_ip_state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.99")

    assert blocked_state.blocked is True
    assert fresh_ip_state.blocked is False


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


def test_emergency_login_throttle_blocks_distributed_account_spray(monkeypatch):
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    for ip in ("203.0.113.10", "203.0.113.11", "203.0.113.12"):
        auth_rate_limit.record_login_failure("admin@example.com", ip)
        auth_rate_limit.record_login_failure("admin@example.com", ip)

    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.99")

    assert state.blocked is True
    assert state.retry_after_seconds == 120
    assert state.backend_available is False


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
