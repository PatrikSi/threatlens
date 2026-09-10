"""Deadlines for bounded database-only operations on PostgreSQL 16.

Each SQL statement in one transaction receives the remaining allowance. This caps
lock waits across a sequence of statements. Callers must separately bound CPU
and external I/O; never wrap an authorization-fenced transfer in this context.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.runtime_metrics import record_runtime_event


logger = logging.getLogger(__name__)
DatabaseOperation = Literal["interactive", "repair", "lifecycle"]


class DatabaseDeadlineExceeded(TimeoutError):
    """The caller must roll back this operation and retry from durable state."""


@contextmanager
def database_operation(
    db: Session,
    *,
    operation: DatabaseOperation,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    settings = get_settings()
    allowance = (
        settings.database_operation_timeout_seconds
        if timeout_seconds is None
        else timeout_seconds
    )
    if not math.isfinite(allowance) or allowance <= 0:
        raise ValueError("Database operation timeout must be positive and finite")
    deadline = time.monotonic() + allowance
    connection = db.connection()
    transaction = db.get_transaction()
    postgres = connection.dialect.name == "postgresql"
    previous = None
    if postgres:
        previous = connection.exec_driver_sql(
            "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"
        ).one()

    def remaining_ms() -> int:
        remaining = math.floor((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise DatabaseDeadlineExceeded("Database operation deadline exceeded")
        return remaining

    def before_statement(_connection, cursor, _statement, _parameters, _context, _many):
        remaining = remaining_ms()
        if postgres:
            statement_limit = min(
                remaining, settings.database_statement_timeout_ms or remaining
            )
            lock_limit = min(remaining, settings.database_lock_timeout_ms)
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true), "
                "set_config('lock_timeout', %s, true)",
                (str(statement_limit), str(lock_limit)),
            )

    def before_commit(_session):
        remaining_ms()
        db.flush()
        if postgres:
            # PostgreSQL fires deferred constraints during transaction finish,
            # outside the ordinary COMMIT statement timer. Evaluate them as an
            # explicit timed statement after all ORM changes have been flushed.
            connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
        remaining_ms()

    def after_transaction_create(_session, created):
        if created.parent is None and created is not transaction:
            raise RuntimeError("Use a separate database_operation for each transaction")

    event.listen(connection, "before_cursor_execute", before_statement)
    event.listen(db, "before_commit", before_commit)
    event.listen(db, "after_transaction_create", after_transaction_create)
    listeners_attached = True

    def detach() -> None:
        nonlocal listeners_attached
        if listeners_attached:
            event.remove(connection, "before_cursor_execute", before_statement)
            event.remove(db, "before_commit", before_commit)
            event.remove(db, "after_transaction_create", after_transaction_create)
            listeners_attached = False

    try:
        yield
        # Never report an acknowledged commit as failed merely because Python
        # resumed after the deadline. A subsequent transaction needs a new scope.
        if db.in_transaction():
            remaining_ms()
    except (DatabaseDeadlineExceeded, OperationalError) as exc:
        logger.warning(
            "database_operation_failed operation=%s error_type=%s sqlstate=%s",
            operation,
            type(exc).__name__,
            getattr(getattr(exc, "orig", None), "sqlstate", None),
        )
        # Rollback (including SQLAlchemy savepoint rollback) must remain usable
        # after the allowance is exhausted or PostgreSQL aborts the transaction.
        detach()
        db.rollback()
        if isinstance(exc, DatabaseDeadlineExceeded):
            record_runtime_event("database_deadline")
        raise
    except BaseException:
        detach()
        db.rollback()
        raise
    finally:
        detach()
        # A committed/aborted transaction already cleared SET LOCAL. Preserve
        # callers' settings when a successful scope leaves its transaction open.
        if previous is not None and not connection.closed and connection.in_transaction():
            connection.exec_driver_sql(
                "SELECT set_config('statement_timeout', %s, true), "
                "set_config('lock_timeout', %s, true)",
                tuple(previous),
            )
