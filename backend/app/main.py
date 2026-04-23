import hashlib
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.core.config import Settings, get_settings
from app.api.routes import ai, alerts, audit, auth, feeds, health, items, notifications, stats, tagging, tags, tokens, users, views

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
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
API_ROUTERS: tuple[APIRouter, ...] = (
    auth.router,
    feeds.router,
    items.router,
    tags.router,
    tagging.router,
    views.router,
    alerts.router,
    tokens.router,
    users.router,
    audit.router,
    notifications.router,
    ai.router,
    stats.router,
    health.router,
)


def _build_openapi_visibility_kwargs(active_settings: Settings) -> dict[str, str | None]:
    is_production = active_settings.app_env.lower() in {"production", "prod"}
    if is_production and not active_settings.expose_api_docs_in_production:
        return {"docs_url": None, "redoc_url": None}
    return {}


app = FastAPI(
    title="ThreatLens API",
    version="0.1.0",
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
    **_build_openapi_visibility_kwargs(settings),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = _normalize_request_id(request.headers.get("x-request-id"))
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.exception(
            "request_failed method=%s path=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            duration_ms,
            request_id,
        )
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_complete method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


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


def _mount_api_routers(application: FastAPI) -> None:
    for router in API_ROUTERS:
        application.include_router(router, prefix=API_SERVICE_PREFIX)
        application.include_router(router, include_in_schema=False)


_mount_api_routers(app)


def _apply_published_security_contract(schema: dict[str, Any]) -> dict[str, Any]:
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

    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
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
    )
    schema = _apply_published_security_contract(schema)
    app.openapi_schema = _apply_contract_anchor(schema)
    return app.openapi_schema


app.openapi = custom_openapi
