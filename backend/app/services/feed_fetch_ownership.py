from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feed import Feed


FEED_FETCH_CONFIGURATION_FIELDS = frozenset(
    {
        "url",
        "enabled",
        "fetch_mode",
        "fetch_interval_seconds",
        "schedule_cron",
    }
)


class FeedFetchOwnershipLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeedFetchFence:
    feed_id: uuid.UUID
    fence: int
    url_digest: str


def apply_feed_fetch_configuration(
    feed: Feed,
    values: Mapping[str, object],
) -> frozenset[str]:
    """Apply material settings once; persistent feeds must be row-locked by the caller."""
    unsupported_fields = set(values) - FEED_FETCH_CONFIGURATION_FIELDS
    if unsupported_fields:
        unsupported = ", ".join(sorted(unsupported_fields))
        raise ValueError(f"unsupported feed fetch configuration fields: {unsupported}")

    changed_fields = frozenset(
        field_name
        for field_name, value in values.items()
        if getattr(feed, field_name) != value
    )
    for field_name in changed_fields:
        setattr(feed, field_name, values[field_name])
    if changed_fields:
        feed.fetch_fence = int(feed.fetch_fence or 0) + 1
    return changed_fields


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
    "FEED_FETCH_CONFIGURATION_FIELDS",
    "FeedFetchFence",
    "FeedFetchOwnershipLostError",
    "apply_feed_fetch_configuration",
    "claim_feed_fetch",
    "ensure_feed_fetch_owned",
]
