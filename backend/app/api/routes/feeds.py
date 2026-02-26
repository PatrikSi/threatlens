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
from app.services.url_utils import is_fetchable_url
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("", response_model=list[FeedResponse])
def list_feeds(
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_FEEDS)),
):
    feeds = db.scalars(select(Feed).order_by(Feed.created_at.desc())).all()
    return list(feeds)


@router.post("/metadata", response_model=FeedMetadataResponse)
def get_feed_metadata(
    payload: FeedMetadataRequest,
    _user: User = Depends(require_token_scopes(SCOPE_READ_FEEDS)),
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
            name=feed.name,
            url=feed.url,
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
    errors: list[str] = []

    for index, entry in enumerate(payload.feeds, start=1):
        feed_url = entry.url.strip()
        if not is_fetchable_url(feed_url, allow_private_network=settings.allow_private_network_fetch):
            errors.append(f"entry {index}: feed URL is not allowed")
            continue

        existing = db.scalar(select(Feed).where(Feed.url == feed_url))
        if existing is not None and not payload.overwrite_existing:
            skipped += 1
            continue

        resolved_name = (entry.name or "").strip()
        description = entry.description
        site_url = entry.site_url
        language = entry.language
        etag = existing.etag if existing else None
        last_modified = existing.last_modified if existing else None

        if not resolved_name:
            try:
                metadata = probe_feed_metadata(feed_url)
                resolved_name = metadata.name or feed_url
                description = description or metadata.description
                site_url = site_url or metadata.site_url
                language = language or metadata.language
                etag = metadata.etag or etag
                last_modified = metadata.last_modified or last_modified
            except FeedProbeError:
                resolved_name = feed_url

        if existing is None:
            db.add(
                Feed(
                    name=resolved_name,
                    url=feed_url,
                    description=description,
                    site_url=site_url,
                    language=language,
                    enabled=entry.enabled,
                    fetch_mode=entry.fetch_mode,
                    fetch_interval_seconds=entry.fetch_interval_seconds or 1800,
                    schedule_cron=entry.schedule_cron,
                    etag=etag,
                    last_modified=last_modified,
                )
            )
            created += 1
            continue

        existing.name = resolved_name
        existing.description = description
        existing.site_url = site_url
        existing.language = language
        existing.enabled = entry.enabled
        existing.fetch_mode = entry.fetch_mode
        existing.fetch_interval_seconds = entry.fetch_interval_seconds or existing.fetch_interval_seconds
        existing.schedule_cron = entry.schedule_cron
        existing.etag = etag
        existing.last_modified = last_modified
        db.add(existing)
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
        },
    )
    db.commit()
    return FeedImportResponse(created=created, updated=updated, skipped=skipped, errors=errors)


@router.post("", response_model=FeedResponse, status_code=status.HTTP_201_CREATED)
def create_feed(
    payload: FeedCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_FEEDS)),
):
    settings = get_settings()
    feed_url = payload.url.strip()
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
        try:
            metadata = probe_feed_metadata(feed_url)
        except FeedProbeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unable to auto-detect feed metadata: {exc}",
            ) from exc

        resolved_name = metadata.name or feed_url
        description = description or metadata.description
        site_url = site_url or metadata.site_url
        language = language or metadata.language
        etag = metadata.etag
        last_modified = metadata.last_modified

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
        metadata={"name": feed.name, "url": feed.url},
    )
    db.commit()
    db.refresh(feed)
    return feed


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
    return feed


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

    celery_app.send_task("app.tasks.feed_tasks.fetch_feed", args=[str(feed_id)])
    record_audit(
        db,
        actor_user_id=user.id,
        action="feeds.refresh",
        resource_type="feed",
        resource_id=str(feed_id),
    )
    db.commit()
    return {"status": "queued"}
