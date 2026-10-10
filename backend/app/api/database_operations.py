"""Consistent bounded transactions for JSON control-plane operations."""

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.db.budgets import DatabaseDeadlineExceeded, database_operation

Result = TypeVar("Result")


def interactive_request(
    db: Session,
    call: Callable[[], Result],
    *,
    commit: bool = True,
    error_code: str = "operation_busy",
    busy_detail: str = "This operation is waiting for a concurrent update. Retry the same request.",
) -> Result:
    """Never use for external I/O or response transfers; callers own those bounds."""
    try:
        with database_operation(db, operation="interactive"):
            result = call()
            if commit:
                db.commit()
        return result
    except DatabaseDeadlineExceeded as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=503,
            error_code=error_code,
            detail=busy_detail,
            headers={"Retry-After": "1"},
        ) from exc
    except OperationalError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) in {"55P03", "57014", "40P01"}:
            raise ApiHTTPException(
                status_code=503,
                error_code=error_code,
                detail=busy_detail,
                headers={"Retry-After": "1"},
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise
