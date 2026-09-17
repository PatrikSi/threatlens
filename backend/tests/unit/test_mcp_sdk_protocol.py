"""Optional SDK wire checks; run with the pinned mcp==2.2.0 test dependency.

These exercise the actual SDK Streamable HTTP client against the pure protocol
adapter through a mock HTTP transport. Route/auth/data access integration is
covered separately; this test never opens a network connection or a database.
"""

import asyncio
import json

import pytest

from app.services.mcp_protocol import (
    LATEST_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSION,
    MCPProtocolError,
    PROTOCOL_VERSION_META,
    error_response,
    parse_json,
    parse_request,
    protocol_result,
    success_response,
)


@pytest.fixture
def sdk():
    module = pytest.importorskip(
        "mcp", reason="Optional SDK wire test needs mcp==2.2.0"
    )
    from importlib.metadata import version

    if version("mcp") != "2.2.0":
        pytest.skip("SDK wire test is pinned to mcp==2.2.0")
    return module


@pytest.mark.parametrize(
    ("mode", "version", "opening_method"),
    [
        ("auto", LATEST_PROTOCOL_VERSION, "server/discover"),
        ("legacy", LEGACY_PROTOCOL_VERSION, "initialize"),
    ],
)
def test_official_sdk_discovers_lists_and_calls_stateless_protocol(
    sdk, mode, version, opening_method
):
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    definitions = [
        {
            "name": "get_report",
            "description": "Read a saved report.",
            "inputSchema": {
                "type": "object",
                "properties": {"report_id": {"type": "string"}},
                "required": ["report_id"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {"data": {"type": "string"}},
                "required": ["data"],
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        }
    ]
    requests = []

    async def respond(http_request):
        if http_request.method != "POST":
            return httpx2.Response(405, headers={"Allow": "POST"})
        body = parse_json(await http_request.aread())
        requests.append(body)
        try:
            request = parse_request(body, http_request.headers)
            if request.is_notification:
                return httpx2.Response(202)
            response = protocol_result(request, definitions)
            if response is None:
                assert request.method == "tools/call"
                assert request.params["arguments"] == {"report_id": "saved-report"}
                data = {"data": "Saved report evidence"}
                response = success_response(
                    request,
                    {
                        "content": [{"type": "text", "text": json.dumps(data)}],
                        "structuredContent": data,
                        "isError": False,
                    },
                )
            return httpx2.Response(200, json=response)
        except MCPProtocolError as error:
            return httpx2.Response(error.status_code, json=error_response(error))

    async def exercise():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
            transport = streamable_http_client(
                "http://test.example/v1/mcp", http_client=http
            )
            async with sdk.Client(
                transport, mode=mode, read_timeout_seconds=5
            ) as client:
                assert client.protocol_version == version
                listing = await client.list_tools()
                assert [tool.name for tool in listing.tools] == ["get_report"]
                result = await client.call_tool(
                    "get_report", {"report_id": "saved-report"}
                )
                assert result.structured_content == {"data": "Saved report evidence"}
                assert result.is_error is False

    asyncio.run(exercise())
    assert requests[0]["method"] == opening_method
    if mode == "auto":
        assert all(
            body["params"]["_meta"][PROTOCOL_VERSION_META] == LATEST_PROTOCOL_VERSION
            for body in requests
        )
        assert "initialize" not in {body["method"] for body in requests}
    else:
        assert "notifications/initialized" in {body["method"] for body in requests}
