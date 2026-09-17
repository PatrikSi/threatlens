from types import SimpleNamespace

import pytest
import redis
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.db.budgets import DatabaseDeadlineExceeded
from app.services import mcp_runtime


@pytest.fixture()
def runtime_clock(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        mcp_runtime, "time", SimpleNamespace(monotonic=lambda: clock.now)
    )
    values = {
        "database_statement_timeout_ms": 5000,
        "database_lock_timeout_ms": 2000,
        "redis_url": "redis://test.invalid:6379/0",
    }
    settings = SimpleNamespace(**values)
    settings.model_copy = lambda *, update: SimpleNamespace(**(values | update))
    monkeypatch.setattr(mcp_runtime, "get_settings", lambda: settings)
    return clock


@pytest.fixture()
def sqlite_db():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE budget_test (value INTEGER)"))
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture()
def tracked_listeners(monkeypatch):
    attached = []

    def listen(target, name, listener):
        event.listen(target, name, listener)
        attached.append((target, name, listener))

    monkeypatch.setattr(
        mcp_runtime, "event", SimpleNamespace(listen=listen, remove=event.remove)
    )
    return attached


def _assert_detached(listeners):
    assert listeners
    assert all(
        not event.contains(target, name, listener)
        for target, name, listener in listeners
    )


def test_database_budget_survives_auth_commit_then_rejects_expired_read(
    sqlite_db, runtime_clock, tracked_listeners
):
    with pytest.raises(DatabaseDeadlineExceeded):
        with mcp_runtime.mcp_database_budget(sqlite_db, deadline=110):
            assert sqlite_db.scalar(text("SELECT 1")) == 1
            sqlite_db.commit()
            runtime_clock.now = 109
            assert sqlite_db.scalar(text("SELECT 2")) == 2
            runtime_clock.now = 111
            sqlite_db.scalar(text("SELECT 3"))
    statement_hooks = [
        entry for entry in tracked_listeners if entry[1] == "before_cursor_execute"
    ]
    assert len(statement_hooks) == 2
    _assert_detached(tracked_listeners)
    sqlite_db.rollback()
    assert sqlite_db.scalar(text("SELECT 4")) == 4


def test_postgres_statement_limits_follow_remaining_budget(
    sqlite_db, runtime_clock, tracked_listeners
):
    settings = mcp_runtime.get_settings()
    with mcp_runtime.mcp_database_budget(sqlite_db, deadline=110):
        sqlite_db.scalar(text("SELECT 1"))
        hook = next(
            listener
            for _, name, listener in tracked_listeners
            if name == "before_cursor_execute"
        )
        executions = []
        cursor = SimpleNamespace(
            execute=lambda statement, parameters: executions.append(
                (statement, parameters)
            )
        )
        connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        hook(connection, cursor, "SELECT 1", (), None, False)
        assert executions[-1][1] == ("5000", "2000")
        runtime_clock.now = 109
        hook(connection, cursor, "SELECT 2", (), None, False)
        assert executions[-1][1] == ("1000", "1000")
        settings.database_statement_timeout_ms = 0
        runtime_clock.now = 105
        hook(connection, cursor, "SELECT 3", (), None, False)
        assert executions[-1][1] == ("5000", "2000")
        assert all(
            "set_config('statement_timeout'" in statement
            and "set_config('lock_timeout'" in statement
            for statement, _ in executions
        )
    _assert_detached(tracked_listeners)


def test_expired_commit_leaves_rollback_usable_and_drops_pending_work(
    sqlite_db, runtime_clock, tracked_listeners
):
    with pytest.raises(DatabaseDeadlineExceeded):
        with mcp_runtime.mcp_database_budget(sqlite_db, deadline=110):
            sqlite_db.execute(text("INSERT INTO budget_test(value) VALUES (1)"))
            runtime_clock.now = 111
            sqlite_db.commit()
    _assert_detached(tracked_listeners)
    sqlite_db.rollback()
    assert sqlite_db.scalar(text("SELECT count(*) FROM budget_test")) == 0


def test_expiry_before_yield_and_registration_failure_remove_hooks(
    sqlite_db, runtime_clock, tracked_listeners, monkeypatch
):
    with pytest.raises(DatabaseDeadlineExceeded):
        with mcp_runtime.mcp_database_budget(sqlite_db, deadline=99):
            pytest.fail("expired request must not enter the body")
    _assert_detached(tracked_listeners)
    tracked_listeners.clear()
    original_listen = mcp_runtime.event.listen

    def fail_second_registration(target, name, listener):
        if name == "before_commit":
            raise RuntimeError("registration failed")
        original_listen(target, name, listener)

    monkeypatch.setattr(mcp_runtime.event, "listen", fail_second_registration)
    with pytest.raises(RuntimeError, match="registration failed"):
        with mcp_runtime.mcp_database_budget(sqlite_db, deadline=110):
            pytest.fail("registration failed before the body")
    _assert_detached(tracked_listeners)


def test_success_does_not_commit_or_rollback_the_publication_transaction(
    sqlite_db, runtime_clock, tracked_listeners
):
    with mcp_runtime.mcp_database_budget(sqlite_db, deadline=110):
        sqlite_db.scalar(text("SELECT 1"))
        transaction = sqlite_db.get_transaction()
    assert transaction is sqlite_db.get_transaction()
    assert transaction.is_active
    _assert_detached(tracked_listeners)


class _RedisClient:
    def __init__(self, reply=(1, 60), *, error=None, after_eval=None, close_error=None):
        self.reply = reply
        self.error = error
        self.after_eval = after_eval
        self.close_error = close_error
        self.calls = []
        self.closed = False

    def eval(self, script, key_count, key):
        self.calls.append((script, key_count, key))
        if self.after_eval:
            self.after_eval()
        if self.error:
            raise self.error
        return self.reply

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


def _redis_factory(monkeypatch, client):
    created = []

    def factory(url, **kwargs):
        created.append((url, kwargs))
        return client

    monkeypatch.setattr(mcp_runtime, "redis_client_from_url", factory)
    return created


def test_rate_limit_hashes_bucket_and_uses_atomic_fixed_window_ttl(
    runtime_clock, monkeypatch
):
    client = _RedisClient()
    created = _redis_factory(monkeypatch, client)
    mcp_runtime.enforce_mcp_rate_limit(
        bucket="tlp_do-not-store-token", limit=10, deadline=100.5
    )
    assert client.closed
    script, key_count, key = client.calls[0]
    assert key_count == 1
    assert key.startswith("threatlens:mcp:rate:")
    assert "tlp_do-not-store-token" not in key
    assert "if count == 1 then redis.call('EXPIRE', KEYS[1], 60) end" in script
    assert "redis.call('TTL', KEYS[1])" in script
    options = created[0][1]
    assert options["settings"].redis_connect_timeout_seconds == 0.25
    assert options["settings"].redis_socket_timeout_seconds == 0.25


@pytest.mark.parametrize("reply,expected_retry", [((11, 47), "47"), ((11, 0), "1")])
def test_rate_limit_denial_has_bounded_retry_after(
    runtime_clock, monkeypatch, reply, expected_retry
):
    client = _RedisClient(reply)
    _redis_factory(monkeypatch, client)
    with pytest.raises(ApiHTTPException) as caught:
        mcp_runtime.enforce_mcp_rate_limit(bucket="principal", limit=10, deadline=110)
    assert caught.value.status_code == 429
    assert caught.value.error_code == "mcp_rate_limited"
    assert caught.value.headers == {"Retry-After": expected_retry}
    assert client.closed


@pytest.mark.parametrize(
    "reply", [None, [], [1], [1, 60, 9], ["bad", 60], [1, -1], [0, 60]]
)
def test_invalid_admission_reply_fails_closed(runtime_clock, monkeypatch, reply):
    client = _RedisClient(reply)
    _redis_factory(monkeypatch, client)
    with pytest.raises(ApiHTTPException) as caught:
        mcp_runtime.enforce_mcp_rate_limit(bucket="principal", limit=10, deadline=110)
    assert caught.value.status_code == 503
    assert caught.value.error_code == "mcp_admission_unavailable"
    assert client.closed


def test_redis_creation_and_io_errors_are_sanitized(runtime_clock, monkeypatch, caplog):
    secret = "redis://secret-user:secret-password@internal-host/0"

    def unavailable(*args, **kwargs):
        raise redis.ConnectionError(secret)

    monkeypatch.setattr(mcp_runtime, "redis_client_from_url", unavailable)
    with pytest.raises(ApiHTTPException) as caught:
        mcp_runtime.enforce_mcp_rate_limit(bucket="principal", limit=10, deadline=110)
    assert caught.value.status_code == 503
    assert secret not in str(caught.value.detail)
    client = _RedisClient(
        error=redis.TimeoutError(secret), close_error=redis.ConnectionError(secret)
    )
    _redis_factory(monkeypatch, client)
    with pytest.raises(ApiHTTPException) as caught:
        mcp_runtime.enforce_mcp_rate_limit(bucket="principal", limit=10, deadline=110)
    assert caught.value.status_code == 503
    assert client.closed
    assert secret not in caplog.text


def test_admission_that_finishes_after_deadline_is_rejected(runtime_clock, monkeypatch):
    client = _RedisClient(after_eval=lambda: setattr(runtime_clock, "now", 111))
    _redis_factory(monkeypatch, client)
    with pytest.raises(DatabaseDeadlineExceeded):
        mcp_runtime.enforce_mcp_rate_limit(bucket="principal", limit=10, deadline=110)
    assert client.closed


def test_expired_admission_does_not_create_redis_client(runtime_clock, monkeypatch):
    client = _RedisClient()
    created = _redis_factory(monkeypatch, client)
    with pytest.raises(DatabaseDeadlineExceeded):
        mcp_runtime.enforce_mcp_rate_limit(bucket="principal", limit=10, deadline=99)
    assert not created
