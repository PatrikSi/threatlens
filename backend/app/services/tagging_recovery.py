"""Durable retry state for incomplete rule evaluation, separate from classification."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.item import Item
from app.services.bounded_regex import ERROR_MESSAGES
from app.services.data_access_policy import DataAccessContext, handling_label_access_predicate

MAX_TAGGING_ATTEMPTS = 5
TAGGING_REPAIR_BATCH_SIZE = 50
RETRYABLE_TAGGING_ERRORS = frozenset({"worker_unavailable", "worker_timeout", "resource_limit"})


def record_incomplete_tagging(item: Item, errors: tuple[str, ...]) -> None:
    """The caller owns the Item lock and commits this with its other results."""
    permanent = [code for code in errors if code not in RETRYABLE_TAGGING_ERRORS]
    if not item.tagging_pending or item.tagging_pending_since_at is None:
        item.tagging_pending_since_at = datetime.now(timezone.utc)
    item.tagging_error_code = (permanent or list(errors))[0]
    item.tagging_pending = True
    item.tagging_attempts = min(MAX_TAGGING_ATTEMPTS, (item.tagging_attempts or 0) + 1)
    retry = not permanent and item.tagging_attempts < MAX_TAGGING_ATTEMPTS
    item.tagging_retry_at = (
        datetime.now(timezone.utc) + timedelta(seconds=60 * 2 ** (item.tagging_attempts - 1))
        if retry else None
    )


def clear_incomplete_tagging(item: Item) -> None:
    item.tagging_pending = False
    item.tagging_pending_since_at = None
    item.tagging_retry_at = None
    item.tagging_attempts = 0
    item.tagging_error_code = None


def tagging_recovery_summary(db: Session, data_access: DataAccessContext) -> dict:
    rows = db.execute(
        select(Item.tagging_error_code, Item.tagging_retry_at.is_not(None), func.count(Item.id))
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.tagging_pending.is_(True), handling_label_access_predicate(Feed.handling_label_id, data_access))
        .group_by(Item.tagging_error_code, Item.tagging_retry_at.is_not(None))
    ).all()
    retrying = attention = 0
    errors: dict[str, int] = {}
    for code, retry, count in rows:
        if retry:
            retrying += count
        else:
            attention += count
        if code in ERROR_MESSAGES:
            errors[code] = errors.get(code, 0) + count
    return {"pending": retrying + attention, "retrying": retrying, "needs_attention": attention,
            "errors": [{"code": code, "count": count, "message": ERROR_MESSAGES[code]}
                       for code, count in sorted(errors.items())]}
