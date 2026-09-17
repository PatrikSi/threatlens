"""Stateless read-only MCP with explicitly scoped local bearer credentials."""
from __future__ import annotations

import hashlib
import logging
import time
from contextlib import ExitStack
from typing import Literal

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response

from app.api.deps import resolve_client_ip
from app.api.mcp_context import parse_mcp_bearer_token, resolve_mcp_read_context
from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_MCP
from app.db import session as db_session
from app.db.budgets import DatabaseDeadlineExceeded
from app.services.data_access_policy import DataPolicyError
from app.services.export_job_access import ExportJobAccessDenied
from app.services.mcp_access import (
    fence_mcp_read_context, mcp_transfer_timeout_seconds, record_mcp_audit,
)
from app.services.mcp_dispatch import dispatch_mcp_read, mcp_audit_operation
from app.services.mcp_protocol import MCPProtocolError, MCPRequest, error_response, parse_json, parse_request
from app.services.mcp_read_contracts import MCPReadContext, json_bytes
from app.services.mcp_read_service import tool_required_permissions
from app.services.mcp_runtime import enforce_mcp_rate_limit, mcp_database_budget, remaining_seconds
from app.services.mcp_transport import MCPBodyTooLarge, transport_error

router = APIRouter(tags=["MCP"])
logger = logging.getLogger("threatlens.mcp")


@router.post(
    "/mcp", response_class=Response,
    summary="Read-only MCP endpoint using explicitly scoped bearer tokens",
    description=(
        "Stateless MCP Streamable HTTP supporting protocol revisions 2026-07-28 and 2025-11-25. "
        "Requires MCP_ENABLED and a personal or service-account bearer token explicitly containing read:mcp. "
        "Each tool also requires its ordinary resource permissions. Cookie sessions and OAuth discovery are not supported. "
        "Requests and responses use MCP JSON-RPC envelopes, rather than the REST error contract."
    ),
    openapi_extra={
        "security": [{"ApiTokenBearer": []}],
        "x-threatlens-error-format": "mcp-jsonrpc",
        "x-threatlens-required-token-scopes": [SCOPE_READ_MCP],
        "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}},
        "responses": {"200": {"description": "MCP JSON-RPC result or tool error", "content": {"application/json": {"schema": {"type": "object"}}}},
                      "202": {"description": "Legacy initialized notification accepted"},
                      **{str(code): {"description": "MCP JSON-RPC protocol, authorization or transport error", "content": {"application/json": {"schema": {"type": "object"}}}}
                         for code in (400, 401, 403, 404, 405, 406, 413, 415, 429, 500, 503, 504)}},
    },
)
async def handle_mcp_request(request: Request) -> Response:
    try:
        body = await request.body()
        rpc = parse_request(parse_json(body), request.headers)
    except MCPBodyTooLarge:
        return transport_error(413, "mcp_request_too_large", "MCP request exceeds the configured byte limit.")
    except ClientDisconnect:
        return Response(status_code=499)
    except MCPProtocolError as exc:
        return JSONResponse(error_response(exc), status_code=exc.status_code, headers={"Cache-Control": "no-store"})
    # The outer MCP middleware owns cleanup through the final socket send. A
    # response/background callback would release fences too early behind the
    # application's ordinary HTTP logging middleware.
    resources = ExitStack()
    request.scope.setdefault("state", {})["mcp_cleanup"] = resources.close
    return await run_in_threadpool(_prepare_response, request, rpc, resources)


# Authentication runs inside the owned transaction so MCP can retain its fences
# through transfer. Publish the same permission metadata used by dependency gates.
handle_mcp_request._threatlens_required_scopes = (SCOPE_READ_MCP,)


def _prepare_response(request: Request, rpc: MCPRequest, resources: ExitStack) -> Response:
    settings = get_settings()
    deadline = request.state.mcp_deadline
    context: MCPReadContext | None = None
    db: Session | None = None
    audit_db: Session | None = None
    operation = mcp_audit_operation(rpc)
    try:
        enforce_mcp_rate_limit(bucket=f"source:{resolve_client_ip(request)}",
                               limit=settings.mcp_rate_limit_per_minute * 5, deadline=deadline)
        parse_mcp_bearer_token(request)
        # Acquire both connections before taking any policy/credential locks.
        # Pin them across credential-use commits so auditing cannot wait on an
        # exhausted pool while the response holds authorization fences.
        read_connection = resources.enter_context(db_session.engine.connect())
        remaining_seconds(deadline)
        audit_connection = resources.enter_context(db_session.engine.connect())
        remaining_seconds(deadline)
        db = resources.enter_context(Session(bind=read_connection, autoflush=False))
        audit_db = resources.enter_context(Session(bind=audit_connection, autoflush=False))
        with mcp_database_budget(db, deadline=deadline):
            context = resolve_mcp_read_context(
                request, db,
                cursor_secret=hashlib.sha256(("threatlens-mcp-cursors-v1:" + settings.jwt_secret).encode()).digest(),
            )
            enforce_mcp_rate_limit(
                bucket=f"principal:{context.authorization.principal_type}:{context.principal.id}",
                limit=settings.mcp_rate_limit_per_minute, deadline=deadline,
            )
            payload, failed = dispatch_mcp_read(
                db, context=context, request=rpc,
                response_limit=settings.mcp_response_max_bytes,
                canonical_base_url=settings.public_app_url or "",
            )
            encoded = json_bytes(payload) if payload is not None else b""
            if len(encoded) > settings.mcp_response_max_bytes:
                raise MCPProtocolError(-32603, "Response exceeds the configured byte limit.", status_code=503, request_id=rpc.request_id)
            permissions = tool_required_permissions(operation) if rpc.method == "tools/call" and not failed else ()
            fence_mcp_read_context(db, context, required_permissions=permissions)
            _audit(request, audit_db, context, operation, "denied" if failed else "prepared", deadline)
            # Auditing commits only its separate session. Recheck that no domain
            # helper ended this read's authorization transaction before delivery.
            fence_mcp_read_context(db, context, required_permissions=permissions)
            allowance = mcp_transfer_timeout_seconds(db, context, maximum=remaining_seconds(deadline))
            request.state.mcp_deadline = min(deadline, time.monotonic() + allowance)
        return Response(
            encoded, status_code=202 if payload is None else 200,
            media_type="application/json" if payload is not None else None,
            headers={"Cache-Control": "no-store", "MCP-Protocol-Version": rpc.protocol_version},
        )
    except Exception as exc:
        # Do not hold failed policy/resource locks while persisting diagnostics.
        if db is not None:
            db.rollback()
        if audit_db is not None:
            _audit_failure(request, audit_db, context, operation, deadline)
        return _error_response(rpc, exc)


def _audit(
    request: Request, audit_db: Session, context: MCPReadContext | None,
    operation: str, outcome: Literal["prepared", "denied", "failed"], deadline: float,
) -> None:
    with mcp_database_budget(audit_db, deadline=deadline):
        record_mcp_audit(audit_db, request, context=context, operation=operation, outcome=outcome)
        audit_db.commit()


def _audit_failure(
    request: Request, audit_db: Session, context: MCPReadContext | None,
    operation: str, deadline: float,
) -> None:
    try:
        remaining_seconds(deadline)
        audit_db.rollback()
        _audit(request, audit_db, context, operation, "failed", deadline)
    except Exception as exc:
        logger.warning("mcp_audit_unavailable error_type=%s", type(exc).__name__)


def _error_response(rpc: MCPRequest, exc: Exception) -> Response:
    headers = {"Cache-Control": "no-store"}
    if isinstance(exc, MCPProtocolError):
        error = exc
    elif isinstance(exc, HTTPException):
        headers.update(exc.headers or {})
        error = MCPProtocolError(-31000, str(exc.detail), status_code=exc.status_code, request_id=rpc.request_id,
                                 data={"code": getattr(exc, "error_code", "mcp_access_denied")})
    elif isinstance(exc, ExportJobAccessDenied):
        error = MCPProtocolError(-31000, "MCP access is no longer available. Reauthenticate and retry.",
                                 status_code=403, request_id=rpc.request_id, data={"code": "mcp_access_changed"})
    elif isinstance(exc, DataPolicyError):
        error = MCPProtocolError(-31000, "MCP data access could not be authorized. Check access and retry.",
                                 status_code=exc.status_code, request_id=rpc.request_id, data={"code": exc.code})
    elif isinstance(exc, (DatabaseDeadlineExceeded, TimeoutError)):
        error = MCPProtocolError(-31000, "MCP request timed out. Retry with a smaller request.",
                                 status_code=504, request_id=rpc.request_id, data={"code": "mcp_deadline"})
    else:
        logger.error("mcp_request_failed error_type=%s", type(exc).__name__)
        error = MCPProtocolError(-32603, "MCP is temporarily unavailable. Retry shortly.",
                                 status_code=503 if isinstance(exc, SQLAlchemyError) else 500,
                                 request_id=rpc.request_id, data={"code": "mcp_unavailable"})
    if error.status_code in {429, 503}:
        headers.setdefault("Retry-After", "5")
    if error.status_code == 401:
        headers.setdefault("WWW-Authenticate", 'Bearer realm="ThreatLens MCP"')
    return JSONResponse(error_response(error), status_code=error.status_code, headers=headers)
