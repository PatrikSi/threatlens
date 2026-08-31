from __future__ import annotations

import uuid
from hashlib import sha256

from fastapi import HTTPException, status

from app.core.config import get_settings


def require_ai_enabled() -> None:
    if not get_settings().ai_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI features are disabled",
        )


def hash_prompt(value: str | None) -> str | None:
    if value is None:
        return None
    return sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def effective_reprocess_limit(limit: int) -> int:
    settings = get_settings()
    return max(1, min(int(limit), int(settings.dispatch_ai_reprocess_batch_size)))


def celery_task_id(task: object) -> str | None:
    task_id = getattr(task, "id", None)
    return str(task_id) if task_id else None


def queue_response_task_id(task: object, run_id: uuid.UUID) -> str:
    return celery_task_id(task) or str(run_id)


__all__ = [
    "celery_task_id",
    "effective_reprocess_limit",
    "hash_prompt",
    "queue_response_task_id",
    "require_ai_enabled",
]
