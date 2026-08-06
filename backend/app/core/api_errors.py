from __future__ import annotations

import logging
import uuid
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging_config import get_log_context


logger = logging.getLogger("threatlens.api.errors")
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_ERROR_CODE_BY_STATUS = {
    400: "invalid_request",
    401: "authentication_required",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    410: "gone",
    412: "precondition_failed",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    425: "too_early",
    429: "rate_limited",
    451: "unavailable_for_legal_reasons",
    500: "internal_error",
    501: "not_implemented",
    502: "upstream_error",
    503: "service_unavailable",
    504: "upstream_timeout",
}


def install_api_error_handlers(application: FastAPI) -> None:
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(Exception, unexpected_exception_handler)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = request_id_for(request)
    status_code = int(exc.status_code)
    detail = exc.detail
    message = _detail_message(detail, status_code)
    logger.debug(
        "request_rejected error_code=%s detail=%s",
        error_code_for_status(status_code),
        message,
        extra=_error_log_fields(request, request_id=request_id, status=status_code),
    )
    return error_response(
        status_code=status_code,
        detail=detail,
        message=message,
        request_id=request_id,
        headers=exc.headers,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = request_id_for(request)
    issues = [_public_validation_issue(issue) for issue in exc.errors()]
    message = _validation_message(issues)
    logger.debug(
        "request_validation_failed issue_count=%s fields=%s",
        len(issues),
        [".".join(str(part) for part in issue.get("loc", [])) for issue in issues[:10]],
        extra=_error_log_fields(request, request_id=request_id, status=422),
    )
    return error_response(
        status_code=422,
        detail=issues,
        message=message,
        request_id=request_id,
        code="validation_error",
    )


async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_for(request)
    logger.error(
        "unhandled_request_exception error_type=%s",
        type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
        extra=_error_log_fields(request, request_id=request_id, status=500),
    )
    return error_response(
        status_code=500,
        detail="The server could not complete the request.",
        message="The server could not complete the request.",
        request_id=request_id,
        code="internal_error",
    )


def error_response(
    *,
    status_code: int,
    detail: Any,
    message: str,
    request_id: str,
    code: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        headers=response_headers,
        content={
            "detail": detail,
            "error": {
                "code": code or error_code_for_status(status_code),
                "message": message,
                "request_id": request_id,
                "status": status_code,
                "retryable": status_code in _RETRYABLE_STATUS_CODES,
            },
        },
    )


def request_id_for(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None) or get_log_context().get("request_id")
    return str(request_id or uuid.uuid4())


def error_code_for_status(status_code: int) -> str:
    return _ERROR_CODE_BY_STATUS.get(status_code, "request_failed")


def _detail_message(detail: Any, status_code: int) -> str:
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, dict):
        candidate = detail.get("message") or detail.get("detail")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return f"HTTP {status_code}"


def _public_validation_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        key: issue[key]
        for key in ("type", "loc", "msg")
        if key in issue
    }


def _validation_message(issues: list[dict[str, Any]]) -> str:
    summaries: list[str] = []
    for issue in issues[:5]:
        location = ".".join(str(part) for part in issue.get("loc", []) if part != "body")
        message = str(issue.get("msg", "Invalid value")).strip()
        summaries.append(f"{location}: {message}" if location else message)
    if not summaries:
        return "Request validation failed."
    suffix = f"; plus {len(issues) - len(summaries)} more issue(s)" if len(issues) > len(summaries) else ""
    return f"Request validation failed: {'; '.join(summaries)}{suffix}"


def _error_log_fields(request: Request, *, request_id: str, status: int) -> dict[str, object]:
    route = request.scope.get("route")
    return {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "route": getattr(route, "path", None),
        "status": status,
    }


__all__ = [
    "error_code_for_status",
    "error_response",
    "install_api_error_handlers",
    "request_id_for",
]
