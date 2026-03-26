import logging
import time
import uuid

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.routes import ai, alerts, audit, auth, feeds, health, items, notifications, stats, tagging, tags, tokens, users, views

app = FastAPI(title="ThreatLens API", version="0.1.0")
settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
logger = logging.getLogger("threatlens.api")
_REQUEST_ID_ALLOWED_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._")

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

app.include_router(auth.router)
app.include_router(feeds.router)
app.include_router(items.router)
app.include_router(tags.router)
app.include_router(tagging.router)
app.include_router(views.router)
app.include_router(alerts.router)
app.include_router(tokens.router)
app.include_router(users.router)
app.include_router(audit.router)
app.include_router(notifications.router)
app.include_router(ai.router)
app.include_router(stats.router)
app.include_router(health.router)
