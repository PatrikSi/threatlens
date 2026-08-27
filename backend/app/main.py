import hashlib
import json
import logging
import time
import uuid
from copy import deepcopy
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from app.core.api_errors import install_api_error_handlers
from app.core.config import Settings, get_settings
from app.core.logging_config import (
    configure_logging,
    log_configuration_summary,
    redact_log_text,
    reset_log_context,
    set_log_context,
    verbose_logging_enabled,
)
from app.db import session as db_session
from app.api.routes import (
    ai,
    alerts,
    audit,
    auth,
    auth_security,
    exports,
    feeds,
    health,
    integrations,
    investigations,
    items,
    notifications,
    oidc,
    operations,
    reports,
    stats,
    tagging,
    tags,
    tokens,
    users,
    views,
)
from app.services.encrypted_data_inventory import record_startup_encrypted_data_inventory_error, refresh_startup_encrypted_data_inventory
from app.version import get_app_version

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger("threatlens.api")
_REQUEST_ID_ALLOWED_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._")
API_VERSION = "v1"
API_SERVICE_PREFIX = f"/{API_VERSION}"
WEB_PROXY_API_PREFIX = f"/api/{API_VERSION}"
API_SERVICE_ROOT = "/"
WEB_PROXY_ROOT = "/api"
OPENAPI_PROXY_PATH = "/api/openapi.json"
API_TOKEN_SECURITY_SCHEME_NAME = "ApiTokenBearer"
SESSION_COOKIE_SECURITY_SCHEME_NAME = "SessionCookieAuth"
OPENAPI_CONTRACT_ANCHOR_FIELD = "x-threatlens-contract-sha256"
OPENAPI_REQUIRED_TOKEN_SCOPES_FIELD = "x-threatlens-required-token-scopes"
PUBLIC_BROWSER_RESPONSE_HEADERS = (
    "Content-Disposition",
    "Retry-After",
    "X-Current-Revision",
    "X-Current-Row-Version",
    "X-Current-Rule-Revision",
    "X-Current-Security-Version",
    "X-Current-Version",
    "X-Error-Code",
    "X-Request-ID",
    "X-ThreatLens-Revoked-Descendant-Count",
    "X-ThreatLens-Revoked-Token-Count",
    "X-ThreatLens-Root-Token-Revoked",
)
SAVED_VIEW_QUERY_SCHEMA = "SavedViewQueryPayload"
SAVED_VIEW_QUERY_INPUT_SCHEMA = "SavedViewQueryPayload-Input"
SAVED_VIEW_QUERY_OUTPUT_SCHEMA = "SavedViewQueryPayload-Output"
API_ROUTERS: tuple[APIRouter, ...] = (
    auth.router,
    auth_security.router,
    oidc.router,
    exports.router,
    reports.router,
    feeds.router,
    items.router,
    tags.router,
    tagging.router,
    views.router,
    alerts.router,
    tokens.router,
    users.router,
    audit.router,
    integrations.router,
    investigations.router,
    notifications.router,
    ai.router,
    stats.router,
    operations.router,
    health.router,
)


def _build_openapi_visibility_kwargs(active_settings: Settings) -> dict[str, str | None]:
    is_production = active_settings.app_env.lower() in {"production", "prod"}
    kwargs: dict[str, str | None] = {}
    if is_production and not active_settings.expose_api_docs_in_production:
        kwargs.update({"docs_url": None, "redoc_url": None})
    if is_production and not active_settings.expose_openapi_schema_in_production:
        kwargs["openapi_url"] = None
    return kwargs


def _should_mount_legacy_api_aliases(active_settings: Settings) -> bool:
    return active_settings.app_env.lower() not in {"production", "prod"}


@asynccontextmanager
async def app_lifespan(_application: FastAPI):
    log_configuration_summary(settings, logger=logger)
    if settings.allow_insecure_http_oidc:
        logger.warning(
            "insecure_http_oidc_enabled OIDC authorization codes, tokens, and identity claims may traverse plaintext HTTP"
        )
    with db_session.SessionLocal() as db:
        try:
            snapshot = refresh_startup_encrypted_data_inventory(db, settings=settings)
            logger.info(
                "startup_encrypted_data_inventory_complete status=%s unreadable_records=%s unreadable_fields=%s",
                snapshot.status,
                snapshot.summary.unreadable_records,
                snapshot.summary.unreadable_fields,
            )
        except Exception as exc:
            record_startup_encrypted_data_inventory_error(redact_log_text(exc, max_chars=4000))
            logger.warning("startup_encrypted_data_inventory_failed error=%s", exc, exc_info=True)
    yield


app = FastAPI(
    title="ThreatLens API",
    version=get_app_version(),
    summary="ThreatLens API contract.",
    description=(
        "The published API contract is versioned under `/v1` on the backend service and `/api/v1` through the bundled "
        "web proxy. "
        "Authorization bearer credentials are scoped API tokens, while browser logins establish HttpOnly cookie sessions "
        "through `/v1/auth/login`. "
        "The bundled web proxy publishes only `/api/v1/*` plus `/api/openapi.json`. "
        "Any unversioned backend-service compatibility aliases are intentionally excluded from the OpenAPI schema and "
        "shipped browser/runtime contract."
    ),
    servers=[
        {"url": API_SERVICE_ROOT, "description": "Backend service root"},
        {"url": WEB_PROXY_ROOT, "description": "Web reverse proxy root"},
    ],
    license_info={
        "name": "Apache-2.0",
        "identifier": "Apache-2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
    lifespan=app_lifespan,
    **_build_openapi_visibility_kwargs(settings),
)
install_api_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=list(PUBLIC_BROWSER_RESPONSE_HEADERS),
)

if settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = _normalize_request_id(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    context_token = set_log_context(request_id=request_id)
    started_at = time.perf_counter()
    try:
        if verbose_logging_enabled(settings):
            logger.debug(
                "request_started query_keys=%s content_type=%s user_agent=%s",
                sorted(set(request.query_params.keys())),
                request.headers.get("content-type", ""),
                request.headers.get("user-agent", ""),
                extra=_request_log_fields(request),
            )
        response = await call_next(request)

        duration_ms = (time.perf_counter() - started_at) * 1000
        response.headers["X-Request-ID"] = request_id
        completion_level = _request_completion_log_level(response.status_code, duration_ms)
        logger.log(
            completion_level,
            "request_complete",
            extra=_request_log_fields(request, status=response.status_code, duration_ms=duration_ms),
        )
        return response
    finally:
        reset_log_context(context_token)


def _request_log_fields(
    request: Request,
    *,
    status: int | None = None,
    duration_ms: float | None = None,
) -> dict[str, object]:
    route = request.scope.get("route")
    fields: dict[str, object] = {
        "method": request.method,
        "path": request.url.path,
        "route": getattr(route, "path", None),
        "status": status,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
    }
    if settings.log_include_client_ip:
        fields["client_ip"] = request.client.host if request.client else "unknown"
    return fields


def _request_completion_log_level(status_code: int, duration_ms: float) -> int:
    if status_code >= 500:
        return logging.ERROR
    if duration_ms >= settings.log_slow_request_ms:
        return logging.WARNING
    return logging.INFO


def _normalize_request_id(raw_request_id: str | None) -> str:
    generated = str(uuid.uuid4())
    if not raw_request_id:
        return generated

    candidate = raw_request_id.strip()
    if not candidate:
        return generated

    sanitized = "".join(char for char in candidate if char in _REQUEST_ID_ALLOWED_CHARS)
    if not sanitized:
        return generated

    return sanitized[:128]


def _mount_api_routers(application: FastAPI, *, include_legacy_aliases: bool) -> None:
    for router in API_ROUTERS:
        application.include_router(router, prefix=API_SERVICE_PREFIX)
        if include_legacy_aliases:
            application.include_router(router, include_in_schema=False)


_mount_api_routers(app, include_legacy_aliases=_should_mount_legacy_api_aliases(settings))


def _collect_route_token_scopes(route: Any) -> tuple[str, ...]:
    scopes: list[str] = []
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        call = getattr(dependant, "call", None)
        required = getattr(call, "_threatlens_required_scopes", ())
        for scope in required or ():
            if scope not in scopes:
                scopes.append(scope)
        stack.extend(getattr(dependant, "dependencies", []) or [])
    return tuple(scopes)


def _iter_effective_api_routes(application: FastAPI):
    for route in application.routes:
        if isinstance(route, APIRoute):
            yield route
            continue

        route_contexts = getattr(route, "effective_route_contexts", None)
        if not callable(route_contexts):
            continue
        for route_context in route_contexts():
            if isinstance(getattr(route_context, "original_route", None), APIRoute):
                yield route_context


def _route_required_token_scopes_by_operation(application: FastAPI) -> dict[tuple[str, str], tuple[str, ...]]:
    required_by_operation: dict[tuple[str, str], tuple[str, ...]] = {}
    for route in _iter_effective_api_routes(application):
        scopes = _collect_route_token_scopes(route)
        if not scopes:
            continue
        for method in route.methods or []:
            method_key = method.lower()
            if method_key in {"head", "options"}:
                continue
            required_by_operation[(route.path, method_key)] = scopes
    return required_by_operation


def _apply_published_security_contract(
    schema: dict[str, Any],
    *,
    required_scopes_by_operation: dict[tuple[str, str], tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    components = schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes.pop("OAuth2PasswordBearer", None)
    security_schemes[API_TOKEN_SECURITY_SCHEME_NAME] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "API token",
        "description": (
            "Use a scoped personal API token in the `Authorization: Bearer <token>` header. "
            "Browser sign-in at `/v1/auth/login` creates a cookie session and returns only session-cookie metadata; "
            "bearer auth requires a dedicated API token."
        ),
    }
    security_schemes[SESSION_COOKIE_SECURITY_SCHEME_NAME] = {
        "type": "apiKey",
        "in": "cookie",
        "name": settings.auth_cookie_name,
        "description": (
            "HttpOnly browser session cookie established by `/v1/auth/login`, mirrored through the web proxy at "
            "`/api/v1/auth/login`. Cookie-authenticated mutating requests must also send the CSRF header."
        ),
    }

    required_scopes_by_operation = required_scopes_by_operation or {}
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            required_scopes = required_scopes_by_operation.get((path, method.lower()))
            if required_scopes:
                operation[OPENAPI_REQUIRED_TOKEN_SCOPES_FIELD] = list(required_scopes)
            security = operation.get("security")
            if not security:
                continue
            if not any(
                isinstance(requirement, dict) and "OAuth2PasswordBearer" in requirement for requirement in security
            ):
                continue
            operation["security"] = [
                {API_TOKEN_SECURITY_SCHEME_NAME: []},
                {SESSION_COOKIE_SECURITY_SCHEME_NAME: []},
            ]

    return schema


def _replace_schema_ref(node: Any, *, source: str, target: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and value == source:
                node[key] = target
            else:
                _replace_schema_ref(value, source=source, target=target)
    elif isinstance(node, list):
        for value in node:
            _replace_schema_ref(value, source=source, target=target)


def _preserve_saved_view_query_schema_names(schema: dict[str, Any]) -> dict[str, Any]:
    schemas = schema.get("components", {}).get("schemas", {})
    merged_schema = schemas.pop(SAVED_VIEW_QUERY_SCHEMA, None)
    if not isinstance(merged_schema, dict):
        return schema

    schemas[SAVED_VIEW_QUERY_INPUT_SCHEMA] = deepcopy(merged_schema)
    schemas[SAVED_VIEW_QUERY_OUTPUT_SCHEMA] = deepcopy(merged_schema)

    source_ref = f"#/components/schemas/{SAVED_VIEW_QUERY_SCHEMA}"
    for component_name, target_name in (
        ("SavedViewCreate", SAVED_VIEW_QUERY_INPUT_SCHEMA),
        ("SavedViewUpdate", SAVED_VIEW_QUERY_INPUT_SCHEMA),
        ("SavedViewResponse", SAVED_VIEW_QUERY_OUTPUT_SCHEMA),
    ):
        component = schemas.get(component_name)
        if isinstance(component, dict):
            _replace_schema_ref(
                component,
                source=source_ref,
                target=f"#/components/schemas/{target_name}",
            )
    return schema


def _apply_contract_anchor(schema: dict[str, Any]) -> dict[str, Any]:
    info = schema.setdefault("info", {})
    info.pop(OPENAPI_CONTRACT_ANCHOR_FIELD, None)
    digest = hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    info[OPENAPI_CONTRACT_ANCHOR_FIELD] = digest
    return schema


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
        servers=app.servers,
        contact=app.contact,
        license_info=app.license_info,
    )
    schema = _preserve_saved_view_query_schema_names(schema)
    schema = _apply_published_security_contract(
        schema,
        required_scopes_by_operation=_route_required_token_scopes_by_operation(app),
    )
    app.openapi_schema = _apply_contract_anchor(schema)
    return app.openapi_schema


app.openapi = custom_openapi
