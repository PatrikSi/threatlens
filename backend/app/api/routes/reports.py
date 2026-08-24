from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_token_scopes
from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.token_scopes import SCOPE_READ_REPORTS, SCOPE_WRITE_REPORTS
from app.db.session import get_db
from app.models.ai_task_run import AITaskRun
from app.models.feed import Feed
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
from app.services.export_query import ExportSnapshotChangedError
from app.services.report_schedules import (
    apply_schedule_payload,
    create_report_schedule,
    report_schedule_response,
    reserve_schedule_runs,
)
from app.services.report_availability import (
    ReportingUnavailableError,
    ensure_reporting_available,
)
from app.services.report_rendering import (
    render_report_html,
    render_report_markdown,
    render_report_pdf,
)
from app.services.report_idempotency import (
    ReportIdempotencyConflictError,
    ReportIdempotencyError,
    ReportRequestIdentity,
    build_report_create_identity,
    build_report_retry_identity,
    find_report_create_replay,
    find_report_retry_replay,
)
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
from app.tasks.report_tasks import create_report_task_run, enqueue_report_task


router = APIRouter(prefix="/reports", tags=["reports"])
REPORT_PREVIEW_LIMIT = 25
logger = logging.getLogger(__name__)


@router.get("/capabilities", response_model=ReportCapabilitiesResponse)
def get_report_capabilities(
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_REPORTS)),
):
    active = load_active_ai_settings(db)
    feeds = db.execute(select(Feed.id, Feed.name).order_by(Feed.name.asc())).all()
    tags = db.execute(
        select(Tag.id, Tag.name)
        .where(exists(select(1).where(ItemTag.tag_id == Tag.id)))
        .order_by(Tag.name.asc())
    ).all()
    classifications = db.scalars(
        select(func.lower(ItemClassification.primary_category))
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
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_report_author(user)
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
    user: User = Depends(require_token_scopes(SCOPE_READ_REPORTS)),
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
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_report_author(user)
    _require_shared_template_admin(user, payload.visibility)
    template = create_report_template(db, user_id=user.id, payload=payload)
    record_audit(
        db,
        actor_user_id=user.id,
        action="reports.template.create",
        resource_type="report_template",
        resource_id=str(template.id),
        metadata={"visibility": template.visibility},
    )
    db.commit()
    db.refresh(template)
    return report_template_response(template)


@router.put("/templates/{template_id}", response_model=ReportTemplateResponse)
def update_template(
    template_id: uuid.UUID,
    payload: ReportTemplateUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_report_author(user)
    _require_shared_template_admin(user, payload.visibility)
    template = get_visible_report_template(db, template_id=template_id, user_id=user.id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report template not found"
        )
    _require_template_owner_or_admin(user, template.owner_user_id)
    try:
        update_report_template(template, payload=payload)
    except ReportTemplateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
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
    return report_template_response(template)


@router.post(
    "/templates/{template_id}/clone",
    response_model=ReportTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def clone_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_report_author(user)
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
    db.commit()
    db.refresh(clone)
    return report_template_response(clone)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_report_author(user)
    template = get_visible_report_template(db, template_id=template_id, user_id=user.id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report template not found"
        )
    _require_template_owner_or_admin(user, template.owner_user_id)
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
    _user: User = Depends(require_token_scopes(SCOPE_READ_REPORTS)),
):
    query = select(Report)
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
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_report_author(user)
    filters = filters_for_report_period(
        payload.filters,
        period_start=payload.period_start,
        period_end=payload.period_end,
    )
    payload = payload.model_copy(update={"filters": filters})
    identity = _create_request_identity(idempotency_key, payload=payload)
    replay = _find_create_replay(db, user_id=user.id, identity=identity)
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
        )
        report = create_report_from_plan(
            db,
            user_id=user.id,
            payload=payload,
            plan=plan,
            template=template,
            active=active,
            request_idempotency_key_hash=(identity.key_hash if identity else None),
            request_fingerprint=(identity.fingerprint if identity else None),
        )
        run = create_report_task_run(
            db, report=report, actor_user_id=user.id, trigger_source="manual"
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
    except (AIContextBudgetError, ReportStorageError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except IntegrityError:
        db.rollback()
        replay = _find_create_replay(db, user_id=user.id, identity=identity)
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
    _user: User = Depends(require_token_scopes(SCOPE_READ_REPORTS)),
):
    report = db.get(Report, report_id)
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
    _user: User = Depends(require_token_scopes(SCOPE_READ_REPORTS)),
):
    report = db.get(Report, report_id)
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
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_report_author(user)
    identity = _retry_request_identity(idempotency_key, report_id=report_id)
    report = db.scalar(
        select(Report)
        .where(Report.id == report_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    _require_report_owner_or_admin(user, report.owner_user_id)
    replay = _find_retry_replay(
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
    except IntegrityError as exc:
        db.rollback()
        replay = _find_retry_replay(
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
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_report_author(user)
    report = db.get(Report, report_id)
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
    user: User = Depends(require_token_scopes(SCOPE_READ_REPORTS)),
):
    _require_admin(user)
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
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_admin(user)
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
    db.commit()
    db.refresh(schedule)
    return report_schedule_response(schedule)


@router.put("/schedules/{schedule_id}", response_model=ReportScheduleResponse)
def update_schedule(
    schedule_id: uuid.UUID,
    payload: ReportScheduleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_admin(user)
    schedule = db.get(ReportSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report schedule not found"
        )
    if db.get(ReportTemplate, payload.template_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report template not found"
        )
    apply_schedule_payload(schedule, payload)
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
    return report_schedule_response(schedule)


@router.post(
    "/schedules/{schedule_id}/run",
    response_model=list[ReportQueueResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
def run_schedule(
    schedule_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_admin(user)
    _active_reporting_settings(db)
    try:
        reports = reserve_schedule_runs(
            db, schedule_id=schedule_id, now=datetime.now(timezone.utc), force=True
        )
    except (AIContextBudgetError, ReportStorageError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ExportSnapshotChangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Matching articles changed while the scheduled report was being prepared. Try running the schedule again.",
        ) from exc
    if not reports and db.get(ReportSchedule, schedule_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report schedule not found"
        )
    if not reports:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A report already exists for this schedule period. Open the existing report and retry it if needed.",
        )
    entries = []
    for report in reports:
        if report.status != "queued":
            continue
        run = create_report_task_run(
            db, report=report, actor_user_id=user.id, trigger_source="manual"
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
            )
        )
    return responses


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_schedule(
    schedule_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_REPORTS)),
):
    _require_admin(user)
    schedule = db.get(ReportSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report schedule not found"
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


def _active_reporting_settings(db: Session):
    active = load_active_ai_settings(db)
    try:
        ensure_reporting_available(active)
    except ReportingUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return active


def _create_request_identity(
    key: str | None,
    *,
    payload: ReportCreateRequest,
) -> ReportRequestIdentity | None:
    try:
        return build_report_create_identity(key, payload=payload)
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)


def _retry_request_identity(
    key: str | None,
    *,
    report_id: uuid.UUID,
) -> ReportRequestIdentity | None:
    try:
        return build_report_retry_identity(key, report_id=report_id)
    except ReportIdempotencyError as exc:
        _raise_idempotency_http_error(exc)


def _find_create_replay(
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


def _find_retry_replay(
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


def _raise_idempotency_http_error(exc: ReportIdempotencyError) -> NoReturn:
    status_code = (
        status.HTTP_409_CONFLICT
        if isinstance(exc, ReportIdempotencyConflictError)
        else status.HTTP_400_BAD_REQUEST
    )
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


def _queue_response(
    report: Report,
    run: AITaskRun,
    *,
    celery_task_id: str | None = None,
) -> ReportQueueResponse:
    return ReportQueueResponse(
        report_id=report.id,
        task_run_id=run.id,
        celery_task_id=celery_task_id or run.celery_task_id,
        status=run.status,
    )


def _integrity_constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _require_report_author(user: User) -> None:
    if user.role not in {ROLE_ADMIN, ROLE_ANALYST}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Report generation requires the analyst or administrator role.",
        )


def _require_admin(user: User) -> None:
    if user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Report schedules and shared templates require the administrator role.",
        )


def _require_shared_template_admin(user: User, visibility: str) -> None:
    if visibility == "shared" and user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can create or update shared report templates.",
        )


def _require_template_owner_or_admin(
    user: User, owner_user_id: uuid.UUID | None
) -> None:
    if user.role != ROLE_ADMIN and owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own private report templates.",
        )


def _require_report_owner_or_admin(user: User, owner_user_id: uuid.UUID | None) -> None:
    if user.role != ROLE_ADMIN and owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only retry or delete reports that you generated.",
        )
