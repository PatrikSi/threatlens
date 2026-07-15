from datetime import datetime, timezone

import redis
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_optional_current_user, require_token_scopes
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.core.token_scopes import SCOPE_READ_HEALTH, has_required_scope
from app.db.session import get_db
from app.models.user import User
from app.schemas.health import EncryptedDataInventoryResponse
from app.services.encrypted_data_inventory import scan_encrypted_data_inventory
from app.services.notification_webhooks import get_notification_delivery_queue_snapshot
from app.tasks.celery_app import QUEUE_AI, QUEUE_INGEST, QUEUE_MAINTENANCE, QUEUE_NOTIFICATIONS, QUEUE_PROCESSING, celery_app

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
):
    return _readiness_response(db, detailed=_can_view_detailed_health(request, user))


@router.get("/ready")
def ready(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
):
    return _readiness_response(db, detailed=_can_view_detailed_health(request, user))


@router.get("/live")
def live():
    return {"ok": True}


@router.get("/worker")
def worker(
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_HEALTH)),
):
    return _worker_health_response(detailed=True)


@router.get("/beat")
def beat(
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_HEALTH)),
):
    return _beat_health_response(detailed=True)


@router.get("/notifications")
def notifications(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_HEALTH)),
):
    snapshot = get_notification_delivery_queue_snapshot(db)
    status_code = status.HTTP_200_OK if snapshot.ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=snapshot.model_dump())


@router.get("/encrypted-data", response_model=EncryptedDataInventoryResponse)
def encrypted_data(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_HEALTH)),
):
    snapshot = scan_encrypted_data_inventory(db)
    status_code = status.HTTP_200_OK if snapshot.ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=snapshot.model_dump(mode="json"))


def _readiness_response(db: Session, *, detailed: bool):
    settings = get_settings()
    db_ok = _database_health_ok(db)
    redis_ok = _redis_health_ok(settings)
    worker_ok, _workers, _worker_queues = _worker_health_snapshot(settings)
    beat_ok, _heartbeat_raw, _age_seconds = _beat_health_snapshot(settings)

    ok = db_ok and redis_ok and worker_ok and beat_ok
    status_code = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE
    payload = {"ok": ok}
    if detailed:
        payload.update(
            {
                "db": db_ok,
                "redis": redis_ok,
                "worker": worker_ok,
                "beat": beat_ok,
            }
        )
    return JSONResponse(
        status_code=status_code,
        content=payload,
    )


def _worker_health_response(*, detailed: bool):
    settings = get_settings()
    worker_ok, workers, queue_snapshot = _worker_health_snapshot(settings)

    status_code = status.HTTP_200_OK if worker_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    payload = {"ok": worker_ok}
    if detailed:
        payload["workers"] = workers
        payload["queues"] = queue_snapshot
    return JSONResponse(status_code=status_code, content=payload)


def _beat_health_response(*, detailed: bool):
    settings = get_settings()
    beat_ok, heartbeat_raw, age_seconds = _beat_health_snapshot(settings)

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


def _can_view_detailed_health(request: Request, user: User | None) -> bool:
    if user is None or user.role != ROLE_ADMIN:
        return False
    token_scopes = getattr(request.state, "token_scopes", None)
    return token_scopes is None or has_required_scope(set(token_scopes), SCOPE_READ_HEALTH)


def _database_health_ok(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _redis_health_ok(settings) -> bool:
    try:
        client = redis.Redis.from_url(settings.redis_url)
        return bool(client.ping())
    except Exception:
        return False


def _worker_health_snapshot(settings) -> tuple[bool, dict[str, str], dict[str, object]]:
    worker_ok = False
    workers: dict[str, str] = {}
    queue_snapshot: dict[str, object] = {
        "required": _required_worker_queues(settings),
        "covered": [],
        "missing": _required_worker_queues(settings),
        "by_worker": {},
    }
    try:
        inspector = celery_app.control.inspect(timeout=settings.health_worker_ping_timeout_seconds)
        raw_ping = inspector.ping() or {}
        for worker_name, response in raw_ping.items():
            pong_value = response.get("ok") if isinstance(response, dict) else None
            workers[worker_name] = str(pong_value or "unknown")
        raw_queues = inspector.active_queues() or {}
        covered_queues: set[str] = set()
        queues_by_worker: dict[str, list[str]] = {}
        for worker_name, queues in raw_queues.items():
            worker_queue_names = sorted(
                queue.get("name")
                for queue in queues
                if isinstance(queue, dict) and isinstance(queue.get("name"), str)
            )
            queues_by_worker[worker_name] = worker_queue_names
            covered_queues.update(worker_queue_names)
        required_queues = set(_required_worker_queues(settings))
        missing_queues = sorted(required_queues - covered_queues)
        queue_snapshot = {
            "required": sorted(required_queues),
            "covered": sorted(covered_queues),
            "missing": missing_queues,
            "by_worker": queues_by_worker,
        }
        worker_ok = bool(raw_ping) and not missing_queues
    except Exception:
        worker_ok = False
    return worker_ok, workers, queue_snapshot


def _required_worker_queues(settings) -> list[str]:
    queues = [QUEUE_INGEST, QUEUE_PROCESSING, QUEUE_NOTIFICATIONS, QUEUE_MAINTENANCE]
    if settings.ai_enabled:
        queues.append(QUEUE_AI)
    return queues


def _beat_health_snapshot(settings) -> tuple[bool, str | None, int | None]:
    now = datetime.now(timezone.utc)
    heartbeat_raw: str | None = None
    age_seconds: int | None = None

    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        heartbeat_raw = client.get(settings.beat_heartbeat_key)
    except Exception:
        return False, None, None

    if not heartbeat_raw:
        return False, None, None

    try:
        heartbeat_at = datetime.fromisoformat(heartbeat_raw)
    except ValueError:
        return False, heartbeat_raw, None

    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
    age_seconds = max(0, int((now - heartbeat_at).total_seconds()))
    return age_seconds <= settings.beat_heartbeat_stale_after_seconds, heartbeat_raw, age_seconds
