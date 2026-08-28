import pytest
import redis

from app.services import auth_rate_limit


class _UnavailableRedis:
    def get(self, _key: str):
        raise redis.RedisError("redis unavailable")

    def eval(self, _script: str, _numkeys: int, *_args):
        raise redis.RedisError("redis unavailable")

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

    def get(self, key: str):
        return self.values.get(key)

    def eval(self, script: str, numkeys: int, *args):
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if "threatlens-auth-record-v1" in script:
            email_ip_count = self.incr(keys[0])
            ip_count = self.incr(keys[1])
            account_count = self.incr(keys[2])
            for key, count in zip(keys[:3], (email_ip_count, ip_count, account_count), strict=True):
                if count == 1:
                    self.expire(key, int(argv[1]))
            members = self.values.setdefault(keys[3], set())
            assert isinstance(members, set)
            members.add(str(argv[0]))
            self.expire(keys[3], int(argv[1]))
            distinct_ips = len(members)
            self.values[keys[7]] = str(argv[7])
            self.expire(keys[7], int(argv[1]))
            if email_ip_count >= int(argv[2]):
                self.set(keys[4], "1", ex=int(argv[5]), nx=True)
            if ip_count >= int(argv[3]):
                self.set(keys[5], "1", ex=int(argv[5]), nx=True)
            if account_count >= int(argv[4]) and distinct_ips >= int(argv[6]):
                self.set(keys[6], "1", ex=int(argv[5]), nx=True)
            return [email_ip_count, ip_count, account_count, distinct_ips]
        if "threatlens-auth-clear-v1" in script:
            expected = str(argv[0])
            current = str(self.values.get(keys[5], ""))
            if expected and current != expected:
                return 0
            self.delete(*keys[:5])
            return 1
        raise AssertionError("unexpected Lua script")

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
        self.fail_clear = True

    def eval(self, script: str, numkeys: int, *args):
        if self.fail_clear and "threatlens-auth-clear-v1" in script:
            raise redis.RedisError("redis unavailable")
        return super().eval(script, numkeys, *args)


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
        ("pending@example.com", "203.0.113.10"),
        ("user@example.com", "203.0.113.10"),
    ]:
        auth_rate_limit._emergency_clear_login_failures(email, ip)
        auth_rate_limit._emergency_clear_password_verification_failures(email, ip)
        auth_rate_limit._emergency_clear_mfa_action_failures(email, ip)
        auth_rate_limit._emergency_clear_self_registration_attempts(email, ip)
    auth_rate_limit._emergency_failures.clear()
    auth_rate_limit._emergency_locks.clear()
    auth_rate_limit._emergency_account_ip_sets.clear()
    auth_rate_limit._emergency_versions.clear()
    yield
    for email, ip in [
        ("admin@example.com", "203.0.113.10"),
        ("admin@example.com", "203.0.113.11"),
        ("admin@example.com", "203.0.113.12"),
        ("admin@example.com", "203.0.113.99"),
        ("pending@example.com", "203.0.113.10"),
        ("user@example.com", "203.0.113.10"),
    ]:
        auth_rate_limit._emergency_clear_login_failures(email, ip)
        auth_rate_limit._emergency_clear_password_verification_failures(email, ip)
        auth_rate_limit._emergency_clear_mfa_action_failures(email, ip)
        auth_rate_limit._emergency_clear_self_registration_attempts(email, ip)
    auth_rate_limit._emergency_failures.clear()
    auth_rate_limit._emergency_locks.clear()
    auth_rate_limit._emergency_account_ip_sets.clear()
    auth_rate_limit._emergency_versions.clear()


def test_check_login_throttle_uses_local_emergency_state_when_backend_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")

    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")
    assert state.blocked is False
    assert state.backend_available is False


def test_record_login_failure_triggers_local_lockout_when_backend_unavailable(
    monkeypatch,
):
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


def test_ip_wide_lock_uses_distinct_higher_threshold(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_ip_max_attempts", 3)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("first@example.com", "203.0.113.10")
    auth_rate_limit.record_login_failure("second@example.com", "203.0.113.10")
    assert (
        auth_rate_limit.check_login_throttle(
            "fresh@example.com", "203.0.113.10"
        ).blocked
        is False
    )

    auth_rate_limit.record_login_failure("third@example.com", "203.0.113.10")
    assert (
        auth_rate_limit.check_login_throttle(
            "fresh@example.com", "203.0.113.10"
        ).blocked
        is True
    )


def test_password_verification_throttle_uses_a_separate_namespace(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_password_verification_failure(
        "admin@example.com", "203.0.113.10"
    )

    verification_state = auth_rate_limit.check_password_verification_throttle(
        "admin@example.com", "203.0.113.10"
    )
    login_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )

    assert verification_state.blocked is True
    assert login_state.blocked is False


def test_sensitive_mfa_throttle_uses_a_separate_namespace(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_mfa_action_failure("admin@example.com", "203.0.113.10")

    mfa_state = auth_rate_limit.check_mfa_action_throttle(
        "admin@example.com", "203.0.113.10"
    )
    login_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
    password_state = auth_rate_limit.check_password_verification_throttle(
        "admin@example.com", "203.0.113.10"
    )

    assert mfa_state.blocked is True
    assert login_state.blocked is False
    assert password_state.blocked is False


def test_self_registration_throttle_uses_a_separate_namespace(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_self_registration_attempt(
        "pending@example.com", "203.0.113.10"
    )

    registration_state = auth_rate_limit.check_self_registration_throttle(
        "pending@example.com", "203.0.113.10"
    )
    login_state = auth_rate_limit.check_login_throttle(
        "pending@example.com", "203.0.113.10"
    )

    assert registration_state.blocked is True
    assert login_state.blocked is False


def test_invalid_oidc_callbacks_are_rate_limited_per_source_ip(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_ip_max_attempts", 10)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_invalid_oidc_callback("203.0.113.10")
    auth_rate_limit.record_invalid_oidc_callback("203.0.113.10")

    assert auth_rate_limit.check_oidc_callback_throttle("203.0.113.10").blocked
    assert not auth_rate_limit.check_oidc_callback_throttle("203.0.113.11").blocked
    assert not auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    ).blocked


def test_clear_login_failures_removes_existing_locks(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    blocked_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
    assert blocked_state.blocked is True

    auth_rate_limit.clear_login_failures("admin@example.com", "203.0.113.10")

    cleared_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
    assert cleared_state.blocked is False


def test_login_throttle_email_lock_is_scoped_to_source_ip(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")

    blocked_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
    other_ip_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.11"
    )

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


def test_login_throttle_does_not_globally_lock_account_before_spray_threshold(
    monkeypatch,
):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    for ip in ("203.0.113.10", "203.0.113.11"):
        auth_rate_limit.record_login_failure("admin@example.com", ip)
        auth_rate_limit.record_login_failure("admin@example.com", ip)

    blocked_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
    fresh_ip_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.99"
    )

    assert blocked_state.blocked is True
    assert fresh_ip_state.blocked is False


def test_atomic_login_throttle_does_not_depend_on_client_set_helpers(monkeypatch):
    redis_client = _MemoryRedisWithoutSets()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    for ip in ("203.0.113.10", "203.0.113.11", "203.0.113.12"):
        auth_rate_limit.record_login_failure("admin@example.com", ip)
        auth_rate_limit.record_login_failure("admin@example.com", ip)

    blocked_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
    fresh_ip_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.99"
    )

    assert blocked_state.blocked is True
    assert fresh_ip_state.blocked is True


def test_successful_login_clear_removes_local_emergency_state(monkeypatch):
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    blocked_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
    assert blocked_state.blocked is True

    auth_rate_limit.clear_login_failures("admin@example.com", "203.0.113.10")
    cleared_state = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
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


def test_failed_clear_is_not_replayed_over_a_newer_failure(monkeypatch):
    redis_client = _DeleteFlakyRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    assert (
        auth_rate_limit.check_login_throttle(
            "admin@example.com", "203.0.113.10"
        ).blocked
        is True
    )

    ticket = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
    auth_rate_limit.clear_login_failures(
        "admin@example.com",
        "203.0.113.10",
        observed_failure_version=ticket.failure_version,
    )

    redis_client.fail_clear = False
    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    auth_rate_limit.clear_login_failures(
        "admin@example.com",
        "203.0.113.10",
        observed_failure_version=ticket.failure_version,
    )
    state = auth_rate_limit.check_login_throttle("admin@example.com", "203.0.113.10")

    assert state.blocked is True


def test_success_clear_preserves_global_ip_abuse_state(monkeypatch):
    redis_client = _MemoryRedis()
    monkeypatch.setattr(auth_rate_limit, "redis_client", redis_client)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_ip_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    auth_rate_limit.record_login_failure("admin@example.com", "203.0.113.10")
    ticket = auth_rate_limit.check_login_throttle(
        "admin@example.com", "203.0.113.10"
    )
    auth_rate_limit.clear_login_failures(
        "admin@example.com",
        "203.0.113.10",
        observed_failure_version=ticket.failure_version,
    )

    assert auth_rate_limit.check_login_throttle(
        "fresh@example.com", "203.0.113.10"
    ).blocked
