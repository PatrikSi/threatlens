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

_API_ERROR_DETAIL_SCHEMA = {
    "type": "object",
    "required": ["code", "message", "request_id", "status", "retryable"],
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "request_id": {"type": "string"},
        "status": {"type": "integer"},
        "retryable": {"type": "boolean"},
        "context": {"type": "object", "additionalProperties": True},
    },
}
_API_ERROR_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["detail", "error"],
    "properties": {
        "detail": {},
        "error": {"$ref": "#/components/schemas/ApiErrorDetail"},
    },
}


class ApiHTTPException(StarletteHTTPException):
    """HTTP error with a stable machine-readable code and legacy-safe detail."""

    def __init__(
        self,
        *,
        status_code: int,
        detail: Any,
        error_code: str,
        error_context: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code
        self.error_context = error_context


def install_api_error_handlers(application: FastAPI) -> None:
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )
    application.add_exception_handler(Exception, unexpected_exception_handler)


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    request_id = request_id_for(request)
    status_code = int(exc.status_code)
    detail = exc.detail
    message = _detail_message(detail, status_code)
    error_code = getattr(exc, "error_code", None) or error_code_for_status(status_code)
    error_context = getattr(exc, "error_context", None)
    logger.debug(
        "request_rejected error_code=%s detail=%s",
        error_code,
        message,
        extra=_error_log_fields(request, request_id=request_id, status=status_code),
    )
    return error_response(
        status_code=status_code,
        detail=detail,
        message=message,
        request_id=request_id,
        code=error_code,
        error_context=error_context,
        headers=exc.headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
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


async def unexpected_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
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
    error_context: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id
    error_payload: dict[str, Any] = {
        "code": code or error_code_for_status(status_code),
        "message": message,
        "request_id": request_id,
        "status": status_code,
        "retryable": status_code in _RETRYABLE_STATUS_CODES,
    }
    if error_context is not None:
        error_payload["context"] = error_context
    return JSONResponse(
        status_code=status_code,
        headers=response_headers,
        content={
            "detail": detail,
            "error": error_payload,
        },
    )


def request_id_for(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None) or get_log_context().get(
        "request_id"
    )
    return str(request_id or uuid.uuid4())


def error_code_for_status(status_code: int) -> str:
    return _ERROR_CODE_BY_STATUS.get(status_code, "request_failed")


def apply_openapi_error_contract(schema: dict[str, Any]) -> dict[str, Any]:
    """Align declared API errors with the envelope emitted by our handlers."""

    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas["ApiErrorDetail"] = _API_ERROR_DETAIL_SCHEMA
    schemas["ApiErrorResponse"] = _API_ERROR_RESPONSE_SCHEMA
    error_ref = {"$ref": "#/components/schemas/ApiErrorResponse"}
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            if operation.get("security"):
                for status_code, description in (
                    ("401", "Authentication is required or the credential is invalid"),
                    (
                        "403",
                        "The authenticated principal is not allowed to perform this operation",
                    ),
                    ("503", "Authorization policy could not be evaluated safely"),
                ):
                    responses.setdefault(
                        status_code,
                        {
                            "description": description,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ApiErrorResponse"
                                    }
                                }
                            },
                        },
                    )
            for status_code, response in responses.items():
                if not str(status_code).isdigit() or int(status_code) < 400:
                    continue
                if not isinstance(response, dict) or "$ref" in response:
                    continue
                content = response.setdefault("content", {})
                content.setdefault("application/json", {})["schema"] = error_ref
    return schema


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
    return {key: issue[key] for key in ("type", "loc", "msg") if key in issue}


def _validation_message(issues: list[dict[str, Any]]) -> str:
    summaries: list[str] = []
    for issue in issues[:5]:
        location = ".".join(
            str(part) for part in issue.get("loc", []) if part != "body"
        )
        message = str(issue.get("msg", "Invalid value")).strip()
        summaries.append(f"{location}: {message}" if location else message)
    if not summaries:
        return "Request validation failed."
    suffix = (
        f"; plus {len(issues) - len(summaries)} more issue(s)"
        if len(issues) > len(summaries)
        else ""
    )
    return f"Request validation failed: {'; '.join(summaries)}{suffix}"


def _error_log_fields(
    request: Request, *, request_id: str, status: int
) -> dict[str, object]:
    route = request.scope.get("route")
    return {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "route": getattr(route, "path", None),
        "status": status,
    }


__all__ = [
    "ApiHTTPException",
    "error_code_for_status",
    "error_response",
    "install_api_error_handlers",
    "request_id_for",
]
