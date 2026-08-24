from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate
from app.services.report_idempotency import (
    ReportIdempotencyConflictError,
    ReportIdempotencyError,
    ReportRequestIdentity,
    build_report_create_identity,
    build_report_operation_identity,
    build_report_retry_identity,
    build_report_schedule_run_identity,
    find_report_create_replay,
    find_report_operation_replay,
    find_report_retry_replay,
    find_report_schedule_run_replay,
    record_report_operation_receipt,
)


OperationResource = TypeVar("OperationResource", ReportTemplate, ReportSchedule)
Params = ParamSpec("Params")
Result = TypeVar("Result")


def _translate_idempotency_errors(
    function: Callable[Params, Result],
) -> Callable[Params, Result]:
    @wraps(function)
    def translated(*args: Params.args, **kwargs: Params.kwargs) -> Result:
        try:
            return function(*args, **kwargs)
        except ReportIdempotencyError as exc:
            status_code = (
                status.HTTP_409_CONFLICT
                if isinstance(exc, ReportIdempotencyConflictError)
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return translated


create_request_identity = _translate_idempotency_errors(build_report_create_identity)
retry_request_identity = _translate_idempotency_errors(build_report_retry_identity)
schedule_run_request_identity = _translate_idempotency_errors(
    build_report_schedule_run_identity
)
operation_request_identity = _translate_idempotency_errors(
    build_report_operation_identity
)
_find_operation_replay = _translate_idempotency_errors(find_report_operation_replay)
find_create_replay = _translate_idempotency_errors(find_report_create_replay)
find_retry_replay = _translate_idempotency_errors(find_report_retry_replay)
find_schedule_run_replay = _translate_idempotency_errors(
    find_report_schedule_run_replay
)


def find_operation_resource(
    db: Session,
    *,
    user_id: uuid.UUID,
    operation: str,
    resource_type: str,
    identity: ReportRequestIdentity | None,
    model: type[OperationResource],
    missing_detail: str,
) -> OperationResource | None:
    receipt = _find_operation_replay(
        db,
        user_id=user_id,
        operation=operation,
        resource_type=resource_type,
        identity=identity,
    )
    if receipt is None:
        return None
    resource = db.get(model, receipt.resource_id)
    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=missing_detail,
        )
    return resource


def commit_operation_resource(
    db: Session,
    *,
    resource: OperationResource,
    user_id: uuid.UUID,
    operation: str,
    resource_type: str,
    identity: ReportRequestIdentity | None,
    model: type[OperationResource],
    missing_detail: str,
) -> OperationResource:
    record_report_operation_receipt(
        db,
        user_id=user_id,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource.id,
        identity=identity,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = find_operation_resource(
            db,
            user_id=user_id,
            operation=operation,
            resource_type=resource_type,
            identity=identity,
            model=model,
            missing_detail=missing_detail,
        )
        if replay is not None:
            return replay
        raise
    db.refresh(resource)
    return resource
