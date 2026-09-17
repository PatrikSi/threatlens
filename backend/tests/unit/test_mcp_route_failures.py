from contextlib import ExitStack, nullcontext
import json
import time
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from app.api.routes import mcp
from app.services.mcp_protocol import MCPRequest, LATEST_PROTOCOL_VERSION


def _rpc():
    return MCPRequest(7, "tools/list", {}, LATEST_PROTOCOL_VERSION)


@pytest.mark.parametrize("sqlstate,status,code", [
    ("57014", 504, "mcp_deadline"),
    ("55P03", 503, "mcp_database_busy"),
    ("40P01", 503, "mcp_database_busy"),
    ("40001", 503, "mcp_database_busy"),
    ("08006", 503, "mcp_unavailable"),
])
@pytest.mark.parametrize("driver_attribute", ["sqlstate", "pgcode"])
def test_database_failures_keep_safe_mcp_error_contract(sqlstate, status, code, driver_attribute, caplog):
    original = RuntimeError("PRIVATE_DATABASE_FAILURE")
    setattr(original, driver_attribute, sqlstate)
    error = OperationalError("PRIVATE_SQL", {}, original)
    response = mcp._error_response(_rpc(), error)
    body = json.loads(response.body)
    assert response.status_code == status
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 7
    assert body["error"]["data"]["code"] == code
    if status == 503:
        assert response.headers["retry-after"] == "5"
    assert "PRIVATE_" not in response.body.decode()
    assert "PRIVATE_" not in caplog.text


@pytest.mark.parametrize("invalidate_fails", [False, True])
def test_failed_rollback_cannot_replace_the_original_mcp_response(monkeypatch, caplog, invalidate_fails):
    actions = []

    class FailedSession:
        def rollback(self):
            actions.append("rollback")
            raise OperationalError("PRIVATE_SQL", {}, RuntimeError("PRIVATE_ROLLBACK"))

        def invalidate(self):
            actions.append("invalidate")
            if invalidate_fails:
                raise RuntimeError("PRIVATE_INVALIDATION_DETAILS")

    monkeypatch.setattr(mcp, "enforce_mcp_rate_limit", lambda **kwargs: None)
    monkeypatch.setattr(mcp.db_session, "engine", SimpleNamespace(connect=lambda: nullcontext(object())))
    monkeypatch.setattr(mcp, "Session", lambda **kwargs: nullcontext(FailedSession()))
    monkeypatch.setattr(mcp, "mcp_database_budget", lambda *args, **kwargs: nullcontext())

    def fail_resolution(*args, **kwargs):
        raise TimeoutError("PRIVATE_ORIGINAL_TIMEOUT")

    monkeypatch.setattr(mcp, "resolve_mcp_read_context", fail_resolution)
    monkeypatch.setattr(mcp, "_audit_failure", lambda *args: actions.append("audit_failure"))
    request = Request({
        "type": "http", "method": "POST", "path": "/v1/mcp", "query_string": b"",
        "headers": [(b"authorization", b"Bearer tlp_synthetic")],
        "state": {"mcp_deadline": time.monotonic() + 10},
    })
    with ExitStack() as resources:
        response = mcp._prepare_response(request, _rpc(), resources)
    assert response.status_code == 504
    assert json.loads(response.body)["error"]["data"]["code"] == "mcp_deadline"
    assert actions == ["rollback", "invalidate", "audit_failure"]
    assert "mcp_rollback_failed" in caplog.text
    assert "PRIVATE_" not in caplog.text
