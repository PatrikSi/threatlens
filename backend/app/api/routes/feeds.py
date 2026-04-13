import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_operator_user, require_token_scopes
from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_FEEDS, SCOPE_WRITE_FEEDS
from app.db.session import get_db
from app.models.feed import Feed
from app.models.user import User
from app.schemas.feed import (
    FeedCreate,
    FeedExportResponse,
    FeedImportEntry,
    FeedImportRequest,
    FeedImportResponse,
    FeedMetadataRequest,
    FeedMetadataResponse,
    FeedResponse,
    FeedUpdate,
)
from app.services.audit import record_audit
from app.services.feed_probe import FeedProbeError, probe_feed_metadata
from app.services.url_utils import is_fetchable_url, normalize_feed_url, redact_feed_url
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("", response_model=list[FeedResponse])
def list_feeds(
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_FEEDS)),
):
    feeds = db.scalars(select(Feed).order_by(Feed.created_at.desc())).all()
    return [_serialize_feed(feed) for feed in feeds]


@router.post("/metadata", response_model=FeedMetadataResponse)
def get_feed_metadata(
    payload: FeedMetadataRequest,
    _operator: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_FEEDS)),
):
    try:
        metadata = probe_feed_metadata(payload.url)
    except FeedProbeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return FeedMetadataResponse(
        name=metadata.name,
        description=metadata.description,
        site_url=metadata.site_url,
        language=metadata.language,
        etag=metadata.etag,
        last_modified=metadata.last_modified,
        resolved_url=metadata.resolved_url,
        feed_type=metadata.feed_type,
    )


@router.get("/export", response_model=FeedExportResponse)
def export_feeds(
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_FEEDS)),
):
    feeds = db.scalars(select(Feed).order_by(Feed.created_at.asc())).all()
    exported = [
        FeedImportEntry(
            name=redact_feed_url(feed.name),
            url=redact_feed_url(feed.url),
            description=feed.description,
            site_url=feed.site_url,
            language=feed.language,
            enabled=feed.enabled,
            fetch_mode=feed.fetch_mode,
            fetch_interval_seconds=feed.fetch_interval_seconds,
            schedule_cron=feed.schedule_cron,
        )
        for feed in feeds
    ]
    return FeedExportResponse(exported_at=datetime.now(timezone.utc), feeds=exported)


@router.post("/import", response_model=FeedImportResponse)
def import_feeds(
    payload: FeedImportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_FEEDS)),
):
    settings = get_settings()
    created = 0
    updated = 0
    skipped = 0
    metadata_backfill_enqueued = 0
    errors: list[str] = []
    metadata_backfill_ids: list[str] = []

    for index, entry in enumerate(payload.feeds, start=1):
        feed_url = normalize_feed_url(entry.url)
        if not is_fetchable_url(feed_url, allow_private_network=settings.allow_private_network_fetch):
            errors.append(f"entry {index}: feed URL is not allowed")
            continue

        existing = db.scalar(select(Feed).where(Feed.url == feed_url))
        if existing is not None and not payload.overwrite_existing:
            skipped += 1
            continue

        resolved_name = _resolve_import_text(entry, "name", existing.name if existing else None)
        description = _resolve_import_text(entry, "description", existing.description if existing else None)
        site_url = _resolve_import_text(entry, "site_url", existing.site_url if existing else None)
        language = _resolve_import_text(entry, "language", existing.language if existing else None)
        etag = existing.etag if existing else None
        last_modified = existing.last_modified if existing else None
        enabled = entry.enabled if existing is None or _import_field_provided(entry, "enabled") else existing.enabled
        fetch_mode, fetch_interval_seconds, schedule_cron = _resolve_import_fetch_settings(entry, existing)

        if not resolved_name:
            if settings.probe_feed_metadata_on_import:
                try:
                    metadata = probe_feed_metadata(feed_url)
                    resolved_name = metadata.name or redact_feed_url(feed_url)
                    description = description or _clean_optional_text(metadata.description)
                    site_url = site_url or _clean_optional_text(metadata.site_url)
                    language = language or _clean_optional_text(metadata.language)
                    etag = metadata.etag or etag
                    last_modified = metadata.last_modified or last_modified
                except FeedProbeError:
                    resolved_name = redact_feed_url(feed_url)
            else:
                resolved_name = redact_feed_url(feed_url)

        if existing is None:
            new_feed = Feed(
                name=resolved_name,
                url=feed_url,
                description=description,
                site_url=site_url,
                language=language,
                enabled=enabled,
                fetch_mode=fetch_mode,
                fetch_interval_seconds=fetch_interval_seconds,
                schedule_cron=schedule_cron,
                etag=etag,
                last_modified=last_modified,
            )
            db.add(new_feed)
            db.flush()
            metadata_backfill_ids.append(str(new_feed.id))
            created += 1
            continue

        existing.name = resolved_name
        existing.description = description
        existing.site_url = site_url
        existing.language = language
        existing.enabled = enabled
        existing.fetch_mode = fetch_mode
        existing.fetch_interval_seconds = fetch_interval_seconds
        existing.schedule_cron = schedule_cron
        existing.etag = etag
        existing.last_modified = last_modified
        db.add(existing)
        metadata_backfill_ids.append(str(existing.id))
        updated += 1

    record_audit(
        db,
        actor_user_id=user.id,
        action="feeds.import",
        resource_type="feed",
        metadata={
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": len(errors),
            "metadata_backfill_requested": len(metadata_backfill_ids),
        },
    )
    db.commit()
    metadata_backfill_enqueued = _enqueue_metadata_backfills(
        metadata_backfill_ids,
        settings.max_metadata_backfill_tasks_per_request,
    )
    if metadata_backfill_enqueued < len(metadata_backfill_ids):
        errors.append(
            f"metadata backfill queue capped at {settings.max_metadata_backfill_tasks_per_request}; remaining feeds will backfill via scheduler"
        )
    return FeedImportResponse(created=created, updated=updated, skipped=skipped, errors=errors)


@router.post("", response_model=FeedResponse, status_code=status.HTTP_201_CREATED)
def create_feed(
    payload: FeedCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_FEEDS)),
):
    settings = get_settings()
    feed_url = normalize_feed_url(payload.url)
    if not is_fetchable_url(feed_url, allow_private_network=settings.allow_private_network_fetch):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Feed URL is not allowed")

    existing = db.scalar(select(Feed).where(Feed.url == feed_url))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Feed URL already exists")

    resolved_name = (payload.name or "").strip()
    description = payload.description
    site_url = payload.site_url
    language = payload.language
    etag = None
    last_modified = None

    if not resolved_name:
        if settings.probe_feed_metadata_on_create:
            try:
                metadata = probe_feed_metadata(feed_url)
            except FeedProbeError:
                metadata = None

            if metadata is not None:
                resolved_name = metadata.name or redact_feed_url(feed_url)
                description = description or metadata.description
                site_url = site_url or metadata.site_url
                language = language or metadata.language
                etag = metadata.etag
                last_modified = metadata.last_modified
            else:
                resolved_name = redact_feed_url(feed_url)
        else:
            resolved_name = redact_feed_url(feed_url)

    feed = Feed(
        name=resolved_name,
        url=feed_url,
        description=description,
        site_url=site_url,
        language=language,
        enabled=payload.enabled,
        fetch_mode=payload.fetch_mode,
        fetch_interval_seconds=payload.fetch_interval_seconds or 1800,
        schedule_cron=payload.schedule_cron,
        etag=etag,
        last_modified=last_modified,
    )
    db.add(feed)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="feeds.create",
        resource_type="feed",
        resource_id=str(feed.id),
        metadata={"name": redact_feed_url(feed.name), "url": redact_feed_url(feed.url)},
    )
    db.commit()
    db.refresh(feed)
    _enqueue_metadata_backfills([str(feed.id)], settings.max_metadata_backfill_tasks_per_request)
    return _serialize_feed(feed)


@router.patch("/{feed_id}", response_model=FeedResponse)
def update_feed(
    feed_id: uuid.UUID,
    payload: FeedUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_FEEDS)),
):
    feed = db.scalar(select(Feed).where(Feed.id == feed_id))
    if feed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feed not found")

    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates and not updates["name"]:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Feed name cannot be empty")

    target_mode = updates.get("fetch_mode", feed.fetch_mode)
    if target_mode == "interval":
        if updates.get("fetch_interval_seconds") is None:
            updates["fetch_interval_seconds"] = feed.fetch_interval_seconds or 1800
        updates["schedule_cron"] = None
    elif target_mode == "schedule":
        schedule_cron = updates.get("schedule_cron", feed.schedule_cron)
        if not schedule_cron:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="schedule_cron is required for schedule mode")
        updates["schedule_cron"] = schedule_cron

    for key, value in updates.items():
        setattr(feed, key, value)

    db.add(feed)
    record_audit(
        db,
        actor_user_id=user.id,
        action="feeds.update",
        resource_type="feed",
        resource_id=str(feed.id),
        metadata=updates,
    )
    db.commit()
    db.refresh(feed)
    return _serialize_feed(feed)


@router.delete("/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_feed(
    feed_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_FEEDS)),
):
    feed = db.scalar(select(Feed).where(Feed.id == feed_id))
    if feed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feed not found")

    db.delete(feed)
    record_audit(
        db,
        actor_user_id=user.id,
        action="feeds.delete",
        resource_type="feed",
        resource_id=str(feed_id),
    )
    db.commit()


@router.post("/{feed_id}/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh_feed(
    feed_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_FEEDS)),
):
    feed = db.scalar(select(Feed.id).where(Feed.id == feed_id))
    if feed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feed not found")

    celery_app.send_task("app.tasks.feed_tasks.fetch_feed", args=[str(feed_id)], kwargs={"force": True})
    record_audit(
        db,
        actor_user_id=user.id,
        action="feeds.refresh",
        resource_type="feed",
        resource_id=str(feed_id),
    )
    db.commit()
    return {"status": "queued"}


def _enqueue_metadata_backfills(feed_ids: list[str], max_tasks: int) -> int:
    if max_tasks <= 0:
        return 0
    enqueued = 0
    for target_id in feed_ids[:max_tasks]:
        celery_app.send_task("app.tasks.feed_tasks.backfill_feed_metadata", args=[target_id])
        enqueued += 1
    return enqueued


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _resolve_import_text(entry: FeedImportEntry, field_name: str, existing_value: str | None) -> str | None:
    if not _import_field_provided(entry, field_name):
        return _clean_optional_text(existing_value)
    return _clean_optional_text(getattr(entry, field_name))


def _resolve_import_fetch_settings(entry: FeedImportEntry, existing: Feed | None) -> tuple[str, int, str | None]:
    if existing is None:
        return (
            entry.fetch_mode,
            entry.fetch_interval_seconds or 1800,
            entry.schedule_cron,
        )

    fetch_mode = entry.fetch_mode if _import_field_provided(entry, "fetch_mode") else existing.fetch_mode
    fetch_interval_seconds = (
        entry.fetch_interval_seconds
        if _import_field_provided(entry, "fetch_interval_seconds") or _import_field_provided(entry, "fetch_mode")
        else existing.fetch_interval_seconds
    ) or existing.fetch_interval_seconds or 1800
    if fetch_mode == "interval":
        return (fetch_mode, fetch_interval_seconds, None)

    schedule_cron = (
        entry.schedule_cron
        if _import_field_provided(entry, "schedule_cron") or _import_field_provided(entry, "fetch_mode")
        else existing.schedule_cron
    )
    return (fetch_mode, fetch_interval_seconds, schedule_cron)


def _import_field_provided(entry: FeedImportEntry, field_name: str) -> bool:
    if field_name == "schedule_cron":
        return field_name in entry.model_dump(exclude_defaults=True)
    return field_name in entry.model_fields_set


def _serialize_feed(feed: Feed) -> FeedResponse:
    return FeedResponse.model_validate(feed).model_copy(
        update={
            "name": redact_feed_url(feed.name),
            "url": redact_feed_url(feed.url),
        }
    )
