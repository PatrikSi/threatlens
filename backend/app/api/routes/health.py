from datetime import datetime, timezone

import redis
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_optional_current_user
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.db.session import get_db
from app.models.user import User
from app.services.notification_webhooks import get_notification_delivery_queue_snapshot
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(db: Session = Depends(get_db)):
    return _readiness_response(db)


@router.get("/ready")
def ready(db: Session = Depends(get_db)):
    return _readiness_response(db)


@router.get("/live")
def live():
    return {"ok": True}


@router.get("/worker")
def worker(user: User | None = Depends(get_optional_current_user)):
    detailed = _is_admin_user(user)
    return _worker_health_response(detailed=detailed)


@router.get("/beat")
def beat(user: User | None = Depends(get_optional_current_user)):
    detailed = _is_admin_user(user)
    return _beat_health_response(detailed=detailed)


@router.get("/notifications")
def notifications(db: Session = Depends(get_db), user: User | None = Depends(get_optional_current_user)):
    snapshot = get_notification_delivery_queue_snapshot(db)
    status_code = status.HTTP_200_OK if snapshot.ok else status.HTTP_503_SERVICE_UNAVAILABLE
    payload = snapshot.model_dump()
    if not _is_admin_user(user):
        payload = {
            "ok": snapshot.ok,
            "status": snapshot.status,
        }
    return JSONResponse(status_code=status_code, content=payload)


def _readiness_response(db: Session):
    settings = get_settings()

    db_ok = False
    redis_ok = False

    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        client = redis.Redis.from_url(settings.redis_url)
        redis_ok = bool(client.ping())
    except Exception:
        redis_ok = False

    ok = db_ok and redis_ok
    status_code = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content={"ok": ok, "db": db_ok, "redis": redis_ok})


def _worker_health_response(*, detailed: bool):
    settings = get_settings()

    worker_ok = False
    workers: dict[str, str] = {}
    try:
        inspector = celery_app.control.inspect(timeout=settings.health_worker_ping_timeout_seconds)
        raw_ping = inspector.ping() or {}
        worker_ok = bool(raw_ping)
        for worker_name, response in raw_ping.items():
            pong_value = response.get("ok") if isinstance(response, dict) else None
            workers[worker_name] = str(pong_value or "unknown")
    except Exception:
        worker_ok = False

    status_code = status.HTTP_200_OK if worker_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    payload = {"ok": worker_ok}
    if detailed:
        payload["workers"] = workers
    return JSONResponse(status_code=status_code, content=payload)


def _beat_health_response(*, detailed: bool):
    settings = get_settings()
    now = datetime.now(timezone.utc)

    heartbeat_raw: str | None = None
    beat_ok = False
    age_seconds: int | None = None

    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        heartbeat_raw = client.get(settings.beat_heartbeat_key)
    except Exception:
        heartbeat_raw = None

    if heartbeat_raw:
        try:
            heartbeat_at = datetime.fromisoformat(heartbeat_raw)
            if heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
            age_seconds = max(0, int((now - heartbeat_at).total_seconds()))
            beat_ok = age_seconds <= settings.beat_heartbeat_stale_after_seconds
        except ValueError:
            beat_ok = False

    status_code = status.HTTP_200_OK if beat_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    payload = {"ok": beat_ok}
    if detailed:
        payload.update(
            {
                "heartbeat_key": settings.beat_heartbeat_key,
                "heartbeat_at": heartbeat_raw,
                "age_seconds": age_seconds,
                "stale_after_seconds": settings.beat_heartbeat_stale_after_seconds,
            }
        )
    return JSONResponse(status_code=status_code, content=payload)


def _is_admin_user(user: User | None) -> bool:
    return user is not None and user.role == ROLE_ADMIN
