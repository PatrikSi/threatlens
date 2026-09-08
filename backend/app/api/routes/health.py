from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_optional_current_user, require_token_scopes
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.core.token_scopes import SCOPE_READ_HEALTH, has_required_scope
from app.db.session import get_db
from app.models.user import User
from app.schemas.health import EncryptedDataInventoryResponse
from app.services import component_health
from app.services.encrypted_data_inventory import scan_encrypted_data_inventory
from app.services.notification_webhooks import get_notification_delivery_queue_snapshot

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
    status_code = (
        status.HTTP_200_OK if snapshot.ok else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=status_code, content=snapshot.model_dump())


@router.get("/encrypted-data", response_model=EncryptedDataInventoryResponse)
def encrypted_data(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_HEALTH)),
):
    snapshot = scan_encrypted_data_inventory(db)
    status_code = (
        status.HTTP_200_OK if snapshot.ok else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(
        status_code=status_code, content=snapshot.model_dump(mode="json")
    )


def _readiness_response(db: Session, *, detailed: bool):
    settings = get_settings()
    db_ok = component_health.database_health_ok(db)
    redis_ok = component_health.redis_health_ok(settings)
    worker_ok, _workers, _worker_queues = component_health.worker_health_snapshot(
        settings
    )
    beat_snapshot = component_health.beat_health_snapshot(settings)

    ok = db_ok and redis_ok and worker_ok and beat_snapshot.readiness_ok
    status_code = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE
    payload = {"ok": ok}
    if detailed:
        payload.update(
            {
                "db": db_ok,
                "redis": redis_ok,
                "worker": worker_ok,
                "beat": beat_snapshot.readiness_ok,
            }
        )
    return JSONResponse(
        status_code=status_code,
        content=payload,
    )


def _worker_health_response(*, detailed: bool):
    settings = get_settings()
    worker_ok, workers, queue_snapshot = component_health.worker_health_snapshot(
        settings
    )

    status_code = (
        status.HTTP_200_OK if worker_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    payload = {"ok": worker_ok}
    if detailed:
        payload["workers"] = workers
        payload["queues"] = queue_snapshot
    return JSONResponse(status_code=status_code, content=payload)


def _beat_health_response(*, detailed: bool):
    settings = get_settings()
    snapshot = component_health.beat_health_snapshot(settings)

    status_code = (
        status.HTTP_200_OK
        if snapshot.readiness_ok
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    payload = {"ok": snapshot.readiness_ok}
    if detailed:
        payload.update(
            {
                "heartbeat_key": settings.beat_heartbeat_key,
                "heartbeat_at": snapshot.worker_round_trip.heartbeat_at,
                "age_seconds": snapshot.worker_round_trip.age_seconds,
                "reason": snapshot.readiness_reason,
                "round_trip_reason": snapshot.worker_round_trip.reason,
                "stale_after_seconds": settings.beat_heartbeat_stale_after_seconds,
                "scheduler_heartbeat_key": settings.beat_scheduler_heartbeat_key,
                "scheduler_heartbeat_at": snapshot.scheduler.heartbeat_at,
                "scheduler_age_seconds": snapshot.scheduler.age_seconds,
                "scheduler_reason": snapshot.scheduler.reason,
            }
        )
    return JSONResponse(status_code=status_code, content=payload)


def _can_view_detailed_health(request: Request, user: User | None) -> bool:
    if user is None or user.role != ROLE_ADMIN:
        return False
    token_scopes = getattr(request.state, "token_scopes", None)
    return token_scopes is None or has_required_scope(
        set(token_scopes), SCOPE_READ_HEALTH
    )
