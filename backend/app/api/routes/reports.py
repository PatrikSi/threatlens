from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfoNotFoundError

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from pydantic import ValidationError
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_data_access_context,
    require_permission_roles,
    require_permissions,
)
from app.api.resource_preconditions import (
    next_resource_version,
    resource_version_tag,
)
from app.api.routes.report_route_helpers import (
    active_reporting_settings as _active_reporting_settings,
    get_accessible_report as _get_accessible_report,
    integrity_constraint_name as _integrity_constraint_name,
    queue_response as _queue_response,
    require_current_resource_version as _require_current_resource_version,
    require_report_owner_or_admin as _require_report_owner_or_admin,
    require_shared_template_admin as _require_shared_template_admin,
    require_template_owner_or_admin as _require_template_owner_or_admin,
)
from app.api.routes.report_request_idempotency import (
    commit_operation_resource,
    create_request_identity,
    find_create_replay,
    find_operation_resource,
    find_retry_replay,
    find_schedule_run_replay,
    operation_request_identity,
    retry_request_identity,
    schedule_run_request_identity,
)
from app.core.rbac import ROLE_ADMIN
from app.core.token_scopes import SCOPE_READ_REPORTS, SCOPE_WRITE_REPORTS
from app.db.session import get_db
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.report import Report
from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate
from app.models.tag import ItemTag, Tag
from app.models.user import User
from app.schemas.exports import ExportOptionEntry
from app.schemas.reports import (
    ReportCapabilitiesResponse,
    ReportCreateRequest,
    ReportDetailResponse,
    ReportListItem,
    ReportPreviewRequest,
    ReportPreviewResponse,
    ReportQueueResponse,
    ReportScheduleCreate,
    ReportScheduleResponse,
    ReportScheduleUpdate,
    ReportTemplateCreate,
    ReportTemplateResponse,
    ReportTemplateUpdate,
)
from app.services.ai_config import load_active_ai_settings
from app.services.ai_context_budget import AIContextBudgetError
from app.services.audit import record_audit
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_REPORT,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import (
    DataAccessContext,
    handling_label_access_predicate,
)
from app.services.export_query import (
    ExportAuthorizationChangedError,
    ExportSnapshotChangedError,
)
from app.services.report_schedules import (
    apply_schedule_payload,
    create_report_schedule,
    report_schedule_response,
    reserve_schedule_runs,
)
from app.services.report_rendering import (
    render_report_html,
    render_report_markdown,
    render_report_pdf,
)
from app.schemas.reports import ReportSectionSetError
from app.services.report_sources import (
    build_report_source_plan,
    filters_for_report_period,
    report_preview_from_plan,
)
from app.services.report_storage import (
    ReportStorageError,
    create_report_from_plan,
    delete_report,
    report_detail_response,
    report_list_item,
    reset_report_for_retry,
)
from app.services.report_templates import (
    ReportTemplateError,
    clone_report_template,
    create_report_template,
    delete_report_template,
    get_visible_report_template,
    list_visible_report_templates,
    report_template_response,
    update_report_template,
)
from app.services.report_task_lineage import ReportTaskLineageError
from app.tasks.report_tasks import create_report_task_run, enqueue_report_task


router = APIRouter(prefix="/reports", tags=["reports"])
require_report_write = require_permissions(
    SCOPE_WRITE_REPORTS,
    denial_detail="Report generation requires the analyst or administrator role.",
)
_REPORT_ADMIN_DETAIL = (
    "Report schedules and shared templates require the administrator role."
)
require_report_admin_read = require_permission_roles(
    SCOPE_READ_REPORTS,
    roles=(ROLE_ADMIN,),
    detail=_REPORT_ADMIN_DETAIL,
)
require_report_admin_write = require_permission_roles(
    SCOPE_WRITE_REPORTS,
    roles=(ROLE_ADMIN,),
    detail=_REPORT_ADMIN_DETAIL,
)
REPORT_PREVIEW_LIMIT = 25
RESOURCE_PRECONDITION_RESPONSES = {
    status.HTTP_400_BAD_REQUEST: {"description": "Malformed If-Match header"},
    status.HTTP_412_PRECONDITION_FAILED: {
        "description": "Resource version no longer matches"
    },
}
logger = logging.getLogger(__name__)


@router.get("/capabilities", response_model=ReportCapabilitiesResponse)
def get_report_capabilities(
    db: Session = Depends(get_db),
    _user: User = Depends(require_permissions(SCOPE_READ_REPORTS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    active = load_active_ai_settings(db)
    feed_access = handling_label_access_predicate(
        Feed.handling_label_id,
        data_access,
    )
    feeds = db.execute(
        select(Feed.id, Feed.name).where(feed_access).order_by(Feed.name.asc())
    ).all()
    tags = db.execute(
        select(Tag.id, Tag.name)
        .where(
            exists(
                select(1)
                .select_from(ItemTag)
                .join(Item, Item.id == ItemTag.item_id)
                .join(Feed, Feed.id == Item.feed_id)
                .where(ItemTag.tag_id == Tag.id, feed_access)
            )
        )
        .order_by(Tag.name.asc())
    ).all()
    classifications = db.scalars(
        select(func.lower(ItemClassification.primary_category))
        .join(Item, Item.id == ItemClassification.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(feed_access)
        .distinct()
        .order_by(func.lower(ItemClassification.primary_category))
    ).all()
    return ReportCapabilitiesResponse(
        reporting_enabled=active.ai_enabled and active.reporting_enabled,
        ai_configured=active.ai_configured,
        feeds=[ExportOptionEntry(id=row.id, name=row.name) for row in feeds],
        tags=[ExportOptionEntry(id=row.id, name=row.name) for row in tags],
        classifications=[value for value in classifications if value],
        max_sources=active.report_max_sources,
        preview_limit=REPORT_PREVIEW_LIMIT,
        context_window_tokens=active.report_context_window_tokens,
        reserved_output_tokens=active.report_reserved_output_tokens,
        source_token_cap=active.report_source_token_cap,
        max_model_calls=active.report_max_model_calls,
        safety_percent=active.report_context_safety_percent,
    )


@router.post("/preview", response_model=ReportPreviewResponse)
def preview_report(
    payload: ReportPreviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    active = _active_reporting_settings(db)
    started_at = time.monotonic()
    try:
        plan = build_report_source_plan(
            db,
            user_id=user.id,
            filters=payload.filters,
            excluded_item_ids=payload.excluded_item_ids,
            prompt=payload.prompt,
            sections=payload.sections,
            active=active,
            data_access=data_access,
        )
    except AIContextBudgetError as exc:
        logger.info(
            "report_preview_rejected user_id=%s reason=context_budget duration_ms=%d",
            user.id,
            round((time.monotonic() - started_at) * 1000),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ExportSnapshotChangedError as exc:
        logger.info(
            "report_preview_rejected user_id=%s reason=snapshot_changed duration_ms=%d",
            user.id,
            round((time.monotonic() - started_at) * 1000),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Matching articles changed while report context was being prepared. Refresh the estimate and try again.",
        ) from exc
    except ExportAuthorizationChangedError as exc:
        logger.info(
            "report_preview_rejected user_id=%s reason=authorization_changed duration_ms=%d",
            user.id,
            round((time.monotonic() - started_at) * 1000),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your data access changed while report context was being prepared. Refresh the estimate and try again.",
        ) from exc
    logger.info(
        "report_preview_planned user_id=%s total_matches=%d selected_sources=%d batches=%d duration_ms=%d",
        user.id,
        plan.total_matches,
        len(plan.included_sources),
        plan.batch_count,
        round((time.monotonic() - started_at) * 1000),
    )
    return report_preview_from_plan(plan, preview_limit=REPORT_PREVIEW_LIMIT)


@router.get("/templates", response_model=list[ReportTemplateResponse])
def list_report_templates(
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_REPORTS)),
):
    return [
        report_template_response(template)
        for template in list_visible_report_templates(db, user_id=user.id)
    ]


@router.post(
    "/templates",
    response_model=ReportTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_template(
    payload: ReportTemplateCreate,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
):
    _require_shared_template_admin(user, payload.visibility)
    operation = "report:template:create"
    identity = operation_request_identity(
        idempotency_key,
        operation=operation,
        payload=payload,
    )
    replay = find_operation_resource(
        db,
        user_id=user.id,
        operation=operation,
        resource_type="report_template",
        identity=identity,
        model=ReportTemplate,
        missing_detail=(
            "The report template created by this Idempotency-Key no longer exists. "
            "Use a new Idempotency-Key to create another template."
        ),
    )
    if replay is not None:
        return report_template_response(replay)
    template = create_report_template(db, user_id=user.id, payload=payload)
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.template.create",
        resource_type="report_template",
        resource_id=str(template.id),
        metadata={"visibility": template.visibility},
    )
    template = commit_operation_resource(
        db,
        resource=template,
        user_id=user.id,
        operation=operation,
        resource_type="report_template",
        identity=identity,
        model=ReportTemplate,
        missing_detail=(
            "The report template created by this Idempotency-Key no longer exists. "
            "Use a new Idempotency-Key to create another template."
        ),
    )
    return report_template_response(template)


@router.put(
    "/templates/{template_id}",
    response_model=ReportTemplateResponse,
    responses=RESOURCE_PRECONDITION_RESPONSES,
)
def update_template(
    template_id: uuid.UUID,
    payload: ReportTemplateUpdate,
    response: Response,
    if_match: Annotated[list[str] | None, Header(alias="If-Match")] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
):
    _require_shared_template_admin(user, payload.visibility)
    template = get_visible_report_template(
        db,
        template_id=template_id,
        user_id=user.id,
        for_update=True,
    )
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report template not found"
        )
    _require_template_owner_or_admin(user, template.owner_user_id)
    _require_current_resource_version(
        current_updated_at=template.updated_at,
        if_match=if_match,
        resource_label="report template",
    )
    try:
        update_report_template(template, payload=payload)
    except ReportTemplateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    template.updated_at = next_resource_version(template.updated_at)
    db.add(template)
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.template.update",
        resource_type="report_template",
        resource_id=str(template.id),
    )
    db.commit()
    db.refresh(template)
    response.headers["ETag"] = resource_version_tag(template.updated_at)
    return report_template_response(template)


@router.post(
    "/templates/{template_id}/clone",
    response_model=ReportTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def clone_template(
    template_id: uuid.UUID,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
):
    operation = f"report:template:clone:{template_id}"
    identity = operation_request_identity(
        idempotency_key,
        operation=operation,
        payload={"source_template_id": str(template_id), "version": 1},
    )
    replay = find_operation_resource(
        db,
        user_id=user.id,
        operation=operation,
        resource_type="report_template",
        identity=identity,
        model=ReportTemplate,
        missing_detail=(
            "The cloned report template created by this Idempotency-Key no longer "
            "exists. Use a new Idempotency-Key to clone the template again."
        ),
    )
    if replay is not None:
        return report_template_response(replay)
    template = get_visible_report_template(db, template_id=template_id, user_id=user.id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report template not found"
        )
    clone = clone_report_template(db, template=template, user_id=user.id)
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.template.clone",
        resource_type="report_template",
        resource_id=str(clone.id),
        metadata={"source_template_id": str(template.id)},
    )
    clone = commit_operation_resource(
        db,
        resource=clone,
        user_id=user.id,
        operation=operation,
        resource_type="report_template",
        identity=identity,
        model=ReportTemplate,
        missing_detail=(
            "The cloned report template created by this Idempotency-Key no longer "
            "exists. Use a new Idempotency-Key to clone the template again."
        ),
    )
    return report_template_response(clone)


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=RESOURCE_PRECONDITION_RESPONSES,
)
def remove_template(
    template_id: uuid.UUID,
    if_match: Annotated[list[str] | None, Header(alias="If-Match")] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
):
    template = get_visible_report_template(
        db,
        template_id=template_id,
        user_id=user.id,
        for_update=True,
    )
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report template not found"
        )
    _require_template_owner_or_admin(user, template.owner_user_id)
    _require_current_resource_version(
        current_updated_at=template.updated_at,
        if_match=if_match,
        resource_label="report template",
    )
    try:
        delete_report_template(db, template=template)
        db.flush()
    except ReportTemplateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Template is used by a report schedule and cannot be deleted.",
        ) from exc
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.template.delete",
        resource_type="report_template",
        resource_id=str(template_id),
    )
    db.commit()


@router.get("", response_model=list[ReportListItem])
def list_reports(
    report_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(require_permissions(SCOPE_READ_REPORTS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    query = select(Report).where(
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_REPORT,
            Report.id,
            data_access,
        )
    )
    if report_status:
        if report_status not in {"queued", "running", "ready", "error", "skipped"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid report status filter",
            )
        query = query.where(Report.status == report_status)
    reports = db.scalars(
        query.order_by(Report.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return [report_list_item(report) for report in reports]


@router.post(
    "", response_model=ReportQueueResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_report(
    payload: ReportCreateRequest,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    filters = filters_for_report_period(
        payload.filters,
        period_start=payload.period_start,
        period_end=payload.period_end,
    )
    payload = payload.model_copy(update={"filters": filters})
    identity = create_request_identity(idempotency_key, payload=payload)
    replay = find_create_replay(db, user_id=user.id, identity=identity)
    if replay is not None:
        return _queue_response(*replay)

    active = _active_reporting_settings(db)
    template = None
    if payload.template_id:
        template = get_visible_report_template(
            db, template_id=payload.template_id, user_id=user.id
        )
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report template not found",
            )
    try:
        plan = build_report_source_plan(
            db,
            user_id=user.id,
            filters=filters,
            excluded_item_ids=payload.excluded_item_ids,
            prompt=payload.prompt,
            sections=payload.sections,
            active=active,
            data_access=data_access,
        )
        report = create_report_from_plan(
            db,
            user_id=user.id,
            payload=payload,
            plan=plan,
            template=template,
            active=active,
            request_idempotency_key=(identity.legacy_key if identity else None),
            request_idempotency_key_hash=(identity.key_hash if identity else None),
            request_fingerprint=(identity.fingerprint if identity else None),
        )
        run = create_report_task_run(
            db,
            report=report,
            actor_user_id=user.id,
            trigger_source="manual",
            originating_request=True,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="reports.generate.queue",
            resource_type="report",
            resource_id=str(report.id),
            metadata={
                "source_count": report.included_source_count,
                "estimated_input_tokens": report.estimated_input_tokens,
                "estimated_batches": report.generation_batches,
            },
        )
        db.commit()
    except ExportSnapshotChangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Matching articles changed while the report was being prepared. Try generating it again.",
        ) from exc
    except ExportAuthorizationChangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your data access changed while the report was being prepared. Try generating it again.",
        ) from exc
    except (AIContextBudgetError, ReportStorageError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except IntegrityError:
        db.rollback()
        replay = find_create_replay(db, user_id=user.id, identity=identity)
        if replay is not None:
            return _queue_response(*replay)
        raise
    report_id = report.id
    run_id = run.id
    task_id = enqueue_report_task(report_id=report_id, task_run_id=run_id)
    return _queue_response(report, run, celery_task_id=task_id)


@router.get("/{report_id:uuid}", response_model=ReportDetailResponse)
def get_report(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permissions(SCOPE_READ_REPORTS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    report = _get_accessible_report(
        db,
        report_id=report_id,
        data_access=data_access,
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    return report_detail_response(db, report=report)


@router.get("/{report_id:uuid}/download")
def download_report(
    report_id: uuid.UUID,
    format: str = Query(default="markdown", pattern="^(markdown|html|pdf)$"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_permissions(SCOPE_READ_REPORTS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    report = _get_accessible_report(
        db,
        report_id=report_id,
        data_access=data_access,
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    if report.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only completed reports can be downloaded.",
        )
    detail = report_detail_response(db, report=report)
    filename = f"threatlens-report-{report.id}"
    if format == "pdf":
        content = render_report_pdf(detail)
        media_type = "application/pdf"
        extension = "pdf"
    elif format == "html":
        content = render_report_html(detail).encode("utf-8")
        media_type = "text/html; charset=utf-8"
        extension = "html"
    else:
        content = render_report_markdown(detail).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
        extension = "md"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.{extension}"'
        },
    )


@router.post(
    "/{report_id:uuid}/retry",
    response_model=ReportQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_report(
    report_id: uuid.UUID,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    identity = retry_request_identity(idempotency_key, report_id=report_id)
    report = _get_accessible_report(
        db,
        report_id=report_id,
        data_access=data_access,
        for_update=True,
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    _require_report_owner_or_admin(user, report.owner_user_id)
    replay = find_retry_replay(
        db,
        user_id=user.id,
        report_id=report.id,
        identity=identity,
    )
    if replay is not None:
        return _queue_response(report, replay)
    try:
        reset_report_for_retry(db, report=report)
        run = create_report_task_run(
            db,
            report=report,
            actor_user_id=user.id,
            trigger_source="manual",
            originating_request=False,
            request_idempotency_key_hash=(identity.key_hash if identity else None),
            request_fingerprint=(identity.fingerprint if identity else None),
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="reports.generate.retry",
            resource_type="report",
            resource_id=str(report.id),
        )
        db.commit()
    except ReportStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ReportTaskLineageError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The report's originating task history is invalid. "
                "Contact an administrator before retrying it."
            ),
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        replay = find_retry_replay(
            db,
            user_id=user.id,
            report_id=report_id,
            identity=identity,
        )
        if replay is not None:
            current_report = db.get(Report, report_id)
            if current_report is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Report not found",
                ) from exc
            return _queue_response(current_report, replay)
        if _integrity_constraint_name(exc) == "uq_ai_task_runs_active_report":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This report already has a queued or running generation attempt.",
            ) from exc
        raise
    report_id = report.id
    run_id = run.id
    task_id = enqueue_report_task(report_id=report_id, task_run_id=run_id)
    return _queue_response(report, run, celery_task_id=task_id)


@router.delete("/{report_id:uuid}", status_code=status.HTTP_204_NO_CONTENT)
def remove_report(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    report = _get_accessible_report(
        db,
        report_id=report_id,
        data_access=data_access,
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    _require_report_owner_or_admin(user, report.owner_user_id)
    if report.status in {"queued", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A queued or running report cannot be deleted.",
        )
    delete_report(db, report=report)
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.delete",
        resource_type="report",
        resource_id=str(report_id),
    )
    db.commit()


@router.get("/schedules", response_model=list[ReportScheduleResponse])
def list_schedules(
    db: Session = Depends(get_db),
    user: User = Depends(require_report_admin_read),
):
    schedules = db.scalars(
        select(ReportSchedule).order_by(ReportSchedule.name.asc())
    ).all()
    return [report_schedule_response(schedule) for schedule in schedules]


@router.post(
    "/schedules",
    response_model=ReportScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(
    payload: ReportScheduleCreate,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_admin_write),
):
    operation = "report:schedule:create"
    identity = operation_request_identity(
        idempotency_key,
        operation=operation,
        payload=payload,
    )
    replay = find_operation_resource(
        db,
        user_id=user.id,
        operation=operation,
        resource_type="report_schedule",
        identity=identity,
        model=ReportSchedule,
        missing_detail=(
            "The report schedule created by this Idempotency-Key no longer exists. "
            "Use a new Idempotency-Key to create another schedule."
        ),
    )
    if replay is not None:
        return report_schedule_response(replay)
    if db.get(ReportTemplate, payload.template_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report template not found"
        )
    schedule = create_report_schedule(db, user_id=user.id, payload=payload)
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.schedule.create",
        resource_type="report_schedule",
        resource_id=str(schedule.id),
    )
    schedule = commit_operation_resource(
        db,
        resource=schedule,
        user_id=user.id,
        operation=operation,
        resource_type="report_schedule",
        identity=identity,
        model=ReportSchedule,
        missing_detail=(
            "The report schedule created by this Idempotency-Key no longer exists. "
            "Use a new Idempotency-Key to create another schedule."
        ),
    )
    return report_schedule_response(schedule)


@router.put(
    "/schedules/{schedule_id}",
    response_model=ReportScheduleResponse,
    responses=RESOURCE_PRECONDITION_RESPONSES,
)
def update_schedule(
    schedule_id: uuid.UUID,
    payload: ReportScheduleUpdate,
    response: Response,
    if_match: Annotated[list[str] | None, Header(alias="If-Match")] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_admin_write),
):
    schedule = db.scalar(
        select(ReportSchedule)
        .where(ReportSchedule.id == schedule_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report schedule not found"
        )
    _require_current_resource_version(
        current_updated_at=schedule.updated_at,
        if_match=if_match,
        resource_label="report schedule",
    )
    if db.get(ReportTemplate, payload.template_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report template not found"
        )
    apply_schedule_payload(schedule, payload)
    schedule.updated_at = next_resource_version(schedule.updated_at)
    db.add(schedule)
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.schedule.update",
        resource_type="report_schedule",
        resource_id=str(schedule.id),
    )
    db.commit()
    db.refresh(schedule)
    response.headers["ETag"] = resource_version_tag(schedule.updated_at)
    return report_schedule_response(schedule)


@router.post(
    "/schedules/{schedule_id}/run",
    response_model=list[ReportQueueResponse],
    status_code=status.HTTP_202_ACCEPTED,
    responses=RESOURCE_PRECONDITION_RESPONSES,
)
def run_schedule(
    schedule_id: uuid.UUID,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    if_match: Annotated[list[str] | None, Header(alias="If-Match")] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_admin_write),
):
    identity = schedule_run_request_identity(
        idempotency_key,
        schedule_id=schedule_id,
        actor_user_id=user.id,
    )
    replay = find_schedule_run_replay(
        db,
        user_id=user.id,
        schedule_id=schedule_id,
        identity=identity,
    )
    if replay is not None:
        return [_queue_response(*replay)] if replay[1] is not None else []
    schedule = db.scalar(
        select(ReportSchedule)
        .where(ReportSchedule.id == schedule_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report schedule not found",
        )
    replay = find_schedule_run_replay(
        db,
        user_id=user.id,
        schedule_id=schedule_id,
        identity=identity,
    )
    if replay is not None:
        return [_queue_response(*replay)] if replay[1] is not None else []
    _require_current_resource_version(
        current_updated_at=schedule.updated_at,
        if_match=if_match,
        resource_label="report schedule",
    )
    _active_reporting_settings(db)
    try:
        reports = reserve_schedule_runs(
            db,
            schedule_id=schedule_id,
            now=datetime.now(timezone.utc),
            force=True,
            generation_key_override=(
                f"schedule-manual:{schedule_id}:{identity.key_hash}"
                if identity
                else None
            ),
            request_idempotency_key_hash=(identity.key_hash if identity else None),
            request_fingerprint=(identity.fingerprint if identity else None),
        )
    except (
        AIContextBudgetError,
        ReportStorageError,
        ReportSectionSetError,
        ValidationError,
        ZoneInfoNotFoundError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ExportSnapshotChangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Matching articles changed while the scheduled report was being prepared. Try running the schedule again.",
        ) from exc
    except ExportAuthorizationChangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The schedule owner's data access changed while the report was being prepared. Try running the schedule again.",
        ) from exc
    if not reports:
        replay = find_schedule_run_replay(
            db,
            user_id=user.id,
            schedule_id=schedule_id,
            identity=identity,
        )
        if replay is not None:
            return [_queue_response(*replay)] if replay[1] is not None else []
    if not reports:
        if schedule.failure_state == "quarantined":
            detail = schedule.last_error or (
                "The report schedule is quarantined until its configuration is corrected."
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=detail,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A report already exists for this schedule period. Open the existing report and retry it if needed.",
        )
    entries = []
    for report in reports:
        if report.status != "queued":
            continue
        run = create_report_task_run(
            db,
            report=report,
            actor_user_id=user.id,
            trigger_source="manual",
            originating_request=True,
        )
        entries.append((report.id, run.id))
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.schedule.run",
        resource_type="report_schedule",
        resource_id=str(schedule_id),
        metadata={"queued": len(entries)},
    )
    db.commit()
    responses = []
    for report_id, run_id in entries:
        task_id = enqueue_report_task(report_id=report_id, task_run_id=run_id)
        responses.append(
            ReportQueueResponse(
                report_id=report_id,
                task_run_id=run_id,
                celery_task_id=task_id,
                status="queued",
                schedule_id=schedule_id,
            )
        )
    return responses


@router.delete(
    "/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=RESOURCE_PRECONDITION_RESPONSES,
)
def remove_schedule(
    schedule_id: uuid.UUID,
    if_match: Annotated[list[str] | None, Header(alias="If-Match")] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_admin_write),
):
    schedule = db.scalar(
        select(ReportSchedule)
        .where(ReportSchedule.id == schedule_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report schedule not found"
        )
    _require_current_resource_version(
        current_updated_at=schedule.updated_at,
        if_match=if_match,
        resource_label="report schedule",
    )
    db.delete(schedule)
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.schedule.delete",
        resource_type="report_schedule",
        resource_id=str(schedule_id),
    )
    db.commit()
