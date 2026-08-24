from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feed import Feed


class FeedFetchOwnershipLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeedFetchFence:
    feed_id: uuid.UUID
    fence: int
    url_digest: str


def claim_feed_fetch(db: Session, *, feed: Feed) -> FeedFetchFence:
    feed.fetch_fence = int(feed.fetch_fence or 0) + 1
    db.add(feed)
    db.flush()
    return FeedFetchFence(
        feed_id=feed.id,
        fence=feed.fetch_fence,
        url_digest=feed.url_digest,
    )


def ensure_feed_fetch_owned(
    db: Session,
    *,
    claim: FeedFetchFence,
) -> None:
    # Do not autoflush or refresh the caller's Feed instance here. This check is
    # deliberately the final gate before commit, so pending work must remain
    # local until ownership has been verified under the feed row lock.
    with db.no_autoflush:
        ownership = db.execute(
            select(Feed.enabled, Feed.url_digest, Feed.fetch_fence)
            .where(Feed.id == claim.feed_id)
            .with_for_update()
        ).one_or_none()
    if ownership is None:
        raise FeedFetchOwnershipLostError("feed was deleted during fetch")
    if not ownership.enabled:
        raise FeedFetchOwnershipLostError("feed was disabled during fetch")
    if ownership.url_digest != claim.url_digest:
        raise FeedFetchOwnershipLostError("feed URL changed during fetch")
    if int(ownership.fetch_fence or 0) != claim.fence:
        raise FeedFetchOwnershipLostError("a newer worker owns this feed fetch")


__all__ = [
    "FeedFetchFence",
    "FeedFetchOwnershipLostError",
    "claim_feed_fetch",
    "ensure_feed_fetch_owned",
]
