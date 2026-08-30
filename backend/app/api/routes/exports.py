import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.deps import (
    AuthenticatedPrincipal,
    get_data_access_context,
    require_permissions,
)
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.logging_config import verbose_logging_enabled
from app.core.token_scopes import SCOPE_READ_ITEMS
from app.db.session import get_db
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.models.user import User
from app.schemas.exports import (
    ArticleExportCapabilitiesResponse,
    ArticleExportPreviewRequest,
    ArticleExportPreviewResponse,
    ArticleExportRequest,
    ExportFormatCapability,
    ExportOptionEntry,
)
from app.services.audit import record_audit
from app.services.data_access_policy import (
    DataAccessContext,
    current_data_policy_revision,
    handling_label_access_predicate,
)
from app.services.export_artifacts import (
    ExportArtifact,
    ExportSizeLimitError,
    generate_export_artifact,
    remove_export_artifact,
)
from app.services.export_lock import (
    ExportAlreadyRunningError,
    ExportLockUnavailableError,
    acquire_export_lock,
)
from app.services.export_query import (
    ExportAuthorizationChangedError,
    ExportSnapshotChangedError,
    assert_export_authorization_unchanged,
    build_export_query_context,
    build_preview_items,
    iter_export_records,
    load_export_counts,
    load_export_item_ids,
)

router = APIRouter(prefix="/exports", tags=["exports"])
logger = logging.getLogger(__name__)

FORMAT_CAPABILITIES = (
    ExportFormatCapability(
        id="csv",
        label="CSV",
        extension=".csv",
        media_type="text/csv",
        description="Spreadsheet-ready article inventory with flat intelligence fields.",
        supports_article_text=True,
        supports_iocs=True,
        supports_user_state=True,
    ),
    ExportFormatCapability(
        id="jsonl",
        label="JSONL",
        extension=".jsonl",
        media_type="application/x-ndjson",
        description="Complete line-delimited records for scripts, data pipelines, and archival.",
        supports_article_text=True,
        supports_iocs=True,
        supports_user_state=True,
    ),
    ExportFormatCapability(
        id="threat_bundle",
        label="ThreatLens bundle",
        extension=".zip",
        media_type="application/zip",
        description="Manifest, JSONL, CSV, and optional IOC CSV packaged together.",
        supports_article_text=True,
        supports_iocs=True,
        supports_user_state=True,
    ),
    ExportFormatCapability(
        id="stix",
        label="STIX 2.1",
        extension=".stix.json",
        media_type="application/stix+json",
        description="Reports and standards-mapped cyber observables for TIP and SIEM ingestion.",
        supports_article_text=False,
        supports_iocs=True,
        supports_user_state=False,
    ),
    ExportFormatCapability(
        id="misp",
        label="MISP",
        extension=".misp.json",
        media_type="application/json",
        description="Unpublished MISP events with article context and mapped attributes.",
        supports_article_text=True,
        supports_iocs=True,
        supports_user_state=True,
    ),
    ExportFormatCapability(
        id="pdf_bundle",
        label="PDF bundle",
        extension=".pdf.zip",
        media_type="application/zip",
        description="One readable PDF per article plus a manifest, packaged as a ZIP.",
        supports_article_text=True,
        supports_iocs=True,
        supports_user_state=True,
    ),
)


@router.get("/capabilities", response_model=ArticleExportCapabilitiesResponse)
def get_export_capabilities(
    db: Session = Depends(get_db),
    _principal: AuthenticatedPrincipal = Depends(require_permissions(SCOPE_READ_ITEMS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    settings = get_settings()
    _require_data_access_snapshot_current(db, data_access=data_access)
    feed_access = handling_label_access_predicate(
        Feed.handling_label_id,
        data_access,
    )
    feeds = db.execute(
        select(Feed.id, Feed.name)
        .where(feed_access)
        .order_by(Feed.name.asc(), Feed.id.asc())
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
        .order_by(Tag.name.asc(), Tag.id.asc())
    ).all()
    classifications = db.scalars(
        select(func.lower(ItemClassification.primary_category))
        .join(Item, Item.id == ItemClassification.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(feed_access)
        .distinct()
        .order_by(func.lower(ItemClassification.primary_category).asc())
    ).all()
    _require_data_access_snapshot_current(db, data_access=data_access)
    return ArticleExportCapabilitiesResponse(
        formats=list(FORMAT_CAPABILITIES),
        feeds=[ExportOptionEntry(id=feed_id, name=name) for feed_id, name in feeds],
        tags=[ExportOptionEntry(id=tag_id, name=name) for tag_id, name in tags],
        classifications=[value for value in classifications if value],
        max_items=settings.export_max_items,
        max_pdf_items=settings.export_pdf_max_items,
        max_uncompressed_bytes=settings.export_max_uncompressed_bytes,
        preview_limit=settings.export_preview_limit,
    )


@router.post("/preview", response_model=ArticleExportPreviewResponse)
def preview_export(
    payload: ArticleExportPreviewRequest,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(require_permissions(SCOPE_READ_ITEMS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    settings = get_settings()
    _require_supported_machine_state_options(principal, filters=payload.filters)
    context = build_export_query_context(
        user_id=_human_user_id(principal),
        filters=payload.filters,
        data_access=data_access,
    )
    try:
        counts = load_export_counts(db, context=context)
        item_ids = load_export_item_ids(
            db, context=context, limit=settings.export_preview_limit
        )
        records = list(
            iter_export_records(
                db,
                item_ids=item_ids,
                context=context,
                include_iocs=True,
            )
        )
    except ExportAuthorizationChangedError as exc:
        raise _export_authorization_changed_error() from exc
    return ArticleExportPreviewResponse(
        total_matches=counts.total,
        articles_with_text=counts.with_article_text,
        items_with_iocs=counts.with_iocs,
        preview_limit=settings.export_preview_limit,
        exceeds_export_limit=counts.total > settings.export_max_items,
        exceeds_pdf_limit=counts.total > settings.export_pdf_max_items,
        items=build_preview_items(
            records,
            personal_state_available=isinstance(principal, User),
        ),
    )


@router.post("", response_class=FileResponse)
def download_export(
    payload: ArticleExportRequest,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(require_permissions(SCOPE_READ_ITEMS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    settings = get_settings()
    _require_supported_machine_state_options(
        principal,
        filters=payload.filters,
        include_user_state=payload.options.include_user_state,
    )
    context = build_export_query_context(
        user_id=_human_user_id(principal),
        filters=payload.filters,
        data_access=data_access,
    )
    try:
        counts = load_export_counts(db, context=context)
    except ExportAuthorizationChangedError as exc:
        raise _export_authorization_changed_error() from exc
    item_limit = (
        settings.export_pdf_max_items
        if payload.format == "pdf_bundle"
        else settings.export_max_items
    )
    _validate_export_count(
        counts.total, item_limit=item_limit, export_format=payload.format
    )

    artifact: ExportArtifact | None = None
    try:
        with acquire_export_lock(
            principal_type=_principal_type(principal),
            principal_id=principal.id,
            settings=settings,
        ):
            item_ids = load_export_item_ids(db, context=context, limit=item_limit + 1)
            _validate_export_count(
                len(item_ids), item_limit=item_limit, export_format=payload.format
            )
            records = iter_export_records(
                db,
                item_ids=item_ids,
                context=context,
                include_iocs=payload.options.include_iocs,
            )
            artifact = generate_export_artifact(
                records,
                item_count=len(item_ids),
                export_format=payload.format,
                filters=payload.filters,
                options=payload.options,
                max_uncompressed_bytes=settings.export_max_uncompressed_bytes,
            )
            assert_export_authorization_unchanged(db, context=context)
    except ExportAlreadyRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another export is already running for this account. Wait for it to finish and try again.",
        ) from exc
    except ExportLockUnavailableError as exc:
        if artifact is not None:
            remove_export_artifact(artifact.path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Export coordination is temporarily unavailable. Try again later.",
        ) from exc
    except ExportSizeLimitError as exc:
        _record_failed_export(
            db, principal=principal, payload=payload, reason="size_limit"
        )
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                "The generated export exceeded the configured size limit. Narrow the filters or exclude article text."
            ),
        ) from exc
    except ExportSnapshotChangedError as exc:
        _record_failed_export(
            db, principal=principal, payload=payload, reason="snapshot_changed"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Matching articles changed while the export was generated. Refresh the preview and try again.",
        ) from exc
    except ExportAuthorizationChangedError as exc:
        if artifact is not None:
            remove_export_artifact(artifact.path)
        _record_failed_export(
            db, principal=principal, payload=payload, reason="authorization_changed"
        )
        raise _export_authorization_changed_error() from exc
    except Exception as exc:
        if artifact is not None:
            remove_export_artifact(artifact.path)
        logger.exception(
            "article_export_generation_failed user_id=%s format=%s error_type=%s",
            principal.id,
            payload.format,
            type(exc).__name__,
            exc_info=verbose_logging_enabled(settings),
        )
        _record_failed_export(
            db, principal=principal, payload=payload, reason="generation_failed"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The export could not be generated. Review the server logs and try again.",
        ) from exc

    if artifact is None:  # pragma: no cover - defensive only
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Export was not generated",
        )
    try:
        assert_export_authorization_unchanged(db, context=context)
        record_audit(
            db,
            actor_user_id=_human_user_id(principal),
            actor_principal_type=_principal_type(principal),
            actor_principal_id=principal.id,
            action="exports.download",
            resource_type="article_export",
            metadata={
                "format": payload.format,
                "item_count": artifact.item_count,
                "file_size": artifact.file_size,
                "uncompressed_bytes": artifact.uncompressed_bytes,
                "filters": _filter_audit_summary(payload),
            },
        )
        db.commit()
        assert_export_authorization_unchanged(db, context=context)
    except ExportAuthorizationChangedError as exc:
        remove_export_artifact(artifact.path)
        _record_failed_export(
            db, principal=principal, payload=payload, reason="authorization_changed"
        )
        raise _export_authorization_changed_error() from exc
    except Exception:
        remove_export_artifact(artifact.path)
        raise

    return FileResponse(
        path=artifact.path,
        media_type=artifact.media_type,
        filename=artifact.filename,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Export-Item-Count": str(artifact.item_count),
        },
        background=BackgroundTask(remove_export_artifact, artifact.path),
    )


def _export_authorization_changed_error() -> ApiHTTPException:
    return ApiHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Your data access changed while the export was being prepared. "
            "Refresh the preview and start the export again."
        ),
        error_code="export_authorization_changed",
    )


def _require_data_access_snapshot_current(
    db: Session, *, data_access: DataAccessContext
) -> None:
    if current_data_policy_revision(db) != data_access.policy_revision:
        raise _export_authorization_changed_error()


def _validate_export_count(total: int, *, item_limit: int, export_format: str) -> None:
    if total <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No articles match the selected filters.",
        )
    if total > item_limit:
        label = "PDF bundle" if export_format == "pdf_bundle" else "export"
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"The {label} matches {total} articles, above the configured limit of {item_limit}. Narrow the filters.",
        )


def _record_failed_export(
    db: Session,
    *,
    principal: AuthenticatedPrincipal,
    payload: ArticleExportRequest,
    reason: str,
) -> None:
    try:
        record_audit(
            db,
            actor_user_id=_human_user_id(principal),
            actor_principal_type=_principal_type(principal),
            actor_principal_id=principal.id,
            action="exports.download",
            resource_type="article_export",
            success=False,
            metadata={
                "format": payload.format,
                "reason": reason,
                "filters": _filter_audit_summary(payload),
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "article_export_failure_audit_failed principal_type=%s "
            "principal_id=%s reason=%s error_type=%s",
            _principal_type(principal),
            principal.id,
            reason,
            type(exc).__name__,
            exc_info=verbose_logging_enabled(get_settings()),
        )


def _human_user_id(principal: AuthenticatedPrincipal) -> uuid.UUID | None:
    return principal.id if isinstance(principal, User) else None


def _principal_type(principal: AuthenticatedPrincipal) -> str:
    return "user" if isinstance(principal, User) else "service_account"


def _require_supported_machine_state_options(
    principal: AuthenticatedPrincipal,
    *,
    filters,
    include_user_state: bool = False,
) -> None:
    if isinstance(principal, User):
        return
    if (
        filters.is_read is None
        and filters.is_starred is None
        and not include_user_state
    ):
        return
    raise ApiHTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "Service accounts do not have personal read, starred, or note state. "
            "Remove user-state filters and disable user-state export fields."
        ),
        error_code="service_account_user_state_unsupported",
    )


def _filter_audit_summary(payload: ArticleExportRequest) -> dict[str, object]:
    filters = payload.filters
    return {
        "has_search": bool(filters.q),
        "feed_count": len(filters.feed_ids),
        "tag_count": len(filters.tag_ids),
        "classification_count": len(filters.classifications),
        "ai_relevance_labels": list(filters.ai_relevance_labels),
        "has_date_range": bool(filters.since or filters.until),
        "user_state_filtered": filters.is_read is not None
        or filters.is_starred is not None,
        "article_text_filter": filters.has_article_text,
    }
