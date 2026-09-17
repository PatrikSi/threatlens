"""HTTP boundaries for the opt-in, stateless MCP endpoint."""
from __future__ import annotations

import threading
import time
import math
import logging

import anyio
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.logging_config import reset_log_context, set_log_context
from app.core.request_ids import normalize_request_id

logger = logging.getLogger("threatlens.mcp")

MCP_PATH = "/v1/mcp"
_UNIQUE_HEADERS = (
    "authorization", "origin", "content-type", "content-length",
    "mcp-protocol-version", "mcp-method", "mcp-name",
)
_CORS_HEADERS = frozenset({
    "authorization", "content-type", "mcp-protocol-version", "mcp-method", "mcp-name", "x-request-id",
})


def transport_error(status: int, code: str, message: str, *, retry_after: int | None = None) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="ThreatLens MCP"'
    if status == 405:
        headers["Allow"] = "POST, OPTIONS"
    return JSONResponse(
        {"jsonrpc": "2.0", "id": None, "error": {
            "code": -31000, "message": message, "data": {"code": code},
        }}, status_code=status, headers=headers,
    )


class MCPTransportMiddleware:
    """Limit the complete request, including the final downstream socket send.

    Admission is per API process; the Redis budget additionally limits requests
    across processes by source and authenticated principal. No MCP session state
    or cookies are accepted. Other HTTP routes are untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._lock = threading.Lock()
        self._active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].rstrip("/") != MCP_PATH:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        state = scope.setdefault("state", {})
        state["mcp_transport_owned"] = True
        state["request_id"] = normalize_request_id(headers.get("x-request-id"))
        state["mcp_phase"] = "admission"
        state["mcp_cleanup_outcome"] = "not_required"
        state["mcp_preparation_ms"] = 0.0
        state["mcp_cleanup_ms"] = 0.0
        origin = headers.get("origin")
        allowed_origin = (
            origin if len(headers.getlist("origin")) == 1
            and origin in get_settings().mcp_allowed_origins else None
        )
        context_token = set_log_context(request_id=state["request_id"])
        started_at = time.monotonic()
        status = None
        complete = False
        outcome = "failed"

        async def decorated_send(message: Message) -> None:
            nonlocal status, complete
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers = [
                    entry for entry in message.get("headers", [])
                    if not entry[0].lower().startswith(b"access-control-")
                    and entry[0].lower() not in {b"cache-control", b"x-request-id"}
                ]
                response_headers.extend([
                    (b"cache-control", b"no-store"),
                    (b"x-request-id", state["request_id"].encode("ascii")),
                ])
                if allowed_origin:
                    response_headers.extend([
                        (b"access-control-allow-origin", allowed_origin.encode("ascii")),
                        (b"access-control-expose-headers", b"X-Request-ID, Retry-After, MCP-Protocol-Version"),
                        (b"vary", b"Origin"),
                    ])
                    if scope["method"] == "OPTIONS" and status == 204:
                        response_headers.extend([
                            (b"access-control-allow-methods", b"POST"),
                            (b"access-control-allow-headers", ", ".join(sorted(_CORS_HEADERS)).encode("ascii")),
                            (b"access-control-max-age", b"600"),
                        ])
                message = {**message, "headers": response_headers}
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                complete = True

        try:
            await self._handle(scope, receive, decorated_send)
            if status == 504:
                outcome = "deadline"
            elif not complete:
                outcome = "incomplete"
            else:
                outcome = "rejected" if (status or 500) >= 400 else "completed"
        except TimeoutError:
            outcome = "deadline"
            raise
        except OSError:
            outcome = "disconnected"
            raise
        except BaseException as exc:
            outcome = "cancelled" if isinstance(exc, anyio.get_cancelled_exc_class()) else "failed"
            raise
        finally:
            try:
                level = logging.INFO if complete and (status or 500) < 500 and state["mcp_cleanup_outcome"] != "failed" else logging.WARNING
                logger.log(
                    level,
                    "mcp_request_complete outcome=%s phase=%s transfer_complete=%s cleanup_outcome=%s preparation_ms=%.2f cleanup_ms=%.2f",
                    outcome, state["mcp_phase"], complete, state["mcp_cleanup_outcome"],
                    state["mcp_preparation_ms"], state["mcp_cleanup_ms"],
                    extra={
                        "request_id": state["request_id"], "method": scope["method"],
                        "path": MCP_PATH, "status": status,
                        "duration_ms": (time.monotonic() - started_at) * 1000,
                    },
                )
            finally:
                reset_log_context(context_token)

    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        settings = get_settings()
        headers = Headers(scope=scope)
        rejection = self._rejection(scope, headers)
        if rejection is not None:
            await rejection(scope, receive, send)
            return
        if scope["method"] == "OPTIONS":
            response = self._preflight(headers)
            await response(scope, receive, send)
            return
        with self._lock:
            # A prepared response holds its read connection while a separate
            # connection persists the audit. Reserve both before admitting work.
            pool_capacity = settings.database_pool_size + settings.database_max_overflow
            concurrency_limit = min(settings.mcp_max_concurrent_requests, max(1, pool_capacity // 2))
            admitted = self._active < concurrency_limit
            if admitted:
                self._active += 1
        if not admitted:
            await transport_error(429, "mcp_busy", "MCP is busy. Retry shortly.", retry_after=1)(scope, receive, send)
            return
        started = False
        consumed = 0
        state = scope.setdefault("state", {})
        state["mcp_phase"] = "preparation"
        preparation_started_at = time.monotonic()
        state["mcp_deadline"] = time.monotonic() + settings.mcp_request_timeout_seconds

        def prepared() -> None:
            state["mcp_preparation_ms"] = (time.monotonic() - preparation_started_at) * 1000

        async def bounded_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > settings.mcp_request_max_bytes:
                    raise MCPBodyTooLarge
            return message

        async def bounded_send(message: Message) -> None:
            nonlocal started
            # The handler may shorten this deadline after learning credential or
            # OIDC assertion expiry. Translate monotonic time into AnyIO's clock
            # and tighten the live scope before handing bytes to the socket.
            remaining = state["mcp_deadline"] - time.monotonic()
            deadline.deadline = min(deadline.deadline, anyio.current_time() + remaining)
            if remaining <= 0:
                deadline.cancel()
                await anyio.lowlevel.checkpoint()
            if message["type"] == "http.response.start":
                prepared()
                started = True
                state["mcp_phase"] = "transfer"
            await send(message)

        try:
            with anyio.move_on_after(settings.mcp_request_timeout_seconds) as deadline:
                try:
                    await self.app(scope, bounded_receive, bounded_send)
                except MCPBodyTooLarge:
                    if started:
                        raise
                    await transport_error(
                        413, "mcp_request_too_large", "MCP request exceeds the configured byte limit."
                    )(scope, receive, bounded_send)
            if deadline.cancel_called:
                if not started:
                    prepared()
                if started:
                    raise TimeoutError("MCP response transfer deadline exceeded")
                # Even a failure response must not retain locks indefinitely if
                # the client stops receiving. No sensitive response was started.
                with anyio.move_on_after(1):
                    await transport_error(504, "mcp_deadline", "MCP request timed out. Retry with a smaller request.")(scope, receive, send)
        finally:
            if not started and state["mcp_preparation_ms"] == 0:
                prepared()
            cleanup_started_at = time.monotonic()
            try:
                cleanup = state.pop("mcp_cleanup", None)
                if cleanup is not None:
                    # This outer ASGI boundary owns database locks past any
                    # buffering middleware and through final socket handoff.
                    with anyio.CancelScope(shield=True):
                        try:
                            await anyio.to_thread.run_sync(cleanup)
                            state["mcp_cleanup_outcome"] = "completed"
                        except Exception as exc:
                            # Preserve the original response/transfer error while
                            # making release failures visible without SQL details.
                            state["mcp_cleanup_outcome"] = "failed"
                            logger.warning("mcp_cleanup_failed error_type=%s", type(exc).__name__)
            finally:
                state["mcp_cleanup_ms"] = (time.monotonic() - cleanup_started_at) * 1000
                with self._lock:
                    self._active -= 1

    @staticmethod
    def _rejection(scope: Scope, headers: Headers) -> Response | None:
        settings = get_settings()
        if not settings.mcp_enabled:
            return transport_error(404, "mcp_disabled", "MCP is not enabled on this installation.")
        if any(len(headers.getlist(key)) > 1 for key in _UNIQUE_HEADERS):
            return transport_error(400, "mcp_duplicate_header", "MCP request headers must not be duplicated.")
        if scope.get("query_string"):
            return transport_error(400, "mcp_query_not_supported", "Use the Authorization header and JSON request body.")
        origin = headers.get("origin")
        if origin is not None and origin not in settings.mcp_allowed_origins:
            return transport_error(403, "mcp_origin_denied", "This browser origin is not allowed for MCP.")
        if scope["method"] not in {"POST", "OPTIONS"}:
            return transport_error(405, "mcp_method_not_allowed", "This stateless MCP endpoint accepts POST requests.")
        if scope["method"] == "OPTIONS":
            return None
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return transport_error(415, "mcp_content_type", "MCP requests require application/json.")
        if headers.get("content-encoding", "identity").lower() != "identity":
            return transport_error(415, "mcp_content_encoding", "Compressed MCP requests are not supported.")
        raw_length = headers.get("content-length")
        if raw_length is not None:
            if not raw_length.isascii() or not raw_length.isdigit() or len(raw_length) > 10:
                return transport_error(400, "mcp_content_length", "Invalid Content-Length.")
            if int(raw_length) > settings.mcp_request_max_bytes:
                return transport_error(413, "mcp_request_too_large", "MCP request exceeds the configured byte limit.")
        if not _accepts_json(headers.get("accept", "*/*")):
            return transport_error(406, "mcp_accept", "MCP responses require Accept: application/json.")
        return None

    @staticmethod
    def _preflight(headers: Headers) -> Response:
        requested = {entry.strip().lower() for entry in headers.get("access-control-request-headers", "").split(",") if entry.strip()}
        if not headers.get("origin") or headers.get("access-control-request-method") != "POST" or not requested <= _CORS_HEADERS:
            return transport_error(403, "mcp_preflight_denied", "MCP preflight is not allowed.")
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": headers["origin"],
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": ", ".join(sorted(_CORS_HEADERS)),
            "Access-Control-Max-Age": "600", "Vary": "Origin", "Cache-Control": "no-store",
        })


class MCPBodyTooLarge(ValueError):
    """A streamed body exceeded the same limit as Content-Length requests."""


def _accepts_json(value: str) -> bool:
    """Honor explicit media-range precedence, including q=0 and fractional q."""
    best_specificity = -1
    quality = 0.0
    for entry in value.split(","):
        media_range, *parameters = entry.strip().lower().split(";")
        specificity = {"*/*": 0, "application/*": 1, "application/json": 2}.get(media_range.strip())
        if specificity is None:
            continue
        candidate = 1.0
        quality_seen = False
        for parameter in parameters:
            name, separator, raw = parameter.strip().partition("=")
            if name.strip() != "q":
                continue
            if quality_seen or not separator:
                candidate = 0.0
                break
            quality_seen = True
            try:
                candidate = float(raw.strip())
            except ValueError:
                candidate = 0.0
            if not math.isfinite(candidate) or not 0 <= candidate <= 1:
                candidate = 0.0
        if specificity > best_specificity:
            best_specificity, quality = specificity, candidate
        elif specificity == best_specificity:
            quality = max(quality, candidate)
    return quality > 0
