import base64
import json

import pytest

from app.services.mcp_protocol import (
    CLIENT_CAPABILITIES_META,
    CLIENT_INFO_META,
    LATEST_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSION,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_JSON_STRING_LENGTH,
    MCPProtocolError,
    PROTOCOL_VERSION_META,
    SERVER_INFO_META,
    SUPPORTED_PROTOCOL_VERSIONS,
    error_response,
    parse_json,
    parse_request,
    protocol_result,
    success_response,
)


def modern_message(method="tools/list", params=None, request_id=7):
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {
            "_meta": {
                PROTOCOL_VERSION_META: LATEST_PROTOCOL_VERSION,
                CLIENT_CAPABILITIES_META: {},
            },
            **(params or {}),
        },
    }
    headers = {
        "MCP-Protocol-Version": LATEST_PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if "name" in body["params"]:
        headers["Mcp-Name"] = body["params"]["name"]
    return body, headers


def assert_error(body, headers, *, code, status=400):
    with pytest.raises(MCPProtocolError) as caught:
        parse_request(body, headers)
    assert caught.value.code == code
    assert caught.value.status_code == status
    return caught.value


def test_modern_discovery_is_stateless_and_has_identity_in_metadata():
    request = parse_request(*modern_message("server/discover"))
    response = protocol_result(request, [], server_version="test-version")
    result = response["result"]
    assert response["id"] == 7
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == list(SUPPORTED_PROTOCOL_VERSIONS)
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["cacheScope"] == "private"
    assert result["ttlMs"] == 0
    assert result["_meta"][SERVER_INFO_META] == {
        "name": "threatlens",
        "version": "test-version",
    }
    assert "serverInfo" not in result


def test_tool_listing_only_contains_supplied_definitions_and_is_deterministic():
    request = parse_request(*modern_message())
    definitions = [{"name": "z_last"}, {"name": "a_first"}]
    response = protocol_result(request, definitions)
    assert response["result"]["tools"] == list(reversed(definitions))
    assert "nextCursor" not in response["result"]
    assert response["result"]["cacheScope"] == "private"
    assert response["result"]["ttlMs"] == 0
    assert definitions[0]["name"] == "z_last"


def test_tool_call_is_delegated_and_success_wrap_preserves_domain_error():
    request = parse_request(*modern_message("tools/call", {"name": "get_report"}))
    assert protocol_result(request, []) is None
    result = {"content": [{"type": "text", "text": "Unavailable"}], "isError": True}
    response = success_response(request, result)
    assert response["result"]["isError"] is True
    assert response["result"]["resultType"] == "complete"
    assert "resultType" not in result


@pytest.mark.parametrize("request_id", [0, -1, "", "request-1", 2**53 - 1])
def test_supported_request_ids_round_trip(request_id):
    request = parse_request(*modern_message(request_id=request_id))
    assert success_response(request, {})["id"] == request_id


@pytest.mark.parametrize(
    "request_id", [None, True, False, 1.0, [], {}, "x" * 129, 2**53]
)
def test_invalid_request_ids_are_not_reflected(request_id):
    error = assert_error(*modern_message(request_id=request_id), code=-32600)
    assert "id" not in error_response(error)


@pytest.mark.parametrize("body", [[], [{}], None, 1, True, "tools/list"])
def test_batches_and_nonobjects_are_rejected(body):
    assert_error(body, {}, code=-32600)


@pytest.mark.parametrize(
    "change",
    [
        {"jsonrpc": "1.0"},
        {"method": 42},
        {"method": ""},
        {"method": "x" * 129},
        {"result": {}},
        {"unexpected": "ignored-by-loose-parsers"},
    ],
)
def test_invalid_envelope_is_rejected(change):
    body, headers = modern_message()
    body.update(change)
    assert_error(body, headers, code=-32600)


@pytest.mark.parametrize("params", [None, [], "params", True])
def test_params_must_be_an_object(params):
    body, headers = modern_message()
    body["params"] = params
    assert_error(body, headers, code=-32602)


@pytest.mark.parametrize("field", [PROTOCOL_VERSION_META, CLIENT_CAPABILITIES_META])
def test_modern_required_metadata_is_validated(field):
    body, headers = modern_message()
    del body["params"]["_meta"][field]
    assert_error(body, headers, code=-32602)


@pytest.mark.parametrize("value", [None, [], "capabilities", 0])
def test_modern_capabilities_must_be_an_object(value):
    body, headers = modern_message()
    body["params"]["_meta"][CLIENT_CAPABILITIES_META] = value
    assert_error(body, headers, code=-32602)


@pytest.mark.parametrize("meta", [None, [], {"bad/key/extra": {}}, {"-invalid": {}}])
def test_malformed_metadata_is_rejected(meta):
    body, headers = modern_message()
    body["params"]["_meta"] = meta
    assert_error(body, headers, code=-32602)


@pytest.mark.parametrize(
    "info", [None, {}, {"name": "client"}, {"name": 1, "version": "1"}]
)
def test_optional_client_identity_is_validated_when_present(info):
    body, headers = modern_message()
    body["params"]["_meta"][CLIENT_INFO_META] = info
    assert_error(body, headers, code=-32602)


def test_unknown_vendor_metadata_and_capabilities_are_tolerated():
    body, headers = modern_message()
    body["params"]["_meta"]["com.example/trace"] = {"value": 1}
    body["params"]["_meta"][CLIENT_CAPABILITIES_META] = {"future": {}}
    assert parse_request(body, headers).protocol_version == LATEST_PROTOCOL_VERSION


@pytest.mark.parametrize("header", ["MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"])
def test_missing_required_headers_are_rejected(header):
    body, headers = modern_message("tools/call", {"name": "get_report"})
    del headers[header]
    assert_error(body, headers, code=-32020)


@pytest.mark.parametrize("header", ["MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"])
@pytest.mark.parametrize(
    "value", ["different", " malformed ", "bad\nvalue", "bad\x00value", "é"]
)
def test_mismatched_and_malformed_headers_are_rejected(header, value):
    body, headers = modern_message("tools/call", {"name": "get_report"})
    headers[header] = value
    assert_error(body, headers, code=-32020)


def test_header_names_are_case_insensitive_and_duplicate_names_fail():
    body, headers = modern_message()
    assert parse_request(body, {key.lower(): value for key, value in headers.items()})
    headers["mcp-method"] = "tools/list"
    assert_error(body, headers, code=-32020)


def test_base64_name_header_is_decoded_before_comparison():
    body, headers = modern_message("tools/call", {"name": "get_report"})
    encoded = base64.b64encode(b"get_report").decode()
    headers["Mcp-Name"] = f"=?base64?{encoded}?="
    assert parse_request(body, headers).params["name"] == "get_report"
    headers["Mcp-Name"] = "=?base64?not base64?="
    assert_error(body, headers, code=-32020)


@pytest.mark.parametrize("version", ["1900-01-01", "2024-11-05", "2025-06-18"])
def test_unsupported_version_error_lists_supported_versions(version):
    body, headers = modern_message()
    body["params"]["_meta"][PROTOCOL_VERSION_META] = version
    headers["MCP-Protocol-Version"] = version
    error = assert_error(body, headers, code=-32022)
    assert error_response(error) == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {
            "code": -32022,
            "message": "Unsupported protocol version",
            "data": {
                "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                "requested": version,
            },
        },
    }


@pytest.mark.parametrize(
    "method", ["ping", "initialize", "prompts/list", "resources/list", "arbitrary/run"]
)
def test_modern_unknown_or_removed_methods_fail_explicitly(method):
    assert_error(*modern_message(method), code=-32601, status=404)


@pytest.mark.parametrize("method", ["tools/list", "tools/call", "server/discover"])
def test_modern_methods_cannot_be_notifications(method):
    body, headers = modern_message(
        method, {"name": "get_report"} if method == "tools/call" else {}
    )
    del body["id"]
    assert_error(body, headers, code=-32600)


def test_modern_initialized_notification_is_not_accepted():
    body, headers = modern_message("notifications/initialized")
    del body["id"]
    error = assert_error(body, headers, code=-32601, status=404)
    assert "id" not in error_response(error)


@pytest.mark.parametrize(
    "extra", [{"cursor": "unissued"}, {"cursor": None}, {"arbitrary": True}]
)
def test_fixed_tool_listing_rejects_cursors_and_unknown_params(extra):
    assert_error(*modern_message(params=extra), code=-32602)


@pytest.mark.parametrize("arguments", [None, [], "arguments", False])
def test_tool_arguments_must_be_an_object(arguments):
    assert_error(
        *modern_message("tools/call", {"name": "get_report", "arguments": arguments}),
        code=-32602,
    )


@pytest.mark.parametrize("name", ["", "invalid name", "a" * 129, "get_report\n"])
def test_tool_names_have_a_bounded_ascii_shape(name):
    body, headers = modern_message("tools/call", {"name": name})
    headers["Mcp-Name"] = "=?base64?" + base64.b64encode(name.encode()).decode() + "?="
    assert_error(body, headers, code=-32602)


def legacy_initialize():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LEGACY_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "legacy-client", "version": "1.0.0"},
        },
    }


def test_legacy_initialize_and_notification_need_no_server_session():
    request = parse_request(legacy_initialize(), {})
    result = protocol_result(request, [])["result"]
    assert result["protocolVersion"] == LEGACY_PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "threatlens"
    assert "resultType" not in result
    assert "_meta" not in result
    notification = parse_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"mcp-protocol-version": LEGACY_PROTOCOL_VERSION},
    )
    assert notification.is_notification
    assert protocol_result(notification, []) is None
    with pytest.raises(ValueError, match="Notifications"):
        success_response(notification, {})


@pytest.mark.parametrize("method", ["ping", "tools/list", "tools/call"])
def test_legacy_requests_are_stateless_and_do_not_require_modern_headers(method):
    body = {"jsonrpc": "2.0", "id": "legacy", "method": method}
    if method == "tools/call":
        body["params"] = {"name": "get_report", "arguments": {}}
    request = parse_request(body, {"mcp-protocol-version": LEGACY_PROTOCOL_VERSION})
    assert request.protocol_version == LEGACY_PROTOCOL_VERSION
    assert "resultType" not in success_response(request, {})["result"]


def test_legacy_calls_require_a_version_header_and_reject_mirrored_mismatches():
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert_error(body, {}, code=-32020)
    assert_error(
        body,
        {"mcp-protocol-version": LEGACY_PROTOCOL_VERSION, "mcp-method": "tools/call"},
        code=-32020,
    )
    body["params"] = {"_meta": {PROTOCOL_VERSION_META: LATEST_PROTOCOL_VERSION}}
    assert_error(body, {"mcp-protocol-version": LEGACY_PROTOCOL_VERSION}, code=-32020)


@pytest.mark.parametrize("params", [{}, {"clientInfo": {}}, {"capabilities": None}])
def test_legacy_initialize_params_are_validated(params):
    body = legacy_initialize()
    body["params"].update(params)
    if not params:
        del body["params"]["protocolVersion"]
    assert_error(body, {}, code=-32602)


def test_legacy_initialize_rejects_version_mismatch():
    assert_error(
        legacy_initialize(),
        {"mcp-protocol-version": LATEST_PROTOCOL_VERSION},
        code=-32020,
    )


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{",
        b"\xff",
        b'{"same":1,"same":2}',
        b'{"a":{"same":1,"same":2}}',
        b"NaN",
        b"Infinity",
    ],
)
def test_json_decoder_rejects_ambiguous_and_malformed_json(body):
    with pytest.raises(MCPProtocolError) as caught:
        parse_json(body)
    assert caught.value.code == -32700
    assert "id" not in error_response(caught.value)


def test_json_decoder_rejects_escaped_unpaired_surrogates():
    with pytest.raises(MCPProtocolError) as caught:
        parse_json(b'{"unsafe":"\\ud800"}')
    assert caught.value.code == -32600


@pytest.mark.parametrize(
    "body",
    [b'{"unsafe":"\\u0000"}', b'{"\\ud800":1}', b'{"\\u0000":1}', b'{"unsafe":1e999}'],
)
def test_json_decoder_rejects_unsafe_keys_strings_and_nonfinite_numbers(body):
    with pytest.raises(MCPProtocolError) as caught:
        parse_json(body)
    assert caught.value.code == -32600


@pytest.mark.parametrize(
    "value",
    [
        "x" * (MAX_JSON_STRING_LENGTH + 1),
        [0] * MAX_JSON_NODES,
        {"x" * 257: 0},
        float("inf"),
    ],
)
def test_json_limits_apply_to_bytes_and_predecoded_objects(value):
    with pytest.raises(MCPProtocolError):
        parse_json(json.dumps(value).encode())
    with pytest.raises(MCPProtocolError):
        parse_request(value, {})


def test_json_depth_limit_is_bounded_without_recursive_validation():
    value = {}
    for _ in range(MAX_JSON_DEPTH + 1):
        value = {"child": value}
    with pytest.raises(MCPProtocolError, match="complexity"):
        parse_json(json.dumps(value).encode())


def test_valid_utf8_json_round_trips():
    body, headers = modern_message(
        "tools/call", {"name": "search_articles", "arguments": {"q": "café"}}
    )
    parsed = parse_json(json.dumps(body, ensure_ascii=False).encode())
    assert parse_request(parsed, headers).params["arguments"] == {"q": "café"}
