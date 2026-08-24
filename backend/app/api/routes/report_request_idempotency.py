from __future__ import annotations

import uuid
from typing import NoReturn, TypeVar

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate
from app.schemas.reports import ReportCreateRequest
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


def create_request_identity(
    key: str | None,
    *,
    payload: ReportCreateRequest,
) -> ReportRequestIdentity | None:
    try:
        return build_report_create_identity(key, payload=payload)
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)


def retry_request_identity(
    key: str | None,
    *,
    report_id: uuid.UUID,
) -> ReportRequestIdentity | None:
    try:
        return build_report_retry_identity(key, report_id=report_id)
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)


def schedule_run_request_identity(
    key: str | None,
    *,
    schedule_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> ReportRequestIdentity | None:
    try:
        return build_report_schedule_run_identity(
            key,
            schedule_id=schedule_id,
            actor_user_id=actor_user_id,
        )
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)


def operation_request_identity(
    key: str | None,
    *,
    operation: str,
    payload: object,
) -> ReportRequestIdentity | None:
    try:
        return build_report_operation_identity(
            key,
            operation=operation,
            payload=payload,
        )
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)


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
    try:
        receipt = find_report_operation_replay(
            db,
            user_id=user_id,
            operation=operation,
            resource_type=resource_type,
            identity=identity,
        )
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)
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


def find_create_replay(
    db: Session,
    *,
    user_id: uuid.UUID,
    identity: ReportRequestIdentity | None,
) -> tuple[Report, AITaskRun] | None:
    try:
        return find_report_create_replay(
            db,
            user_id=user_id,
            identity=identity,
        )
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)


def find_retry_replay(
    db: Session,
    *,
    user_id: uuid.UUID,
    report_id: uuid.UUID,
    identity: ReportRequestIdentity | None,
) -> AITaskRun | None:
    try:
        return find_report_retry_replay(
            db,
            user_id=user_id,
            report_id=report_id,
            identity=identity,
        )
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)


def find_schedule_run_replay(
    db: Session,
    *,
    user_id: uuid.UUID,
    schedule_id: uuid.UUID,
    identity: ReportRequestIdentity | None,
) -> tuple[Report, AITaskRun | None] | None:
    try:
        return find_report_schedule_run_replay(
            db,
            user_id=user_id,
            schedule_id=schedule_id,
            identity=identity,
        )
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)


def _raise_idempotency_http_error(exc: ReportIdempotencyError) -> NoReturn:
    status_code = (
        status.HTTP_409_CONFLICT
        if isinstance(exc, ReportIdempotencyConflictError)
        else status.HTTP_400_BAD_REQUEST
    )
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc
