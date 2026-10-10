import pytest
from pydantic import ValidationError

from app.core.config import Settings


def settings(**values):
    return Settings(_env_file=None, app_env="development", database_url="sqlite://",
                    redis_url="redis://localhost:6379/0", **values)


def test_mcp_is_disabled_without_browser_origins_by_default():
    configured = settings()
    assert configured.mcp_enabled is False
    assert configured.mcp_allowed_origins == []


@pytest.mark.parametrize("origin", ["*", "null", "https://*.example.com", "https://a.example/path",
                                    "https://user:pass@a.example", "https://a.example?token=x",
                                    "https://a.example:bad", "https://a .example"])
def test_mcp_origins_require_unambiguous_explicit_origins(origin):
    with pytest.raises(ValidationError, match="mcp_allowed_origins"):
        settings(mcp_allowed_origins=[origin])


def test_mcp_origins_csv_is_shared_by_compose_and_direct_deployment():
    configured = settings(mcp_allowed_origins="https://assistant.example, http://localhost:3000,https://assistant.example")
    assert configured.mcp_allowed_origins == ["https://assistant.example", "http://localhost:3000"]


@pytest.mark.parametrize("field,value", [
    ("mcp_request_max_bytes", 65537), ("mcp_response_max_bytes", 16383),
    ("mcp_response_max_bytes", 65537), ("mcp_request_timeout_seconds", float("inf")),
    ("mcp_request_timeout_seconds", float("nan")), ("mcp_rate_limit_per_minute", 0),
    ("mcp_max_concurrent_requests", 0),
])
def test_mcp_limits_are_bounded(field, value):
    with pytest.raises(ValidationError):
        settings(**{field: value})


def test_enabled_mcp_requires_capacity_for_fenced_read_and_audit():
    with pytest.raises(ValidationError, match="at least two database connections"):
        settings(mcp_enabled=True, database_pool_size=1, database_max_overflow=0)
    assert settings(mcp_enabled=True, database_pool_size=1, database_max_overflow=1).mcp_enabled
    assert not settings(database_pool_size=1, database_max_overflow=0).mcp_enabled
