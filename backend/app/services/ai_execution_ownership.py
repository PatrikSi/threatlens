"""Delivery ownership for worker continuations, separate from provider receipts.

The scope is installed at worker entry and reset on every exit. It records the
accepted delivery for runs claimed by that invocation; system reconciliation and
HTTP cancellation deliberately run outside that worker scope.
"""

from __future__ import annotations

import inspect
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, Literal, ParamSpec, TypeVar, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun

P = ParamSpec("P")
R = TypeVar("R")


class AIExecutionSuperseded(RuntimeError):
    """An old worker continuation may no longer change its logical run."""

    def __init__(self, message: str, *, reason: str = "superseded_delivery") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class _Execution:
    delivery_id: str
    runs: set[uuid.UUID] = field(default_factory=set)


_execution: ContextVar[_Execution | None] = ContextVar(
    "ai_worker_execution", default=None
)


def ai_worker_execution(function: Callable[P, R]) -> Callable[P, R]:
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        arguments = signature.bind(*args, **kwargs).arguments
        task = arguments.get("task", arguments.get("self"))
        delivery_id = getattr(getattr(task, "request", None), "id", None)
        scope = _Execution(delivery_id) if delivery_id else None
        if scope is not None:
            try:
                scope.runs.add(uuid.UUID(str(arguments.get("task_run_id"))))
            except (TypeError, ValueError):
                pass
        token = _execution.set(scope)
        try:
            return function(*args, **kwargs)
        except AIExecutionSuperseded as exc:
            return cast(R, {"status": "skipped", "reason": exc.reason})
        finally:
            _execution.reset(token)

    return wrapped


def track_ai_execution(run_id: uuid.UUID) -> None:
    scope = _execution.get()
    if scope is not None:
        scope.runs.add(run_id)


def ai_execution_is_current(run: AITaskRun, *, allow_unassigned: bool = False) -> bool:
    """Call while holding the target run lock before a worker-owned mutation."""
    scope = _execution.get()
    if scope is None or run.id not in scope.runs:
        return True
    return run.celery_task_id == scope.delivery_id or (
        allow_unassigned and run.celery_task_id is None
    )


def fence_ai_execution(db: Session, *, run_id: uuid.UUID) -> bool:
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return run is not None and ai_execution_is_current(run)


def require_ai_execution(run: AITaskRun, *, allow_unassigned: bool = False) -> None:
    if not ai_execution_is_current(run, allow_unassigned=allow_unassigned):
        raise AIExecutionSuperseded("AI worker delivery has been superseded.")


def ai_execution_stop_reason(
    db: Session, run: AITaskRun, *, lock_parent: bool | Literal["read"] = False
) -> str | None:
    if not ai_execution_is_current(run, allow_unassigned=True):
        return "superseded_delivery"
    if run.parent_run_id is None:
        return None
    statement = select(AITaskRun).where(AITaskRun.id == run.parent_run_id)
    if lock_parent:
        statement = statement.with_for_update(read=lock_parent == "read")
    parent = db.scalar(statement.execution_options(populate_existing=True))
    if parent is None:
        return None
    if not ai_execution_is_current(parent):
        return "superseded_delivery"
    if (parent.metadata_json or {}).get("cancel_requested_at") or parent.reason in {
        "canceled",
        "cancel_requested",
    }:
        return "canceled"
    return None
