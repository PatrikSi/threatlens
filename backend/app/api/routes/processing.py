"""Scoped work discovery and resumable, principal-owned processing recovery."""

from contextlib import contextmanager
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import (
    AuthenticatedPrincipal,
    get_authorization_context,
    get_data_access_context,
    require_permissions,
)
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.token_scopes import (
    SCOPE_READ_ITEMS,
    SCOPE_READ_OPERATIONS,
    SCOPE_WRITE_OPERATIONS,
)
from app.db.budgets import database_operation
from app.db.session import get_db
from app.schemas.processing import (
    ProcessingRecoveryCancel,
    ProcessingRecoveryList,
    ProcessingRecoveryRequest,
    ProcessingRecoveryResponse,
    ProcessingStage,
    ProcessingState,
    ProcessingWorkList,
)
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationStateUnavailable,
    fence_authorization_context,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyError,
    fence_data_access_context,
)
from app.services.export_job_access import ExportJobAccessDenied
from app.services.export_principals import export_principal_type, export_user_id
from app.services.processing_queries import (
    ProcessingCapacity,
    ProcessingConflict,
    encode_cursor,
    list_processing_work,
)
from app.services.processing_recovery import (
    accept_recovery,
    cancel_recovery,
    list_owned_runs,
    owned_run,
    recovery_response,
)

router = APIRouter(prefix="/processing", tags=["processing"])


@contextmanager
def _operation(db: Session):
    try:
        with database_operation(db, operation="interactive"):
            yield
    except ProcessingConflict as exc:
        raise ApiHTTPException(
            status_code=409, error_code="processing_conflict", detail=str(exc)
        ) from exc
    except ProcessingCapacity as exc:
        raise ApiHTTPException(
            status_code=429,
            error_code="processing_capacity",
            detail=str(exc),
            headers={"Retry-After": "60"},
        ) from exc
    except (
        ExportJobAccessDenied,
        AuthorizationStateUnavailable,
        DataPolicyError,
    ) as exc:
        raise ApiHTTPException(
            status_code=409,
            error_code="processing_authorization_changed",
            detail="Processing access changed. Refresh with your current access before continuing.",
        ) from exc


def _response(db, run, request, access):
    authorization = get_authorization_context(request)
    return recovery_response(
        db,
        run,
        authorization=authorization,
        access=access,
        can_cancel=authorization.has(SCOPE_WRITE_OPERATIONS),
    )


def _retention_header(response: Response):
    response.headers["X-Processing-Recovery-Retention-Seconds"] = str(
        get_settings().processing_recovery_retention_seconds
    )


def _audit(db, principal, run, action):
    record_audit(
        db,
        actor_user_id=export_user_id(principal),
        actor_principal_type=export_principal_type(principal),
        actor_principal_id=principal.id,
        action=action,
        resource_type="processing_recovery",
        resource_id=str(run.id),
        metadata={"selected_stages": run.total_count},
    )


@router.get("/work", response_model=ProcessingWorkList)
def get_processing_work(
    request: Request,
    stage: ProcessingStage | None = None,
    state: ProcessingState | None = None,
    feed_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(
        require_permissions(SCOPE_READ_OPERATIONS, SCOPE_READ_ITEMS)
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> ProcessingWorkList:
    with _operation(db):
        authorization = get_authorization_context(request)
        fence_authorization_context(db, authorization)
        fence_data_access_context(db, access)
        return list_processing_work(
            db,
            access,
            can_retry=authorization.has(SCOPE_WRITE_OPERATIONS),
            stage=stage,
            state=state,
            feed_id=feed_id,
            limit=limit,
            cursor=cursor,
        )


@router.post(
    "/recovery-runs", response_model=ProcessingRecoveryResponse, status_code=202
)
def post_processing_recovery(
    payload: ProcessingRecoveryRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(
        require_permissions(SCOPE_WRITE_OPERATIONS, SCOPE_READ_ITEMS)
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> ProcessingRecoveryResponse:
    with _operation(db):
        run, created = accept_recovery(
            db,
            principal=principal,
            request=request,
            authorization=get_authorization_context(request),
            access=access,
            payload=payload,
        )
        if created:
            _audit(db, principal, run, "processing.recovery.accept")
        identity = run.id
        db.commit()
    # Durable admission does not depend on a live broker. Beat publishes claims.
    with _operation(db):
        run = owned_run(db, identity, principal)
        result = _response(db, run, request, access)
    _retention_header(response)
    return result


@router.get("/recovery-runs", response_model=ProcessingRecoveryList)
def get_processing_recoveries(
    request: Request,
    response: Response,
    limit: int = Query(25, ge=1, le=25),
    cursor: str | None = None,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(
        require_permissions(SCOPE_READ_OPERATIONS, SCOPE_READ_ITEMS)
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> ProcessingRecoveryList:
    with _operation(db):
        found = list_owned_runs(db, principal, limit=limit, cursor=cursor)
        last = found[limit - 1] if len(found) > limit else None
        result = ProcessingRecoveryList(
            items=[_response(db, run, request, access) for run in found[:limit]],
            has_more=last is not None,
            next_cursor=encode_cursor([last.created_at.isoformat(), str(last.id)])
            if last
            else None,
        )
    _retention_header(response)
    return result


@router.get("/recovery-runs/{run_id}", response_model=ProcessingRecoveryResponse)
def get_processing_recovery(
    run_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(
        require_permissions(SCOPE_READ_OPERATIONS, SCOPE_READ_ITEMS)
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> ProcessingRecoveryResponse:
    with _operation(db):
        run = owned_run(db, run_id, principal)
        if run is None:
            raise HTTPException(status_code=404, detail="Processing recovery not found")
        result = _response(db, run, request, access)
    _retention_header(response)
    return result


@router.post(
    "/recovery-runs/{run_id}/cancel", response_model=ProcessingRecoveryResponse
)
def post_processing_recovery_cancel(
    run_id: uuid.UUID,
    payload: ProcessingRecoveryCancel,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(
        require_permissions(SCOPE_WRITE_OPERATIONS)
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> ProcessingRecoveryResponse:
    with _operation(db):
        fence_authorization_context(db, get_authorization_context(request))
        run = owned_run(db, run_id, principal, lock=True)
        if run is None:
            raise HTTPException(status_code=404, detail="Processing recovery not found")
        if cancel_recovery(db, run, expected_version=payload.expected_version):
            _audit(db, principal, run, "processing.recovery.cancel")
        db.commit()
    with _operation(db):
        result = _response(db, owned_run(db, run_id, principal), request, access)
    _retention_header(response)
    return result
