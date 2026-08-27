from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.alert_evaluation_match import AlertEvaluationMatch
from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import AlertOccurrence, AlertOccurrenceActivity
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.user import User
from app.services.alert_acceptance import lock_alert_evaluation_item_and_request
from app.services.alert_evaluation_history import record_alert_evaluation_activity
from app.services.integration_events import (
    build_alert_match_snapshot_payload,
    emit_integration_event,
)
from app.services.notification_webhook_templates import AlertMatchContext
from app.services.url_utils import redact_feed_url


ALERT_EVALUATION_MATCH_PAGE_SIZE = 1_000
ALERT_EVALUATION_OWNER_PAGE_SIZE = 100
ALERT_EVENT_RULE_LIST_CAP = 100
ALERT_EVENT_KEYWORD_LIST_CAP = 512
ALERT_EVENT_OCCURRENCE_ID_CAP = 500


class AlertEvaluationExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code[:64]
        self.public_message = message[:500]
        self.retryable = retryable


class AlertEvaluationExecutionLeaseLost(AlertEvaluationExecutionError):
    def __init__(self) -> None:
        super().__init__(
            "evaluation_lease_lost",
            "The alert evaluation lease expired or was replaced before completion.",
            retryable=True,
        )


@dataclass(frozen=True)
class AlertEvaluationOutcome:
    request_id: uuid.UUID
    evaluated_rules: int
    occurrences_created: int
    suppressed_occurrences: int
    integration_event_ids: tuple[uuid.UUID, ...]
    notifications_skipped: int


def evaluate_alert_request(
    db: Session,
    *,
    request_id: uuid.UUID,
    lease_token: str,
    now: datetime | None = None,
) -> AlertEvaluationOutcome:
    locked = lock_alert_evaluation_item_and_request(db, request_id=request_id)
    request = locked.request
    if request is None:
        raise AlertEvaluationExecutionError(
            "evaluation_request_missing",
            "The durable alert evaluation request no longer exists.",
            retryable=False,
        )
    current_time = _as_utc(now or datetime.now(timezone.utc))
    if (
        request.state != "processing"
        or request.lease_token != lease_token
        or not _is_after(request.lease_expires_at, current_time)
    ):
        raise AlertEvaluationExecutionLeaseLost()

    item = locked.item
    if item is None:
        raise AlertEvaluationExecutionError(
            "evaluation_item_missing",
            "The item was removed before alert evaluation could complete.",
            retryable=False,
        )
    if item.content_hash != request.item_content_hash:
        raise AlertEvaluationExecutionError(
            "evaluation_item_changed",
            "The item changed before this alert evaluation completed; its current version requires a new intent.",
            retryable=False,
        )
    feed = db.get(Feed, item.feed_id)
    if feed is None:
        raise AlertEvaluationExecutionError(
            "evaluation_feed_missing",
            "The source feed was removed before alert evaluation could complete.",
            retryable=False,
        )
    classification = db.scalar(
        select(ItemClassification).where(ItemClassification.item_id == item.id)
    )
    source_snapshot = _build_occurrence_source_snapshot(item, feed, classification)

    occurrence_count = 0
    suppressed_count = 0
    integration_event_ids: list[uuid.UUID] = []
    notification_skip_reasons: Counter[str] = Counter()
    owner_cursor: uuid.UUID | None = None
    while True:
        owner_query = (
            select(AlertEvaluationMatch.owner_user_id)
            .where(AlertEvaluationMatch.request_id == request.id)
            .group_by(AlertEvaluationMatch.owner_user_id)
            .order_by(AlertEvaluationMatch.owner_user_id.asc())
            .limit(ALERT_EVALUATION_OWNER_PAGE_SIZE)
        )
        if owner_cursor is not None:
            owner_query = owner_query.where(
                AlertEvaluationMatch.owner_user_id > owner_cursor
            )
        owner_ids = list(db.scalars(owner_query).all())
        if not owner_ids:
            break
        for owner_id in owner_ids:
            owner_occurrence_count = 0
            owner_suppressed_count = 0
            owner_context: AlertMatchContext | None = None
            match_cursor: uuid.UUID | None = None
            while True:
                match_predicates = [
                    AlertEvaluationMatch.request_id == request.id,
                    AlertEvaluationMatch.owner_user_id == owner_id,
                ]
                if match_cursor is not None:
                    match_predicates.append(AlertEvaluationMatch.id > match_cursor)
                matches = list(
                    db.scalars(
                        select(AlertEvaluationMatch)
                        .where(*match_predicates)
                        .order_by(AlertEvaluationMatch.id.asc())
                        .limit(ALERT_EVALUATION_MATCH_PAGE_SIZE)
                    ).all()
                )
                if not matches:
                    break
                owner_result = _evaluate_owner_matches(
                    db,
                    request=request,
                    item=item,
                    feed=feed,
                    owner_id=owner_id,
                    matches=matches,
                    source_snapshot=source_snapshot,
                    now=current_time,
                    context=owner_context,
                )
                owner_occurrence_count += owner_result.occurrences_created
                owner_suppressed_count += owner_result.suppressed_occurrences
                owner_context = owner_result.context
                match_cursor = matches[-1].id
                if len(matches) < ALERT_EVALUATION_MATCH_PAGE_SIZE:
                    break
            occurrence_count += owner_occurrence_count
            suppressed_count += owner_suppressed_count
            notification = _emit_owner_notification_event(
                db,
                request=request,
                item=item,
                feed=feed,
                owner_id=owner_id,
                context=owner_context,
            )
            if notification.event_id is not None:
                integration_event_ids.append(notification.event_id)
            if notification.skip_reason is not None:
                notification_skip_reasons[notification.skip_reason] += 1
        owner_cursor = owner_ids[-1]

    request.state = "succeeded"
    request.completed_at = current_time
    request.claimed_at = None
    request.lease_token = None
    request.lease_expires_at = None
    request.dispatch_claimed_at = None
    request.evaluated_rule_count = max(0, int(request.accepted_rule_count or 0))
    request.occurrence_count = occurrence_count
    request.last_error_code = None
    request.last_error_message = None
    request.notify_existing_occurrences = False
    request.version = max(1, int(request.version or 1)) + 1
    db.add(request)
    record_alert_evaluation_activity(
        db,
        request_id=request.id,
        action="succeeded",
        details={
            "active_source": request.active_source,
            "evaluated_rule_count": request.evaluated_rule_count,
            "occurrence_count": occurrence_count,
            "suppressed_occurrence_count": suppressed_count,
            "integration_event_count": len(integration_event_ids),
            "notification_skip_count": sum(notification_skip_reasons.values()),
            "notification_skip_reasons": dict(
                sorted(notification_skip_reasons.items())
            ),
        },
    )
    db.flush()
    return AlertEvaluationOutcome(
        request_id=request.id,
        evaluated_rules=request.evaluated_rule_count,
        occurrences_created=occurrence_count,
        suppressed_occurrences=suppressed_count,
        integration_event_ids=tuple(integration_event_ids),
        notifications_skipped=sum(notification_skip_reasons.values()),
    )


@dataclass(frozen=True)
class _OwnerEvaluationResult:
    occurrences_created: int
    suppressed_occurrences: int
    context: AlertMatchContext | None


@dataclass(frozen=True)
class _OwnerNotificationResult:
    event_id: uuid.UUID | None = None
    skip_reason: str | None = None


def _evaluate_owner_matches(
    db: Session,
    *,
    request: AlertEvaluationRequest,
    item: Item,
    feed: Feed,
    owner_id: uuid.UUID,
    matches: list[AlertEvaluationMatch],
    source_snapshot: dict,
    now: datetime,
    context: AlertMatchContext | None,
) -> _OwnerEvaluationResult:
    if not matches:
        return _OwnerEvaluationResult(0, 0, context)
    live_rule_ids = set(
        db.scalars(
            select(AlertInterest.id).where(
                AlertInterest.id.in_([match.alert_interest_id for match in matches])
            )
        ).all()
    )
    created_occurrences: list[AlertOccurrence] = []
    suppressed_count = 0
    for match in matches:
        occurrence, created = _get_or_create_occurrence(
            db,
            match=match,
            live_rule_ids=live_rule_ids,
            item=item,
            source_snapshot=source_snapshot,
            now=now,
        )
        if not created:
            if (
                request.notify
                and request.notify_existing_occurrences
                and not match.suppressed
                and occurrence.integration_event_id is None
            ):
                context = _append_alert_context(context, match)
            continue
        created_occurrences.append(occurrence)
        if match.suppressed:
            suppressed_count += 1
            continue
        if request.notify:
            context = _append_alert_context(context, match)
    return _OwnerEvaluationResult(len(created_occurrences), suppressed_count, context)


def _emit_owner_notification_event(
    db: Session,
    *,
    request: AlertEvaluationRequest,
    item: Item,
    feed: Feed,
    owner_id: uuid.UUID,
    context: AlertMatchContext | None,
) -> _OwnerNotificationResult:
    if not request.notify or context is None:
        return _OwnerNotificationResult()
    ineligibility_reason = _owner_notification_ineligibility_reason(
        db, owner_id=owner_id
    )
    if ineligibility_reason is not None:
        record_alert_evaluation_activity(
            db,
            request_id=request.id,
            action="notification_skipped",
            details={
                "reason": ineligibility_reason,
                "owner_user_id": str(owner_id),
                "stage": "post_acceptance",
            },
        )
        return _OwnerNotificationResult(skip_reason=ineligibility_reason)
    accepted_match = exists(
        select(AlertEvaluationMatch.id).where(
            AlertEvaluationMatch.request_id == request.id,
            AlertEvaluationMatch.owner_user_id == owner_id,
            AlertEvaluationMatch.suppressed.is_(False),
            AlertEvaluationMatch.alert_interest_id == AlertOccurrence.rule_id_snapshot,
            AlertEvaluationMatch.rule_revision == AlertOccurrence.rule_revision,
        )
    )
    deliverable = (
        AlertOccurrence.owner_user_id == owner_id,
        AlertOccurrence.item_id_snapshot == item.id,
        AlertOccurrence.item_content_hash == item.content_hash,
        AlertOccurrence.integration_event_id.is_(None),
        accepted_match,
    )
    occurrence_ids = list(
        db.scalars(
            select(AlertOccurrence.id)
            .where(*deliverable)
            .order_by(AlertOccurrence.id.asc())
            .limit(ALERT_EVENT_OCCURRENCE_ID_CAP + 1)
        ).all()
    )
    if not occurrence_ids:
        return _OwnerNotificationResult()
    truncated = len(occurrence_ids) > ALERT_EVENT_OCCURRENCE_ID_CAP
    payload_ids = occurrence_ids[:ALERT_EVENT_OCCURRENCE_ID_CAP]
    event = emit_integration_event(
        db,
        event_type="alert_match",
        source_type="item",
        source_id=item.id,
        idempotency_key=(
            f"item:{item.id}:alert_match:v4:{item.content_hash}:owner:{owner_id}:"
            f"evaluation:{request.id}:accepted:{_as_utc(request.accepted_at).isoformat()}"
        ),
        schema_version=3,
        payload=build_alert_match_snapshot_payload(
            item=item,
            feed=feed,
            contexts_by_owner={owner_id: context},
            occurrence_ids=payload_ids,
            occurrence_count=context.count,
            occurrence_ids_truncated=truncated,
            evaluation_request_id=request.id,
            owner_user_id=owner_id,
        ),
    )
    db.execute(
        update(AlertOccurrence)
        .where(*deliverable)
        .values(integration_event_id=event.id)
        .execution_options(synchronize_session=False)
    )
    return _OwnerNotificationResult(event_id=event.id)


def _owner_notification_ineligibility_reason(
    db: Session, *, owner_id: uuid.UUID
) -> str | None:
    owner = db.scalar(select(User).where(User.id == owner_id))
    if owner is None:
        return "owner_missing_after_acceptance"
    if not owner.is_active:
        return "owner_inactive_after_acceptance"
    if not owner.is_approved:
        return "owner_unapproved_after_acceptance"
    return None


def _get_or_create_occurrence(
    db: Session,
    *,
    match: AlertEvaluationMatch,
    live_rule_ids: set[uuid.UUID],
    item: Item,
    source_snapshot: dict,
    now: datetime,
) -> tuple[AlertOccurrence, bool]:
    identity = (
        AlertOccurrence.rule_id_snapshot == match.alert_interest_id,
        AlertOccurrence.rule_revision == match.rule_revision,
        AlertOccurrence.item_id_snapshot == item.id,
        AlertOccurrence.item_content_hash == item.content_hash,
    )
    existing = db.scalar(select(AlertOccurrence).where(*identity))
    if existing is not None:
        return existing, False

    occurrence = AlertOccurrence(
        alert_interest_id=(
            match.alert_interest_id
            if match.alert_interest_id in live_rule_ids
            else None
        ),
        rule_id_snapshot=match.alert_interest_id,
        owner_user_id=match.owner_user_id,
        item_id=item.id,
        item_id_snapshot=item.id,
        rule_revision=match.rule_revision,
        item_content_hash=item.content_hash,
        alert_name_snapshot=match.alert_name_snapshot,
        alert_category_snapshot=match.alert_category_snapshot,
        alert_keywords_snapshot=list(match.alert_keywords_snapshot or []),
        matched_keywords=list(match.matched_keywords or []),
        source_snapshot_json=source_snapshot,
        severity_snapshot=match.severity_snapshot,
        suppressed_at=now if match.suppressed else None,
        suppression_reason=match.suppression_reason if match.suppressed else None,
    )
    try:
        with db.begin_nested():
            db.add(occurrence)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(AlertOccurrence).where(*identity))
        if existing is None:
            raise
        return existing, False
    db.add(
        AlertOccurrenceActivity(
            occurrence_id=occurrence.id,
            actor_user_id=None,
            action="created",
            details_json={
                "matched_keyword_count": len(occurrence.matched_keywords),
                "suppressed": match.suppressed,
                "rule_revision": match.rule_revision,
                **(
                    {"suppression_reason": occurrence.suppression_reason}
                    if match.suppressed
                    else {}
                ),
            },
        )
    )
    return occurrence, True


def _append_alert_context(
    existing: AlertMatchContext | None,
    match: AlertEvaluationMatch,
) -> AlertMatchContext:
    if existing is None:
        return AlertMatchContext(
            count=1,
            primary_name=match.alert_name_snapshot,
            names=[match.alert_name_snapshot],
            categories=[match.alert_category_snapshot],
            matched_keywords=list(match.matched_keywords or []),
        )
    names = list(existing.names)
    categories = list(existing.categories)
    if len(names) < ALERT_EVENT_RULE_LIST_CAP:
        names.append(match.alert_name_snapshot)
        categories.append(match.alert_category_snapshot)
    keywords = list(existing.matched_keywords)
    for keyword in match.matched_keywords or []:
        if keyword not in keywords and len(keywords) < ALERT_EVENT_KEYWORD_LIST_CAP:
            keywords.append(keyword)
    return AlertMatchContext(
        count=existing.count + 1,
        primary_name=existing.primary_name,
        names=names,
        categories=categories,
        matched_keywords=keywords,
    )


def _build_occurrence_source_snapshot(
    item: Item,
    feed: Feed,
    classification: ItemClassification | None,
) -> dict:
    return {
        "item": {
            "id": str(item.id),
            "title": item.title[:512],
            "summary": item.summary[:2_000] if item.summary else None,
            "url": item.url[:2_048],
            "canonical_url": item.canonical_url[:2_048] if item.canonical_url else None,
            "published_at": _isoformat(item.published_at),
            "first_seen_at": _isoformat(item.first_seen_at),
            "status": item.status[:32],
        },
        "feed": {
            "id": str(feed.id),
            "name": feed.name[:255],
            "url": redact_feed_url(feed.url)[:2_048],
        },
        "classification": (
            {"primary_category": (classification.primary_category or "")[:64]}
            if classification is not None
            else None
        ),
    }


def _isoformat(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _is_after(value: datetime | None, reference: datetime) -> bool:
    return value is not None and _as_utc(value) > _as_utc(reference)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
