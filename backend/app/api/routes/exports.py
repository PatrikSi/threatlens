import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.deps import require_token_scopes
from app.core.config import get_settings
from app.core.logging_config import verbose_logging_enabled
from app.core.token_scopes import SCOPE_READ_ITEMS
from app.db.session import get_db
from app.models.feed import Feed
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
    ExportSnapshotChangedError,
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
    _user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    settings = get_settings()
    feeds = db.execute(select(Feed.id, Feed.name).order_by(Feed.name.asc(), Feed.id.asc())).all()
    tags = db.execute(
        select(Tag.id, Tag.name)
        .where(exists(select(1).where(ItemTag.tag_id == Tag.id)))
        .order_by(Tag.name.asc(), Tag.id.asc())
    ).all()
    classifications = db.scalars(
        select(func.lower(ItemClassification.primary_category))
        .distinct()
        .order_by(func.lower(ItemClassification.primary_category).asc())
    ).all()
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
    user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    settings = get_settings()
    context = build_export_query_context(user_id=user.id, filters=payload.filters)
    counts = load_export_counts(db, context=context)
    item_ids = load_export_item_ids(db, context=context, limit=settings.export_preview_limit)
    records = list(
        iter_export_records(
            db,
            item_ids=item_ids,
            context=context,
            include_iocs=True,
        )
    )
    return ArticleExportPreviewResponse(
        total_matches=counts.total,
        articles_with_text=counts.with_article_text,
        items_with_iocs=counts.with_iocs,
        preview_limit=settings.export_preview_limit,
        exceeds_export_limit=counts.total > settings.export_max_items,
        exceeds_pdf_limit=counts.total > settings.export_pdf_max_items,
        items=build_preview_items(records),
    )


@router.post("", response_class=FileResponse)
def download_export(
    payload: ArticleExportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    settings = get_settings()
    context = build_export_query_context(user_id=user.id, filters=payload.filters)
    counts = load_export_counts(db, context=context)
    item_limit = settings.export_pdf_max_items if payload.format == "pdf_bundle" else settings.export_max_items
    _validate_export_count(counts.total, item_limit=item_limit, export_format=payload.format)

    artifact: ExportArtifact | None = None
    try:
        with acquire_export_lock(user_id=user.id, settings=settings):
            item_ids = load_export_item_ids(db, context=context, limit=item_limit + 1)
            _validate_export_count(len(item_ids), item_limit=item_limit, export_format=payload.format)
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
    except ExportAlreadyRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another export is already running for this account. Wait for it to finish and try again.",
        ) from exc
    except ExportLockUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Export coordination is temporarily unavailable. Try again later.",
        ) from exc
    except ExportSizeLimitError as exc:
        _record_failed_export(db, user=user, payload=payload, reason="size_limit")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "The generated export exceeded the configured size limit. Narrow the filters or exclude article text."
            ),
        ) from exc
    except ExportSnapshotChangedError as exc:
        _record_failed_export(db, user=user, payload=payload, reason="snapshot_changed")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Matching articles changed while the export was generated. Refresh the preview and try again.",
        ) from exc
    except Exception as exc:
        if artifact is not None:
            remove_export_artifact(artifact.path)
        logger.exception(
            "article_export_generation_failed user_id=%s format=%s error_type=%s",
            user.id,
            payload.format,
            type(exc).__name__,
            exc_info=verbose_logging_enabled(settings),
        )
        _record_failed_export(db, user=user, payload=payload, reason="generation_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The export could not be generated. Review the server logs and try again.",
        ) from exc

    if artifact is None:  # pragma: no cover - defensive only
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Export was not generated")
    try:
        record_audit(
            db,
            actor_user_id=user.id,
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


def _validate_export_count(total: int, *, item_limit: int, export_format: str) -> None:
    if total <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No articles match the selected filters.",
        )
    if total > item_limit:
        label = "PDF bundle" if export_format == "pdf_bundle" else "export"
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"The {label} matches {total} articles, above the configured limit of {item_limit}. Narrow the filters.",
        )


def _record_failed_export(
    db: Session,
    *,
    user: User,
    payload: ArticleExportRequest,
    reason: str,
) -> None:
    record_audit(
        db,
        actor_user_id=user.id,
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


def _filter_audit_summary(payload: ArticleExportRequest) -> dict[str, object]:
    filters = payload.filters
    return {
        "has_search": bool(filters.q),
        "feed_count": len(filters.feed_ids),
        "tag_count": len(filters.tag_ids),
        "classification_count": len(filters.classifications),
        "ai_relevance_labels": list(filters.ai_relevance_labels),
        "has_date_range": bool(filters.since or filters.until),
        "user_state_filtered": filters.is_read is not None or filters.is_starred is not None,
        "article_text_filter": filters.has_article_text,
    }
