from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas.operations import OperationsDiagnosticsResponse, OperationsOverviewResponse
from app.services.operations_common import as_utc, ordered_issues, overall_status
from app.services.operations_probes import (
    collect_application_info,
    collect_component_checks,
    collect_storage_indicators,
)
from app.services.operations_projections import (
    collect_backlog_snapshots,
    collect_recovery_snapshot,
)
from app.services.operations_redaction import (
    MAX_ERROR_MESSAGE_CHARS,
    MAX_METADATA_DEPTH,
    MAX_METADATA_ENTRIES,
    MAX_METADATA_NODES,
    MAX_METADATA_STRING_CHARS,
    sanitize_operation_metadata,
)
from app.services.operations_runs import (
    create_system_operation_run,
    finish_system_operation_run,
    list_system_operation_runs,
    system_operation_run_response,
)


DIAGNOSTIC_RUN_LIMIT = 25


def collect_operations_overview(
    db: Session,
    *,
    now: datetime | None = None,
) -> OperationsOverviewResponse:
    generated_at = as_utc(now or datetime.now(timezone.utc))
    settings = get_settings()
    issues = []
    database_ok, components = collect_component_checks(
        db,
        settings=settings,
        checked_at=generated_at,
        issues=issues,
    )
    application = collect_application_info(
        db,
        database_ok=database_ok,
        issues=issues,
    )
    storage = collect_storage_indicators(
        db,
        issues=issues,
        database_ok=database_ok,
    )
    backlogs = collect_backlog_snapshots(
        db,
        settings=settings,
        now=generated_at,
        issues=issues,
        database_ok=database_ok,
    )
    recovery = collect_recovery_snapshot(
        db,
        issues=issues,
        database_ok=database_ok,
    )
    issues = ordered_issues(issues)
    return OperationsOverviewResponse(
        generated_at=generated_at,
        overall_status=overall_status(components, storage, backlogs, issues),
        application=application,
        components=components,
        storage=storage,
        backlogs=backlogs,
        recovery=recovery,
        issues=issues,
    )


def collect_operations_diagnostics(db: Session) -> OperationsDiagnosticsResponse:
    overview = collect_operations_overview(db)
    recent = list_system_operation_runs(db, page=1, page_size=DIAGNOSTIC_RUN_LIMIT)
    return OperationsDiagnosticsResponse(
        generated_at=overview.generated_at,
        overview=overview,
        recent_runs=recent.runs,
        recent_runs_truncated=recent.total > DIAGNOSTIC_RUN_LIMIT,
    )


__all__ = [
    "DIAGNOSTIC_RUN_LIMIT",
    "MAX_ERROR_MESSAGE_CHARS",
    "MAX_METADATA_DEPTH",
    "MAX_METADATA_ENTRIES",
    "MAX_METADATA_NODES",
    "MAX_METADATA_STRING_CHARS",
    "collect_operations_diagnostics",
    "collect_operations_overview",
    "create_system_operation_run",
    "finish_system_operation_run",
    "list_system_operation_runs",
    "sanitize_operation_metadata",
    "system_operation_run_response",
]
