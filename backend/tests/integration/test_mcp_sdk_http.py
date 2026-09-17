"""Optional official-SDK checks through the real endpoint and disposable data."""

import asyncio
from importlib.metadata import version

import pytest

mcp = pytest.importorskip("mcp", reason="Optional SDK integration needs mcp==2.2.0")
if version("mcp") != "2.2.0":
    pytest.skip("SDK integration is pinned to mcp==2.2.0", allow_module_level=True)

import httpx2  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402


@pytest.mark.parametrize(
    ("mode", "protocol_version"),
    [("auto", "2026-07-28"), ("legacy", "2025-11-25")],
)
def test_official_sdk_reads_article_through_real_mcp_route(
    mcp_http_environment, mode, protocol_version
):
    environment = mcp_http_environment
    responses = []

    async def observe(response):
        responses.append((response.status_code, response.headers))

    async def exercise():
        transport = httpx2.ASGITransport(
            app=environment.app, client=(environment.source_ip, 50000)
        )
        async with httpx2.AsyncClient(
            transport=transport,
            headers={"Authorization": "Bearer " + environment.token},
            event_hooks={"response": [observe]},
        ) as http:
            stream = streamable_http_client(
                "http://testserver/v1/mcp", http_client=http
            )
            async with mcp.Client(stream, mode=mode, read_timeout_seconds=10) as client:
                assert client.protocol_version == protocol_version
                tools = await client.list_tools()
                assert "get_article_evidence" in {tool.name for tool in tools.tools}
                result = await client.call_tool(
                    "get_article_evidence", {"item_id": str(environment.item_id)}
                )
                assert result.is_error is False
                payload = result.structured_content
                assert payload["data"]["item_id"] == str(environment.item_id)
                assert payload["data"]["content_available"] is True
                assert payload["data"]["article_text"]
                assert payload["provenance"]
                assert payload["freshness"]["retrieved_at"]
                assert str(environment.item_id) in payload["canonical_link"]

    asyncio.run(exercise())
    successful = [headers for status, headers in responses if status == 200]
    assert len(successful) >= 3
    assert all("mcp-session-id" not in headers for _, headers in responses)
    assert all("no-store" in headers.get("cache-control", "") for headers in successful)
    assert all(
        headers.get("mcp-protocol-version") == protocol_version
        for headers in successful
    )
