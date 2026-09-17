"""Keep ordinary MCP reads bounded without weakening publication fences."""

from types import SimpleNamespace

from sqlalchemy import event

from app.services import authorization, mcp_runtime
from app.services.mcp_protocol import CLIENT_CAPABILITIES_META, LATEST_PROTOCOL_VERSION, PROTOCOL_VERSION_META


def test_article_http_read_has_a_bounded_per_request_authorization_cost(
    mcp_http_environment, monkeypatch, record_testsuite_property,
):
    env = mcp_http_environment
    counts = {"queries": 0, "budget_queries": 0, "authorization_snapshots": 0}
    original_snapshot = authorization._authorization_snapshot_for_user

    def snapshot(*args, **kwargs):
        counts["authorization_snapshots"] += 1
        return original_snapshot(*args, **kwargs)

    def record_query(*args):
        counts["queries"] += 1

    class CountingCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def execute(self, *args, **kwargs):
            counts["budget_queries"] += 1
            return self.cursor.execute(*args, **kwargs)

    wrapped_listeners = {}

    def listen(target, name, callback):
        if name == "before_cursor_execute":
            def wrapped(connection, cursor, *args):
                return callback(connection, CountingCursor(cursor), *args)

            wrapped_listeners[(target, name, callback)] = wrapped
            event.listen(target, name, wrapped)
        else:
            event.listen(target, name, callback)

    def remove(target, name, callback):
        event.remove(target, name, wrapped_listeners.pop((target, name, callback), callback))

    monkeypatch.setattr(authorization, "_authorization_snapshot_for_user", snapshot)
    monkeypatch.setattr(mcp_runtime, "event", SimpleNamespace(listen=listen, remove=remove))
    event.listen(env.engine, "before_cursor_execute", record_query)
    try:
        for attempt in range(2):
            counts.update(dict.fromkeys(counts, 0))
            response = env.client.post("/v1/mcp", json={
                "jsonrpc": "2.0", "id": attempt, "method": "tools/call", "params": {
                    "name": "get_article_evidence", "arguments": {"item_id": str(env.item_id)},
                    "_meta": {PROTOCOL_VERSION_META: LATEST_PROTOCOL_VERSION, CLIENT_CAPABILITIES_META: {}},
                },
            }, headers={
                "Authorization": f"Bearer {env.token}", "X-Request-ID": f"{env.request_prefix}-budget-{attempt}",
                "MCP-Protocol-Version": LATEST_PROTOCOL_VERSION,
                "MCP-Method": "tools/call", "MCP-Name": "get_article_evidence",
            })
            assert response.status_code == 200, response.text
            assert response.json()["result"]["isError"] is False
            assert counts["authorization_snapshots"] == 2, counts
            # These counters exclude raw pool pre-pings. The original path used
            # 158 SQL/timeout calls here (160 including two pre-pings) and five
            # full snapshots. Allow headroom without permitting another rebuild.
            assert counts["queries"] <= 42, counts
            assert counts["queries"] + counts["budget_queries"] <= 84, counts
        for key, value in counts.items():
            record_testsuite_property(f"mcp_article_{key}", value)
    finally:
        event.remove(env.engine, "before_cursor_execute", record_query)
