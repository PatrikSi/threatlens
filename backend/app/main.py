import logging
import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

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
        "The published API contract is versioned under `/v1` on the backend service and `/api/v1` through the web proxy. "
        "Legacy unversioned routes remain available for compatibility but are intentionally excluded from the OpenAPI schema."
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
