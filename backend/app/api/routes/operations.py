from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import require_permissions
from app.core.token_scopes import SCOPE_READ_OPERATIONS
from app.db.session import get_db
from app.models.user import User
from app.schemas.operations import (
    HealthHistoryWindow,
    OperationsHealthHistoryResponse,
    OperationsDiagnosticsResponse,
    OperationsOverviewResponse,
    OperationsWorkerTopologyResponse,
    SystemOperationRunListResponse,
    SystemOperationStatus,
    SystemOperationType,
)
from app.services.operations import (
    collect_operations_diagnostics,
    collect_operations_overview,
    list_system_operation_runs,
)
from app.services.operations_health_history import collect_health_history
from app.services.worker_health import collect_worker_topology


router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/overview", response_model=OperationsOverviewResponse)
def overview(
    response: Response,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_OPERATIONS)),
):
    response.headers["Cache-Control"] = "no-store"
    return collect_operations_overview(db)


@router.get("/workers", response_model=OperationsWorkerTopologyResponse)
def workers(
    response: Response,
    _reader: User = Depends(require_permissions(SCOPE_READ_OPERATIONS)),
):
    response.headers["Cache-Control"] = "no-store"
    return collect_worker_topology()


@router.get("/health-history", response_model=OperationsHealthHistoryResponse)
def health_history(
    response: Response,
    window: HealthHistoryWindow = Query(default="24h"),
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_OPERATIONS)),
):
    response.headers["Cache-Control"] = "no-store"
    return collect_health_history(db, window=window)


@router.get("/runs", response_model=SystemOperationRunListResponse)
def runs(
    response: Response,
    operation_type: SystemOperationType | None = None,
    status: SystemOperationStatus | None = None,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_OPERATIONS)),
):
    response.headers["Cache-Control"] = "no-store"
    return list_system_operation_runs(
        db,
        page=page,
        page_size=page_size,
        operation_type=operation_type,
        status=status,
    )


@router.get("/diagnostics", response_model=OperationsDiagnosticsResponse)
def diagnostics(
    response: Response,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_OPERATIONS)),
):
    response.headers["Cache-Control"] = "no-store"
    return collect_operations_diagnostics(db)
