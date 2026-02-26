import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_operator_user, require_token_scopes
from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_FEEDS, SCOPE_WRITE_FEEDS
from app.db.session import get_db
from app.models.feed import Feed
from app.models.user import User
from app.schemas.feed import FeedCreate, FeedResponse, FeedUpdate
from app.services.audit import record_audit
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

    feed = Feed(
        name=payload.name,
        url=feed_url,
        enabled=payload.enabled,
        fetch_interval_seconds=payload.fetch_interval_seconds,
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
