from types import SimpleNamespace

from app.core import redis_client as redis_client_module
from app.db import session as session_module


def test_redis_client_uses_bounded_connect_and_socket_timeouts(monkeypatch):
    captured: dict = {}

    def fake_from_url(redis_url: str, **kwargs):
        captured["redis_url"] = redis_url
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(redis_client_module.redis.Redis, "from_url", fake_from_url)
    settings = SimpleNamespace(redis_connect_timeout_seconds=1.5, redis_socket_timeout_seconds=2.5)

    redis_client_module.redis_client_from_url(
        "redis://redis:6379/0",
        decode_responses=True,
        settings=settings,
    )

    assert captured == {
        "redis_url": "redis://redis:6379/0",
        "decode_responses": True,
        "socket_connect_timeout": 1.5,
        "socket_timeout": 2.5,
        "health_check_interval": 30,
    }


def test_postgres_engine_options_include_connection_statement_and_pool_deadlines(monkeypatch):
    monkeypatch.setattr(session_module.settings, "database_connect_timeout_seconds", 7)
    monkeypatch.setattr(session_module.settings, "database_statement_timeout_ms", 45_000)
    monkeypatch.setattr(session_module.settings, "database_pool_timeout_seconds", 12)
    monkeypatch.setattr(session_module.settings, "database_pool_size", 3)
    monkeypatch.setattr(session_module.settings, "database_max_overflow", 1)
    monkeypatch.setattr(session_module.settings, "database_lock_timeout_ms", 2500)

    options = session_module._engine_options("postgresql+psycopg://user:pass@db/threatlens")

    assert options["pool_timeout"] == 12
    assert options["pool_size"] == 3
    assert options["max_overflow"] == 1
    assert options["connect_args"] == {
        "connect_timeout": 7,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        "tcp_user_timeout": 60_000,
        "options": "-c statement_timeout=45000 -c lock_timeout=2500",
    }


def test_non_postgres_engine_options_do_not_receive_driver_specific_arguments():
    options = session_module._engine_options("sqlite+pysqlite:///:memory:")

    assert options == {"pool_pre_ping": True, "hide_parameters": True}
