import redis
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db

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
