from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.alert_occurrence import AlertOccurrence, AlertOccurrenceActivity
from app.models.user import User
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import DataAccessContext


ALERT_OCCURRENCE_STATES = frozenset({"new", "acknowledged", "investigating", "closed"})
ALERT_OCCURRENCE_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
ALERT_CLOSURE_DISPOSITIONS = frozenset(
    {"true_positive", "false_positive", "benign", "duplicate", "informational", "other"}
)
ALERT_BULK_LIMIT = 100


class AlertOccurrenceNotFoundError(LookupError):
    code = "alert_occurrence_not_found"


class AlertOccurrenceConflictError(RuntimeError):
    code = "alert_occurrence_version_conflict"

    def __init__(self, message: str, *, current_version: int) -> None:
        super().__init__(message)
        self.current_version = current_version


class AlertOccurrenceValidationError(ValueError):
    code = "alert_occurrence_invalid"


@dataclass(frozen=True)
class AlertOccurrencePage:
    items: list[AlertOccurrence]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class AlertOccurrenceActivityPage:
    items: list[AlertOccurrenceActivity]
    total: int
    page: int
    page_size: int


def list_alert_occurrences(
    db: Session,
    *,
    user: User,
    data_access: DataAccessContext,
    lifecycle_states: list[str],
    severities: list[str],
    alert_interest_id: uuid.UUID | None,
    suppressed: bool | None,
    snoozed: bool | None,
    since: datetime | None,
    until: datetime | None,
    page: int,
    page_size: int,
) -> AlertOccurrencePage:
    predicates = [
        AlertOccurrence.owner_user_id == user.id,
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
            AlertOccurrence.id,
            data_access,
        ),
    ]
    if lifecycle_states:
        predicates.append(AlertOccurrence.lifecycle_state.in_(lifecycle_states))
    if severities:
        predicates.append(AlertOccurrence.severity_snapshot.in_(severities))
    if alert_interest_id is not None:
        predicates.append(AlertOccurrence.rule_id_snapshot == alert_interest_id)
    if suppressed is not None:
        predicates.append(
            AlertOccurrence.suppressed_at.is_not(None)
            if suppressed
            else AlertOccurrence.suppressed_at.is_(None)
        )
    current_time = datetime.now(timezone.utc)
    if snoozed is True:
        predicates.append(AlertOccurrence.snoozed_until > current_time)
    elif snoozed is False:
        predicates.append(
            or_(
                AlertOccurrence.snoozed_until.is_(None),
                AlertOccurrence.snoozed_until <= current_time,
            )
        )
    if since is not None:
        predicates.append(AlertOccurrence.created_at >= since)
    if until is not None:
        predicates.append(AlertOccurrence.created_at <= until)

    total = db.scalar(select(func.count(AlertOccurrence.id)).where(*predicates)) or 0
    items = list(
        db.scalars(
            select(AlertOccurrence)
            .where(*predicates)
            .order_by(AlertOccurrence.created_at.desc(), AlertOccurrence.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return AlertOccurrencePage(items, int(total), page, page_size)


def get_alert_occurrence(
    db: Session,
    *,
    user: User,
    occurrence_id: uuid.UUID,
    data_access: DataAccessContext,
    for_update: bool = False,
) -> AlertOccurrence:
    query = select(AlertOccurrence).where(
        AlertOccurrence.id == occurrence_id,
        AlertOccurrence.owner_user_id == user.id,
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
            AlertOccurrence.id,
            data_access,
        ),
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    occurrence = db.scalar(query)
    if occurrence is None:
        raise AlertOccurrenceNotFoundError("Alert occurrence not found.")
    return occurrence


def update_alert_occurrence_lifecycle(
    db: Session,
    *,
    user: User,
    occurrence_id: uuid.UUID,
    data_access: DataAccessContext,
    expected_version: int,
    target_state: str,
    disposition: str | None,
    now: datetime | None = None,
) -> AlertOccurrence:
    occurrence = get_alert_occurrence(
        db,
        user=user,
        occurrence_id=occurrence_id,
        data_access=data_access,
        for_update=True,
    )
    _require_expected_version(occurrence, expected_version)
    return _apply_lifecycle_transition(
        db,
        occurrence=occurrence,
        actor_user_id=user.id,
        target_state=target_state,
        disposition=disposition,
        now=now or datetime.now(timezone.utc),
    )


def bulk_update_alert_occurrence_lifecycle(
    db: Session,
    *,
    user: User,
    data_access: DataAccessContext,
    entries: list[tuple[uuid.UUID, int]],
    target_state: str,
    disposition: str | None,
    now: datetime | None = None,
) -> list[AlertOccurrence]:
    if not entries or len(entries) > ALERT_BULK_LIMIT:
        raise AlertOccurrenceValidationError(
            f"Bulk alert occurrence updates require between 1 and {ALERT_BULK_LIMIT} unique items."
        )
    expected_by_id = dict(entries)
    if len(expected_by_id) != len(entries):
        raise AlertOccurrenceValidationError(
            "Bulk alert occurrence updates cannot contain duplicate IDs."
        )

    occurrence_ids = sorted(expected_by_id, key=str)
    rows = list(
        db.scalars(
            select(AlertOccurrence)
            .where(
                AlertOccurrence.owner_user_id == user.id,
                AlertOccurrence.id.in_(occurrence_ids),
                data_access_envelope_predicate(
                    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
                    AlertOccurrence.id,
                    data_access,
                ),
            )
            .order_by(AlertOccurrence.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) != len(occurrence_ids):
        raise AlertOccurrenceNotFoundError(
            "One or more alert occurrences were not found or are not owned by this account."
        )
    for occurrence in rows:
        _require_expected_version(occurrence, expected_by_id[occurrence.id])

    current_time = now or datetime.now(timezone.utc)
    return [
        _apply_lifecycle_transition(
            db,
            occurrence=occurrence,
            actor_user_id=user.id,
            target_state=target_state,
            disposition=disposition,
            now=current_time,
        )
        for occurrence in rows
    ]


def update_alert_occurrence_snooze(
    db: Session,
    *,
    user: User,
    occurrence_id: uuid.UUID,
    data_access: DataAccessContext,
    expected_version: int,
    snoozed_until: datetime | None,
    reason: str | None,
    now: datetime | None = None,
) -> AlertOccurrence:
    current_time = now or datetime.now(timezone.utc)
    occurrence = get_alert_occurrence(
        db,
        user=user,
        occurrence_id=occurrence_id,
        data_access=data_access,
        for_update=True,
    )
    _require_expected_version(occurrence, expected_version)
    if occurrence.lifecycle_state == "closed":
        raise AlertOccurrenceValidationError(
            "Closed alert occurrences cannot be snoozed."
        )
    if snoozed_until is not None:
        if _as_utc(snoozed_until) <= _as_utc(current_time):
            raise AlertOccurrenceValidationError("snoozed_until must be in the future.")
        normalized_reason = (reason or "").strip()
        if not normalized_reason:
            raise AlertOccurrenceValidationError(
                "A reason is required when snoozing an alert occurrence."
            )
        if len(normalized_reason) > 500:
            raise AlertOccurrenceValidationError(
                "Snooze reasons cannot exceed 500 characters."
            )
        occurrence.snoozed_until = snoozed_until
        occurrence.snooze_reason = normalized_reason
        action = "snoozed"
        details = {
            "snoozed_until": _as_utc(snoozed_until).isoformat(),
            "reason": normalized_reason,
        }
    else:
        if reason is not None:
            raise AlertOccurrenceValidationError(
                "A snooze reason cannot be retained when clearing a snooze."
            )
        previous_until = occurrence.snoozed_until
        previous_reason = occurrence.snooze_reason
        occurrence.snoozed_until = None
        occurrence.snooze_reason = None
        action = "snooze_cleared"
        details = {
            **(
                {"previous_snoozed_until": _as_utc(previous_until).isoformat()}
                if previous_until is not None
                else {}
            ),
            **(
                {"previous_reason": previous_reason}
                if previous_reason is not None
                else {}
            ),
        }
    occurrence.version += 1
    db.add(occurrence)
    _record_activity(
        db,
        occurrence=occurrence,
        actor_user_id=user.id,
        action=action,
        details=details,
    )
    db.flush()
    return occurrence


def list_alert_occurrence_activity(
    db: Session,
    *,
    user: User,
    occurrence_id: uuid.UUID,
    data_access: DataAccessContext,
    page: int,
    page_size: int,
) -> AlertOccurrenceActivityPage:
    get_alert_occurrence(
        db,
        user=user,
        occurrence_id=occurrence_id,
        data_access=data_access,
    )
    predicate = AlertOccurrenceActivity.occurrence_id == occurrence_id
    total = (
        db.scalar(select(func.count(AlertOccurrenceActivity.id)).where(predicate)) or 0
    )
    items = list(
        db.scalars(
            select(AlertOccurrenceActivity)
            .where(predicate)
            .order_by(
                AlertOccurrenceActivity.created_at.desc(),
                AlertOccurrenceActivity.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return AlertOccurrenceActivityPage(items, int(total), page, page_size)


def _apply_lifecycle_transition(
    db: Session,
    *,
    occurrence: AlertOccurrence,
    actor_user_id: uuid.UUID,
    target_state: str,
    disposition: str | None,
    now: datetime,
) -> AlertOccurrence:
    if target_state not in ALERT_OCCURRENCE_STATES:
        raise AlertOccurrenceValidationError(
            f"Unsupported alert occurrence state: {target_state}."
        )
    if target_state == "closed":
        if disposition not in ALERT_CLOSURE_DISPOSITIONS:
            raise AlertOccurrenceValidationError(
                "Closing an alert occurrence requires a supported disposition."
            )
    elif disposition is not None:
        raise AlertOccurrenceValidationError(
            "A disposition can be set only when closing an alert occurrence."
        )

    current_state = occurrence.lifecycle_state
    if target_state == current_state:
        if target_state == "closed" and disposition != occurrence.closure_disposition:
            previous_disposition = occurrence.closure_disposition
            occurrence.closure_disposition = disposition
            occurrence.version += 1
            db.add(occurrence)
            _record_activity(
                db,
                occurrence=occurrence,
                actor_user_id=actor_user_id,
                action="disposition_changed",
                details={"from": previous_disposition, "to": disposition},
            )
            db.flush()
        return occurrence
    allowed = {
        "new": {"acknowledged", "investigating", "closed"},
        "acknowledged": {"investigating", "closed"},
        "investigating": {"closed"},
        "closed": set(),
    }
    if target_state not in allowed.get(current_state, set()):
        raise AlertOccurrenceValidationError(
            f"Alert occurrence cannot move from {current_state} to {target_state}."
        )

    occurrence.lifecycle_state = target_state
    if target_state == "acknowledged":
        occurrence.acknowledged_at = now
        occurrence.acknowledged_by_user_id = actor_user_id
    elif target_state == "investigating":
        occurrence.investigating_at = now
        occurrence.investigating_by_user_id = actor_user_id
    elif target_state == "closed":
        occurrence.closed_at = now
        occurrence.closed_by_user_id = actor_user_id
        occurrence.closure_disposition = disposition
        occurrence.snoozed_until = None
        occurrence.snooze_reason = None
    occurrence.version += 1
    db.add(occurrence)
    _record_activity(
        db,
        occurrence=occurrence,
        actor_user_id=actor_user_id,
        action="lifecycle_changed",
        details={
            "from": current_state,
            "to": target_state,
            **({"disposition": disposition} if disposition is not None else {}),
        },
    )
    db.flush()
    return occurrence


def _require_expected_version(
    occurrence: AlertOccurrence, expected_version: int
) -> None:
    if occurrence.version != expected_version:
        raise AlertOccurrenceConflictError(
            (
                f"Alert occurrence changed since it was loaded: expected version {expected_version}, "
                f"current version is {occurrence.version}. Refresh and retry."
            ),
            current_version=occurrence.version,
        )


def _record_activity(
    db: Session,
    *,
    occurrence: AlertOccurrence,
    actor_user_id: uuid.UUID | None,
    action: str,
    details: dict,
) -> None:
    db.add(
        AlertOccurrenceActivity(
            occurrence_id=occurrence.id,
            actor_user_id=actor_user_id,
            action=action,
            details_json=details,
        )
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
