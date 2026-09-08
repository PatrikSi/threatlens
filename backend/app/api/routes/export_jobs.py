import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    AuthenticatedPrincipal,
    get_authorization_context,
    get_data_access_context,
    require_permissions,
)
from app.services.export_principals import (
    export_user_id,
    export_principal_type,
    require_supported_export_state,
)
from app.services.export_transport import DisconnectSafeFileResponse
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_ITEMS
from app.db.session import get_db
from app.models.export_job import ExportJob
from app.schemas.exports import (
    ArticleExportJobList,
    ArticleExportJobRequest,
    ArticleExportJobResponse,
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
from app.services.export_job_access import (
    ExportJobAccessDenied,
    assert_export_sources_visible,
    authorize_export_job,
    fence_export_job_access,
)
from app.services.export_job_contracts import ExportPrincipal
from app.services.export_job_download import (
    ExportJobArtifactUnavailable,
    materialize_export_job_download,
)
from app.services.export_jobs import (
    ExportJobCapacityExceeded,
    ExportJobConflict,
    create_export_job,
    export_job_response,
    export_job_responses,
    terminal_export_job,
)

router = APIRouter(prefix="/exports/jobs", tags=["exports"])


def _job(
    db: Session,
    job_id: uuid.UUID,
    principal: ExportPrincipal,
    *,
    lock: bool = False,
) -> ExportJob:
    statement = select(ExportJob).where(
        ExportJob.id == job_id,
        ExportJob.principal_type == export_principal_type(principal),
        ExportJob.principal_id == principal.id,
    )
    if lock:
        statement = statement.with_for_update()
    job = db.scalar(statement)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")
    return job


def _access_changed() -> ApiHTTPException:
    return ApiHTTPException(
        status_code=409,
        error_code="export_authorization_changed",
        detail=(
            "Export access changed or its accepting credential expired. Start a new export with your current access."
        ),
    )


@router.post(
    "", response_model=ArticleExportJobResponse, status_code=status.HTTP_202_ACCEPTED
)
def accept_export_job(
    payload: ArticleExportJobRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(require_permissions(SCOPE_READ_ITEMS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ArticleExportJobResponse:
    require_supported_export_state(
        principal,
        filters=payload.filters,
        include_user_state=payload.options.include_user_state,
    )
    authorization = get_authorization_context(request)
    try:
        job, created = create_export_job(
            db,
            principal=principal,
            request=request,
            authorization=authorization,
            data_access=data_access,
            payload=payload,
        )
        fence_authorization_context(db, authorization)
        fence_data_access_context(db, data_access)
        if created:
            record_audit(
                db,
                actor_user_id=export_user_id(principal),
                actor_principal_type=export_principal_type(principal),
                actor_principal_id=principal.id,
                action="exports.job.accept",
                resource_type="export_job",
                resource_id=str(job.id),
                metadata={"format": payload.format},
            )
        db.commit()
    except ExportJobConflict as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=409, error_code="idempotency_conflict", detail=str(exc)
        ) from exc
    except ExportJobCapacityExceeded as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=429,
            error_code="export_job_capacity",
            detail=str(exc),
            headers={"Retry-After": "60"},
        ) from exc
    except (
        ExportJobAccessDenied,
        AuthorizationStateUnavailable,
        DataPolicyError,
    ) as exc:
        db.rollback()
        raise _access_changed() from exc
    if created:
        # The durable row is the queue of record. Publication failure leaves a
        # visible queued job for Beat repair instead of losing accepted work.
        from app.tasks.export_tasks import enqueue_export_job

        enqueue_export_job(job.id)
    return export_job_response(
        db, job, current_authorization=authorization, current_access=data_access
    )


@router.get("", response_model=ArticleExportJobList)
def list_export_jobs(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(require_permissions(SCOPE_READ_ITEMS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ArticleExportJobList:
    jobs = db.scalars(
        select(ExportJob)
        .where(
            ExportJob.principal_type == export_principal_type(principal),
            ExportJob.principal_id == principal.id,
        )
        .order_by(ExportJob.created_at.desc(), ExportJob.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return ArticleExportJobList(
        items=export_job_responses(
            db,
            jobs[:limit],
            current_authorization=get_authorization_context(request),
            current_access=data_access,
        ),
        has_more=len(jobs) > limit,
    )


@router.get("/{job_id}", response_model=ArticleExportJobResponse)
def get_export_job(
    job_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(require_permissions(SCOPE_READ_ITEMS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ArticleExportJobResponse:
    return export_job_response(
        db,
        _job(db, job_id, principal),
        current_authorization=get_authorization_context(request),
        current_access=data_access,
    )


@router.post("/{job_id}/cancel", response_model=ArticleExportJobResponse)
def cancel_export_job(
    job_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(require_permissions(SCOPE_READ_ITEMS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ArticleExportJobResponse:
    job = _job(db, job_id, principal, lock=True)
    if job.status in {"queued", "running", "ready"}:
        terminal_export_job(db, job, "cancelled")
        record_audit(
            db,
            actor_user_id=export_user_id(principal),
            actor_principal_type=export_principal_type(principal),
            actor_principal_id=principal.id,
            action="exports.job.cancel",
            resource_type="export_job",
            resource_id=str(job.id),
        )
        db.commit()
    return export_job_response(
        db,
        job,
        current_authorization=get_authorization_context(request),
        current_access=data_access,
    )


@router.get("/{job_id}/download", response_class=DisconnectSafeFileResponse)
def download_export_job(
    job_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(require_permissions(SCOPE_READ_ITEMS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> DisconnectSafeFileResponse:
    job = _job(db, job_id, principal, lock=True)
    if job.expires_at <= datetime.now(timezone.utc):
        terminal_export_job(db, job, "expired")
        db.commit()
        raise HTTPException(
            status_code=410, detail="This export has expired. Start a new export."
        )
    if job.status != "ready":
        raise HTTPException(
            status_code=409, detail="This export is not ready for download."
        )
    download = None
    try:
        download = materialize_export_job_download(
            db,
            job,
            current_authorization=get_authorization_context(request),
            current_access=data_access,
        )
        record_audit(
            db,
            actor_user_id=export_user_id(principal),
            actor_principal_type=export_principal_type(principal),
            actor_principal_id=principal.id,
            action="exports.job.download",
            resource_type="export_job",
            resource_id=str(job.id),
            metadata={
                "format": job.format,
                "item_count": job.item_count,
                "file_size": job.file_size,
            },
        )
        # Keep publication policy fences for response streaming, as for the
        # synchronous export endpoint; audit commit happens before reacquiring.
        db.commit()
        job = _job(db, job_id, principal, lock=True)
        if job.status != "ready" or job.expires_at <= datetime.now(timezone.utc):
            raise ExportJobArtifactUnavailable("Export is no longer available")
        authorization, access = authorize_export_job(db, job)
        fence_export_job_access(db, job, authorization, access)
        current = get_authorization_context(request)
        fence_authorization_context(db, current)
        fence_data_access_context(db, data_access)
        assert_export_sources_visible(db, job, data_access)
        return download.response(
            media_type=job.media_type,
            filename=job.filename,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Export-Item-Count": str(job.item_count),
            },
        )
    except (
        ExportJobAccessDenied,
        AuthorizationStateUnavailable,
        DataPolicyError,
    ) as exc:
        if download is not None:
            download.close()
        raise _access_changed() from exc
    except (ExportJobArtifactUnavailable, ValueError) as exc:
        if download is not None:
            download.close()
        raise HTTPException(
            status_code=410,
            detail="The stored export is unavailable. Start a new export.",
        ) from exc
    except Exception:
        if download is not None:
            download.close()
        raise
