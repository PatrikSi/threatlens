from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.alert_evaluation_match import AlertEvaluationMatch
from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_interest import AlertInterest
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.user import User
from app.services.alert_evaluation_history import record_alert_evaluation_activity
from app.services.alert_matching import build_item_haystack, match_alert_keywords


ALERT_ACCEPTANCE_RULE_PAGE_SIZE = 1_000


@dataclass(frozen=True)
class AlertEvaluationIntent:
    request_id: uuid.UUID
    created: bool
    activity_id: uuid.UUID | None = None


@dataclass(frozen=True)
class AlertAcceptanceSummary:
    accepted_rule_count: int
    accepted_match_count: int
    degraded_owner_count: int
    degraded_owners: tuple[dict, ...]


@dataclass(frozen=True)
class LockedAlertEvaluation:
    item: Item | None
    request: AlertEvaluationRequest | None


def lock_alert_evaluation_item(db: Session, *, item_id: uuid.UUID) -> Item | None:
    """Lock order root: item row, then evaluation request row.

    Rule rows are never locked by alert acceptance. A single streaming SELECT gives
    each evaluation one MVCC-consistent rule catalog snapshot without serializing
    unrelated items or rule editors.
    """
    return db.scalar(
        select(Item)
        .where(Item.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def lock_alert_evaluation_item_and_request(
    db: Session,
    *,
    request_id: uuid.UUID,
) -> LockedAlertEvaluation:
    """Lock an evaluation's item and request in the system-wide order."""
    item_id = db.scalar(
        select(AlertEvaluationRequest.item_id).where(
            AlertEvaluationRequest.id == request_id
        )
    )
    if item_id is None:
        return LockedAlertEvaluation(None, None)

    item = lock_alert_evaluation_item(db, item_id=item_id)
    request = db.scalar(
        select(AlertEvaluationRequest)
        .where(AlertEvaluationRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None:
        return LockedAlertEvaluation(item, None)
    if request.item_id != item_id:
        # item_id is immutable in normal operation. An out-of-band mutation must
        # not make this transaction acquire a second item lock out of order.
        return LockedAlertEvaluation(None, request)
    return LockedAlertEvaluation(item, request)


def persist_alert_evaluation_intent(
    db: Session,
    *,
    item: Item,
    classification: ItemClassification | None = None,
    source: str = "live",
    notify: bool = True,
    respect_rule_cutover: bool = True,
    actor_user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> AlertEvaluationIntent:
    locked_item = lock_alert_evaluation_item(db, item_id=item.id)
    if locked_item is None:
        raise LookupError("The source item no longer exists.")
    item = locked_item
    accepted_at = _as_utc(now or datetime.now(timezone.utc))
    existing = db.scalar(
        select(AlertEvaluationRequest)
        .where(
            AlertEvaluationRequest.item_id == item.id,
            AlertEvaluationRequest.item_content_hash == item.content_hash,
        )
        .with_for_update()
    )
    if existing is not None:
        if source == "live" and existing.active_source == "backfill":
            return promote_backfill_evaluation_to_live(
                db,
                request=existing,
                item=item,
                classification=classification,
                accepted_at=accepted_at,
            )
        return AlertEvaluationIntent(existing.id, False)

    request = AlertEvaluationRequest(
        item_id=item.id,
        item_content_hash=item.content_hash,
        source=source,
        active_source=source,
        notify=notify,
        notify_existing_occurrences=False,
        respect_rule_cutover=respect_rule_cutover,
        state="pending",
        accepted_at=accepted_at,
        available_at=accepted_at,
        dispatch_claimed_at=accepted_at,
        dispatch_attempt_count=1,
        backfill_count=1 if source == "backfill" else 0,
        last_backfill_at=accepted_at if source == "backfill" else None,
    )
    try:
        with db.begin_nested():
            db.add(request)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(AlertEvaluationRequest)
            .where(
                AlertEvaluationRequest.item_id == item.id,
                AlertEvaluationRequest.item_content_hash == item.content_hash,
            )
            .with_for_update()
        )
        if existing is None:
            raise
        if source == "live" and existing.active_source == "backfill":
            return promote_backfill_evaluation_to_live(
                db,
                request=existing,
                item=item,
                classification=classification,
                accepted_at=accepted_at,
            )
        return AlertEvaluationIntent(existing.id, False)

    summary = snapshot_accepted_alert_matches(
        db,
        request=request,
        item=item,
        classification=classification,
        accepted_at=accepted_at,
    )
    activity = record_alert_evaluation_activity(
        db,
        request_id=request.id,
        actor_user_id=actor_user_id,
        action="accepted",
        details={
            "source": source,
            "notify": notify,
            "respect_rule_cutover": respect_rule_cutover,
            "request_version": max(1, int(request.version or 1)),
            "backfill_count": max(0, int(request.backfill_count or 0)),
            "accepted_at": accepted_at.isoformat(),
            "cutover_at": _as_utc(item.first_seen_at).isoformat(),
            "accepted_rule_count": summary.accepted_rule_count,
            "accepted_match_count": summary.accepted_match_count,
            "degraded_owner_count": summary.degraded_owner_count,
        },
    )
    db.flush()
    return AlertEvaluationIntent(request.id, True, activity.id)


def promote_backfill_evaluation_to_live(
    db: Session,
    *,
    request: AlertEvaluationRequest,
    item: Item,
    classification: ItemClassification | None,
    accepted_at: datetime,
) -> AlertEvaluationIntent:
    """Give live classification precedence over a racing historical backfill."""
    previous_state = request.state
    db.execute(
        delete(AlertEvaluationMatch).where(
            AlertEvaluationMatch.request_id == request.id
        )
    )
    request.source = "live"
    request.active_source = "live"
    request.notify = True
    request.notify_existing_occurrences = True
    request.respect_rule_cutover = True
    request.state = "pending"
    request.attempt_count = 0
    request.dispatch_attempt_count = (
        max(0, int(request.dispatch_attempt_count or 0)) + 1
    )
    request.version = max(1, int(request.version or 1)) + 1
    request.accepted_at = accepted_at
    request.available_at = accepted_at
    request.dispatch_claimed_at = accepted_at
    request.dispatch_published_at = None
    request.claimed_at = None
    request.lease_token = None
    request.lease_expires_at = None
    request.completed_at = None
    request.evaluated_rule_count = 0
    request.occurrence_count = 0
    request.last_error_code = None
    request.last_error_message = None
    db.add(request)
    summary = snapshot_accepted_alert_matches(
        db,
        request=request,
        item=item,
        classification=classification,
        accepted_at=accepted_at,
    )
    activity = record_alert_evaluation_activity(
        db,
        request_id=request.id,
        action="promoted_to_live",
        details={
            "previous_source": "backfill",
            "previous_state": previous_state,
            "accepted_rule_count": summary.accepted_rule_count,
            "accepted_match_count": summary.accepted_match_count,
            "notify_existing_occurrences": True,
            "respect_rule_cutover": True,
            "cutover_at": _as_utc(item.first_seen_at).isoformat(),
        },
    )
    db.flush()
    return AlertEvaluationIntent(request.id, True, activity.id)


def reset_alert_evaluation_for_backfill(
    db: Session,
    *,
    request: AlertEvaluationRequest,
    item: Item,
    actor_user_id: uuid.UUID | None,
    now: datetime | None = None,
) -> AlertEvaluationIntent:
    if request.state not in {"succeeded", "dead_letter"}:
        return AlertEvaluationIntent(request.id, False)

    accepted_at = _as_utc(now or datetime.now(timezone.utc))
    db.execute(
        delete(AlertEvaluationMatch).where(
            AlertEvaluationMatch.request_id == request.id
        )
    )
    request.state = "pending"
    request.active_source = "backfill"
    request.notify = False
    request.notify_existing_occurrences = False
    request.respect_rule_cutover = False
    request.attempt_count = 0
    request.dispatch_attempt_count = 1
    request.version = max(1, int(request.version or 1)) + 1
    request.accepted_at = accepted_at
    request.available_at = accepted_at
    request.dispatch_claimed_at = accepted_at
    request.dispatch_published_at = None
    request.claimed_at = None
    request.lease_token = None
    request.lease_expires_at = None
    request.completed_at = None
    request.evaluated_rule_count = 0
    request.occurrence_count = 0
    request.last_error_code = None
    request.last_error_message = None
    request.backfill_count = max(0, int(request.backfill_count or 0)) + 1
    request.last_backfill_at = accepted_at
    db.add(request)
    summary = snapshot_accepted_alert_matches(
        db,
        request=request,
        item=item,
        classification=None,
        accepted_at=accepted_at,
    )
    activity = record_alert_evaluation_activity(
        db,
        request_id=request.id,
        actor_user_id=actor_user_id,
        action="backfill_requested",
        details={
            "original_source": request.source,
            "backfill_count": request.backfill_count,
            "accepted_rule_count": summary.accepted_rule_count,
            "accepted_match_count": summary.accepted_match_count,
            "degraded_owner_count": summary.degraded_owner_count,
            "notify": False,
            "respect_rule_cutover": False,
            "request_version": request.version,
            "accepted_at": accepted_at.isoformat(),
            "cutover_at": _as_utc(item.first_seen_at).isoformat(),
        },
    )
    db.flush()
    return AlertEvaluationIntent(request.id, True, activity.id)


def snapshot_accepted_alert_matches(
    db: Session,
    *,
    request: AlertEvaluationRequest,
    item: Item,
    classification: ItemClassification | None,
    accepted_at: datetime,
) -> AlertAcceptanceSummary:
    resolved_classification = classification
    if resolved_classification is None:
        resolved_classification = db.scalar(
            select(ItemClassification).where(ItemClassification.item_id == item.id)
        )
    haystack = build_item_haystack(
        title=item.title,
        summary=item.summary,
        url=item.url,
        canonical_url=item.canonical_url,
        classification=(
            resolved_classification.primary_category
            if resolved_classification is not None
            else None
        ),
    )

    accepted_rule_count = 0
    accepted_match_count = 0
    degraded_owner_count = 0
    degraded_owners: list[dict] = []
    rule_predicates = [
        AlertInterest.enabled.is_(True),
        User.is_active.is_(True),
        User.is_approved.is_(True),
    ]
    if request.respect_rule_cutover:
        rule_predicates.extend(
            [
                AlertInterest.durable_since.is_not(None),
                AlertInterest.durable_since <= item.first_seen_at,
            ]
        )
    rule_snapshot = db.scalars(
        select(AlertInterest)
        .join(User, User.id == AlertInterest.user_id)
        .where(*rule_predicates)
        .order_by(AlertInterest.user_id.asc(), AlertInterest.id.asc())
        .execution_options(yield_per=ALERT_ACCEPTANCE_RULE_PAGE_SIZE)
    )
    for rule in rule_snapshot:
        accepted_rule_count += 1
        matched_keywords = match_alert_keywords(rule.keywords or [], haystack)
        if not matched_keywords:
            continue
        suppressed = _is_after(rule.suppression_until, accepted_at)
        db.add(
            AlertEvaluationMatch(
                request_id=request.id,
                alert_interest_id=rule.id,
                owner_user_id=rule.user_id,
                rule_revision=rule.revision,
                alert_name_snapshot=rule.name[:255],
                alert_category_snapshot=rule.category[:64],
                alert_keywords_snapshot=[
                    str(keyword)[:128] for keyword in (rule.keywords or [])[:64]
                ],
                matched_keywords=[
                    str(keyword)[:128] for keyword in matched_keywords[:64]
                ],
                severity_snapshot=rule.severity,
                suppressed=suppressed,
                suppression_reason=(
                    (rule.suppression_reason or "Rule suppression window")[:500]
                    if suppressed
                    else None
                ),
            )
        )
        accepted_match_count += 1

    request.accepted_rule_count = accepted_rule_count
    request.accepted_match_count = accepted_match_count
    request.degraded_owner_count = degraded_owner_count
    request.degraded_owners_json = degraded_owners
    db.add(request)
    db.flush()
    return AlertAcceptanceSummary(
        accepted_rule_count,
        accepted_match_count,
        degraded_owner_count,
        tuple(degraded_owners),
    )


def _is_after(value: datetime | None, reference: datetime) -> bool:
    return value is not None and _as_utc(value) > _as_utc(reference)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
