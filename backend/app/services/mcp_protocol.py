"""Bounded, stateless MCP wire handling, independent of HTTP and authorization.

The supported subset is the 2026-07-28 request protocol plus the 2025-11-25
initialize/tools compatibility flow. Transport limits, authentication, scoped
tool discovery, argument validation, and tool execution belong to the caller.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


LATEST_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = (LATEST_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION)
PROTOCOL_VERSION_META = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_META = "io.modelcontextprotocol/serverInfo"
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 2048
MAX_JSON_STRING_LENGTH = 16384
MAX_REQUEST_ID_LENGTH = 128
MAX_SAFE_INTEGER = (1 << 53) - 1
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_META_KEY = re.compile(
    r"(?:(?:[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?)"
    r"(?:\.[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*/)?"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?)?\Z"
)


class MCPProtocolError(ValueError):
    """An intentionally public protocol error, safe to serialize to the peer."""

    def __init__(
        self,
        code: int,
        message: str,
        *,
        status_code: int = 400,
        request_id: str | int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.data = data


@dataclass(frozen=True)
class MCPRequest:
    request_id: str | int | None
    method: str
    params: dict[str, Any]
    protocol_version: str

    @property
    def is_notification(self) -> bool:
        return self.request_id is None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-JSON numeric constant")


def parse_json(body: bytes) -> object:
    """Decode UTF-8 JSON without duplicate keys, nonfinite numbers, or deep trees.

    The transport MUST bound body bytes before calling this function. Structural
    depth/node limits here are also applied by parse_request to decoded objects.
    """
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise MCPProtocolError(-32700, "Parse error") from exc
    _validate_json_limits(payload)
    return payload


def _validate_json_limits(payload: object) -> None:
    pending = [(payload, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise MCPProtocolError(-32600, "Request exceeds JSON complexity limits")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
            if any(
                not isinstance(key, str) or len(key) > 256 or _invalid_string(key)
                for key in value
            ):
                raise MCPProtocolError(-32600, "Invalid JSON object key")
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            if len(value) > MAX_JSON_STRING_LENGTH or _invalid_string(value):
                raise MCPProtocolError(-32600, "Invalid JSON string")
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise MCPProtocolError(-32600, "Invalid JSON number")
        elif value is not None and type(value) not in (int, bool):
            raise MCPProtocolError(-32600, "Invalid JSON value")


def _invalid_string(value: str) -> bool:
    return "\x00" in value or any(
        0xD800 <= ord(character) <= 0xDFFF for character in value
    )


def _valid_id(value: object) -> bool:
    return (isinstance(value, str) and len(value) <= MAX_REQUEST_ID_LENGTH) or (
        type(value) is int and abs(value) <= MAX_SAFE_INTEGER
    )


def _invalid_params(request_id: str | int | None) -> MCPProtocolError:
    return MCPProtocolError(-32602, "Invalid params", request_id=request_id)


def _header_error(request_id: str | int | None) -> MCPProtocolError:
    return MCPProtocolError(
        -32020, "Missing, malformed, or mismatched MCP headers", request_id=request_id
    )


def _normalize_headers(
    headers: Mapping[str, str], request_id: str | int | None
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered not in {"mcp-protocol-version", "mcp-method", "mcp-name"}:
            continue
        if lowered in normalized or not isinstance(value, str):
            raise _header_error(request_id)
        if (
            not value
            or value != value.strip()
            or any(ord(char) < 32 or ord(char) > 126 for char in value)
        ):
            raise _header_error(request_id)
        normalized[lowered] = value
    return normalized


def _decode_name(value: str, request_id: str | int | None) -> str:
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise _header_error(request_id) from exc
    return value


def _validate_client_info(value: object, request_id: str | int | None) -> None:
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 256
        for key in ("name", "version")
    ):
        raise _invalid_params(request_id)


def _validate_meta(meta: object, request_id: str | int | None) -> dict[str, Any]:
    if not isinstance(meta, dict) or any(not _META_KEY.fullmatch(key) for key in meta):
        raise _invalid_params(request_id)
    if CLIENT_INFO_META in meta:
        _validate_client_info(meta[CLIENT_INFO_META], request_id)
    if CLIENT_CAPABILITIES_META in meta and not isinstance(
        meta[CLIENT_CAPABILITIES_META], dict
    ):
        raise _invalid_params(request_id)
    if "progressToken" in meta and not _valid_id(meta["progressToken"]):
        raise _invalid_params(request_id)
    return meta


def _select_version(
    method: str,
    params: dict[str, Any],
    headers: dict[str, str],
    request_id: str | int | None,
) -> str:
    meta = _validate_meta(params.get("_meta", {}), request_id)
    header_version = headers.get("mcp-protocol-version")
    body_version = meta.get(PROTOCOL_VERSION_META)
    legacy_initialize = method == "initialize" and PROTOCOL_VERSION_META not in meta
    if legacy_initialize:
        version = params.get("protocolVersion")
        if not isinstance(version, str):
            raise _invalid_params(request_id)
        if header_version is not None and header_version != version:
            raise _header_error(request_id)
    else:
        version = body_version if PROTOCOL_VERSION_META in meta else header_version
        if PROTOCOL_VERSION_META in meta and not isinstance(body_version, str):
            raise _invalid_params(request_id)
        if header_version is None or (
            PROTOCOL_VERSION_META in meta and header_version != body_version
        ):
            raise _header_error(request_id)
    if version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise MCPProtocolError(
            -32022,
            "Unsupported protocol version",
            request_id=request_id,
            data={"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "requested": version},
        )
    if version == LATEST_PROTOCOL_VERSION:
        if PROTOCOL_VERSION_META not in meta or CLIENT_CAPABILITIES_META not in meta:
            raise _invalid_params(request_id)
        if headers.get("mcp-method") != method:
            raise _header_error(request_id)
        if method in {"tools/call", "prompts/get", "resources/read"}:
            source = "uri" if method == "resources/read" else "name"
            if "mcp-name" not in headers or _decode_name(
                headers["mcp-name"], request_id
            ) != params.get(source):
                raise _header_error(request_id)
    # Even legacy callers cannot supply contradictory routing headers.
    if "mcp-method" in headers and headers["mcp-method"] != method:
        raise _header_error(request_id)
    if "mcp-name" in headers and _decode_name(
        headers["mcp-name"], request_id
    ) != params.get("name", params.get("uri")):
        raise _header_error(request_id)
    return version


def _validate_params(request: MCPRequest) -> None:
    method, params, request_id = request.method, request.params, request.request_id
    allowed = {"_meta"}
    if method == "initialize":
        allowed |= {"protocolVersion", "capabilities", "clientInfo"}
        if not isinstance(params.get("capabilities"), dict):
            raise _invalid_params(request_id)
        _validate_client_info(params.get("clientInfo"), request_id)
    elif method == "tools/call":
        allowed |= {"name", "arguments"}
        if not isinstance(params.get("name"), str) or not _NAME.fullmatch(
            params["name"]
        ):
            raise _invalid_params(request_id)
        if "arguments" in params and not isinstance(params["arguments"], dict):
            raise _invalid_params(request_id)
    # The fixed, small tool catalogue has one page and issues no cursors.
    if params.keys() - allowed:
        raise _invalid_params(request_id)


def parse_request(payload: object, headers: Mapping[str, str]) -> MCPRequest:
    """Validate one JSON-RPC request and its mirrored MCP HTTP headers.

    Batches and client responses are rejected. The only accepted notification
    is legacy notifications/initialized. It receives HTTP 202 with no body.
    """
    _validate_json_limits(payload)
    if not isinstance(payload, dict):
        raise MCPProtocolError(-32600, "Expected one JSON-RPC request object")
    request_id = payload.get("id")
    if "id" in payload and not _valid_id(request_id):
        raise MCPProtocolError(-32600, "Invalid request ID")
    if (
        payload.get("jsonrpc") != "2.0"
        or not isinstance(payload.get("method"), str)
        or not 1 <= len(payload["method"]) <= 128
        or payload.keys() - {"jsonrpc", "id", "method", "params"}
    ):
        raise MCPProtocolError(-32600, "Invalid request", request_id=request_id)
    params = payload.get("params", {})
    if not isinstance(params, dict):
        raise _invalid_params(request_id)
    method = payload["method"]
    normalized_headers = _normalize_headers(headers, request_id)
    version = _select_version(method, params, normalized_headers, request_id)
    allowed_methods = {"tools/list", "tools/call"}
    if version == LATEST_PROTOCOL_VERSION:
        allowed_methods.add("server/discover")
    else:
        allowed_methods |= {"initialize", "notifications/initialized", "ping"}
    if method not in allowed_methods:
        raise MCPProtocolError(
            -32601, "Method not found", status_code=404, request_id=request_id
        )
    if (method == "notifications/initialized") != ("id" not in payload):
        raise MCPProtocolError(-32600, "Invalid request ID", request_id=request_id)
    request = MCPRequest(request_id, method, params, version)
    _validate_params(request)
    return request


def error_response(error: MCPProtocolError) -> dict[str, Any]:
    detail: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.data is not None:
        detail["data"] = error.data
    response: dict[str, Any] = {"jsonrpc": "2.0", "error": detail}
    if error.request_id is not None:
        response["id"] = error.request_id
    return response


def success_response(
    request: MCPRequest, result: dict[str, Any], *, server_version: str = "0.1.0"
) -> dict[str, Any]:
    if request.is_notification:
        raise ValueError("Notifications must not receive a JSON-RPC response")
    result = dict(result)
    if request.protocol_version == LATEST_PROTOCOL_VERSION:
        result["resultType"] = "complete"
        result["_meta"] = {
            **result.get("_meta", {}),
            SERVER_INFO_META: {"name": "threatlens", "version": server_version},
        }
    return {"jsonrpc": "2.0", "id": request.request_id, "result": result}


def protocol_result(
    request: MCPRequest,
    tool_definitions: Sequence[dict[str, Any]],
    *,
    server_version: str = "0.1.0",
) -> dict[str, Any] | None:
    """Return a wrapped built-in response, or None for a call/notification.

    Callers handle tools/call through their authorized read service and return
    notifications/initialized as an empty 202 response. Only definitions visible
    to the authenticated caller may be supplied here.
    """
    capabilities = {"tools": {"listChanged": False}}
    if request.method == "server/discover":
        result = {
            "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": capabilities,
        }
    elif request.method == "initialize":
        result = {
            "protocolVersion": LEGACY_PROTOCOL_VERSION,
            "capabilities": capabilities,
            "serverInfo": {"name": "threatlens", "version": server_version},
        }
    elif request.method == "tools/list":
        result = {"tools": sorted(tool_definitions, key=lambda tool: tool["name"])}
    elif request.method == "ping":
        result = {}
    else:
        return None
    if request.protocol_version == LATEST_PROTOCOL_VERSION:
        # Discovery and tool listing are CacheableResult in the current schema.
        # Never allow authorization-dependent metadata into shared caches.
        result.update({"ttlMs": 0, "cacheScope": "private"})
    return success_response(request, result, server_version=server_version)
