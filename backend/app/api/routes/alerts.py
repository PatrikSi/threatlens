import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import require_token_scopes
from app.api.routes.alert_operations import router as alert_operations_router
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.core.token_scopes import (
    SCOPE_READ_ALERTS,
    SCOPE_READ_ITEMS,
    SCOPE_WRITE_ALERTS,
)
from app.db.session import get_db
from app.models.alert_interest import AlertInterest
from app.models.user import User
from app.schemas.alert import (
    AlertInterestCreate,
    AlertInterestPreviewRequest,
    AlertInterestResponse,
    AlertInterestUpdate,
    AlertBackfillApplyResponse,
    AlertBackfillApplyRequest,
    AlertBackfillPreviewResponse,
    AlertBackfillRequest,
    AlertMatchListResponse,
    AlertOccurrenceActivityListResponse,
    AlertOccurrenceBulkResponse,
    AlertOccurrenceBulkUpdate,
    AlertOccurrenceLifecycleUpdate,
    AlertOccurrenceListResponse,
    AlertOccurrenceResponse,
    AlertOccurrenceSnoozeUpdate,
)
from app.services.alert_evaluation import (
    AlertBackfillPreviewError,
    create_alert_backfill_preview,
    persist_alert_backfill_preview_intents,
    record_alert_backfill_preview_dispatch,
)
from app.services.alert_match_queries import (
    AlertMatchDefinition,
    list_matches_for_alerts,
)
from app.services.alert_matching import normalize_alert_keywords
from app.services.alert_occurrences import (
    ALERT_OCCURRENCE_SEVERITIES,
    ALERT_OCCURRENCE_STATES,
    AlertOccurrenceConflictError,
    AlertOccurrenceNotFoundError,
    AlertOccurrenceValidationError,
    bulk_update_alert_occurrence_lifecycle,
    get_alert_occurrence,
    list_alert_occurrence_activity,
    list_alert_occurrences,
    update_alert_occurrence_lifecycle,
    update_alert_occurrence_snooze,
)
from app.services.audit import record_audit
from app.services.alert_rules import (
    AlertRuleOwnerUnavailableError,
    AlertRuleQuotaExceededError,
    lock_alert_rule_creation_slot,
)
from app.tasks.alert_tasks import enqueue_alert_evaluation_requests

router = APIRouter(prefix="/alerts", tags=["alerts"])
logger = logging.getLogger(__name__)
MAX_ALERT_PAGE = 1_000_000
AlertPage = Annotated[int, Query(ge=1, le=MAX_ALERT_PAGE)]


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Alert name cannot be empty",
        )
    return normalized


def _normalize_category(value: str) -> str:
    normalized = "_".join(value.strip().lower().split())
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Alert category cannot be empty",
        )
    return normalized


def _normalize_keywords(values: list[str]) -> list[str]:
    normalized = normalize_alert_keywords(values)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least one keyword is required",
        )
    return normalized


def _parse_uuid_csv(raw_value: str | None, detail: str) -> list[uuid.UUID]:
    if not raw_value:
        return []

    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in raw_value.split(","):
        candidate = value.strip()
        if not candidate:
            continue
        try:
            parsed_uuid = uuid.UUID(candidate)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
            ) from exc
        if parsed_uuid in seen:
            continue
        seen.add(parsed_uuid)
        parsed.append(parsed_uuid)

    return parsed


def _parse_category_csv(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []

    categories: list[str] = []
    seen: set[str] = set()
    for value in raw_value.split(","):
        normalized = _normalize_category(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        categories.append(normalized)
    return categories


@router.get("", response_model=list[AlertInterestResponse])
def list_alert_interests(
    include_disabled: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS)),
):
    query = select(AlertInterest).where(AlertInterest.user_id == user.id)
    if not include_disabled:
        query = query.where(AlertInterest.enabled.is_(True))

    rows = db.scalars(query.order_by(AlertInterest.created_at.desc())).all()
    return list(rows)


@router.post(
    "", response_model=AlertInterestResponse, status_code=status.HTTP_201_CREATED
)
def create_alert_interest(
    payload: AlertInterestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS)),
):
    try:
        lock_alert_rule_creation_slot(db, owner_user_id=user.id)
    except (AlertRuleQuotaExceededError, AlertRuleOwnerUnavailableError) as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            error_code=exc.code,
            headers={"X-Error-Code": exc.code},
        ) from exc
    now = datetime.now(timezone.utc)
    alert = AlertInterest(
        user_id=user.id,
        name=_normalize_name(payload.name),
        category=_normalize_category(payload.category),
        keywords=_normalize_keywords(payload.keywords),
        enabled=payload.enabled,
        severity=payload.severity,
        revision=1,
        row_version=1,
        durable_since=now if payload.enabled else None,
        suppression_until=payload.suppression_until,
        suppression_reason=_normalize_optional_reason(payload.suppression_reason),
    )
    db.add(alert)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="alerts.create",
        resource_type="alert_interest",
        resource_id=str(alert.id),
        metadata={
            "name": alert.name,
            "category": alert.category,
            "keyword_count": len(alert.keywords),
            "enabled": alert.enabled,
            "severity": alert.severity,
            "revision": alert.revision,
            "row_version": alert.row_version,
            "suppressed": alert.suppression_until is not None,
        },
    )
    db.commit()
    db.refresh(alert)
    return alert


@router.post("/preview", response_model=AlertMatchListResponse)
def preview_alert_interest(
    payload: AlertInterestPreviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS, SCOPE_READ_ITEMS)),
):
    preview = AlertMatchDefinition(
        id=uuid.uuid4(),
        name=_normalize_name(payload.name or "Preview"),
        category=_normalize_category(payload.category),
        keywords=_normalize_keywords(payload.keywords),
    )
    return _list_matches_for_alerts(
        db,
        user=user,
        alerts=[preview],
        page=1,
        page_size=payload.limit,
        sort="first_seen_desc",
    )


@router.get("/matches", response_model=AlertMatchListResponse)
def list_alert_matches(
    q: str | None = None,
    alert_ids: str | None = Query(default=None),
    categories: str | None = Query(default=None),
    include_disabled: bool = Query(default=False),
    is_starred: bool | None = None,
    is_read: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: AlertPage = 1,
    page_size: int = Query(default=25, ge=1, le=100),
    sort: str = Query(default="published_at_desc"),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS, SCOPE_READ_ITEMS)),
):
    selected_alert_ids = _parse_uuid_csv(alert_ids, "Invalid alert id in alert_ids")
    selected_categories = _parse_category_csv(categories)

    alerts_query = select(AlertInterest).where(AlertInterest.user_id == user.id)
    if not include_disabled:
        alerts_query = alerts_query.where(AlertInterest.enabled.is_(True))
    if selected_alert_ids:
        alerts_query = alerts_query.where(AlertInterest.id.in_(selected_alert_ids))
    if selected_categories:
        alerts_query = alerts_query.where(
            AlertInterest.category.in_(selected_categories)
        )

    alerts = db.scalars(alerts_query.order_by(AlertInterest.created_at.desc())).all()
    return _list_matches_for_alerts(
        db,
        user=user,
        alerts=list(alerts),
        q=q,
        is_starred=is_starred,
        is_read=is_read,
        since=since,
        until=until,
        page=page,
        page_size=page_size,
        sort=sort,
    )


def _list_matches_for_alerts(db: Session, **kwargs) -> AlertMatchListResponse:
    return list_matches_for_alerts(
        db,
        **kwargs,
        keyword_cap=get_settings().alert_matches_keyword_cap,
    )


@router.get("/occurrences", response_model=AlertOccurrenceListResponse)
def get_alert_occurrences(
    lifecycle_states: list[str] = Query(default=[]),
    severities: list[str] = Query(default=[]),
    alert_interest_id: uuid.UUID | None = None,
    suppressed: bool | None = None,
    snoozed: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: AlertPage = 1,
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS, SCOPE_READ_ITEMS)),
):
    since = _as_utc(since)
    until = _as_utc(until)
    invalid_states = sorted(set(lifecycle_states) - ALERT_OCCURRENCE_STATES)
    invalid_severities = sorted(set(severities) - ALERT_OCCURRENCE_SEVERITIES)
    if invalid_states:
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported alert occurrence state: {', '.join(invalid_states)}.",
            error_code="alert_occurrence_state_invalid",
        )
    if invalid_severities:
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported alert occurrence severity: {', '.join(invalid_severities)}.",
            error_code="alert_occurrence_severity_invalid",
        )
    if since is not None and until is not None and since > until:
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="since must be earlier than or equal to until.",
            error_code="alert_occurrence_window_invalid",
        )
    result = list_alert_occurrences(
        db,
        user=user,
        lifecycle_states=list(dict.fromkeys(lifecycle_states)),
        severities=list(dict.fromkeys(severities)),
        alert_interest_id=alert_interest_id,
        suppressed=suppressed,
        snoozed=snoozed,
        since=since,
        until=until,
        page=page,
        page_size=page_size,
    )
    return {
        "items": result.items,
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
    }


@router.post(
    "/occurrences/reconciliation/preview",
    response_model=AlertBackfillPreviewResponse,
)
def preview_alert_occurrence_backfill(
    payload: AlertBackfillRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS, SCOPE_READ_ITEMS)),
):
    _require_alert_admin(user)
    snapshot = create_alert_backfill_preview(
        db,
        actor_user_id=user.id,
        since=payload.since,
        until=payload.until,
        limit=payload.limit,
        cursor_first_seen_at=payload.cursor_first_seen_at,
        cursor_item_id=payload.cursor_item_id,
    )
    result = snapshot.preview
    candidates = [
        {
            "item_id": uuid.UUID(str(candidate["item_id"])),
            "content_hash": str(candidate["content_hash"]),
            "title": str(candidate["title"]),
            "first_seen_at": datetime.fromisoformat(str(candidate["first_seen_at"])),
        }
        for candidate in result.candidates_json
    ]
    db.commit()
    return {
        "preview_token": result.id,
        "expires_at": result.expires_at,
        "candidates": candidates,
        "matched_count": result.matched_count,
        "returned_count": len(candidates),
        "truncated": result.has_more,
        "has_more": result.has_more,
        "next_cursor_first_seen_at": result.next_cursor_first_seen_at,
        "next_cursor_item_id": result.next_cursor_item_id,
        "notifications_enabled": False,
    }


@router.post(
    "/occurrences/reconciliation/apply",
    response_model=AlertBackfillApplyResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def apply_alert_occurrence_backfill(
    payload: AlertBackfillApplyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS, SCOPE_READ_ITEMS)),
):
    _require_alert_admin(user)
    try:
        result = persist_alert_backfill_preview_intents(
            db,
            preview_id=payload.preview_token,
            actor_user_id=user.id,
        )
    except AlertBackfillPreviewError as exc:
        db.rollback()
        status_code = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "alert_backfill_preview_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise ApiHTTPException(
            status_code=status_code,
            detail=str(exc),
            error_code=exc.code,
            headers={"X-Error-Code": exc.code},
        ) from exc
    if not result.replayed:
        record_audit(
            db,
            actor_user_id=user.id,
            action="alerts.occurrences.backfill",
            resource_type="alert_evaluation_request",
            metadata={
                "accepted": len(result.request_ids),
                "existing": result.existing_count,
                "skipped": result.skipped_count,
                "preview_token": str(payload.preview_token),
                "has_more": result.next_cursor_item_id is not None,
                "notifications_enabled": False,
            },
        )
    db.commit()
    enqueue_failed = result.enqueue_failed
    if result.dispatch_required:
        enqueue_failed = not enqueue_alert_evaluation_requests(list(result.request_ids))
        try:
            record_alert_backfill_preview_dispatch(
                db,
                preview_id=payload.preview_token,
                actor_user_id=user.id,
                enqueue_failed=enqueue_failed,
            )
            db.commit()
        except (AlertBackfillPreviewError, SQLAlchemyError) as exc:
            db.rollback()
            logger.exception(
                "alert_backfill_dispatch_receipt_failed preview_id=%s error_type=%s",
                payload.preview_token,
                type(exc).__name__,
            )
    return {
        "accepted": len(result.request_ids),
        "existing": result.existing_count,
        "skipped": result.skipped_count,
        "enqueue_failed": enqueue_failed,
        "has_more": result.next_cursor_item_id is not None,
        "next_cursor_first_seen_at": result.next_cursor_first_seen_at,
        "next_cursor_item_id": result.next_cursor_item_id,
        "notifications_enabled": False,
    }


@router.post(
    "/occurrences/bulk/acknowledge",
    response_model=AlertOccurrenceBulkResponse,
)
def bulk_acknowledge_alert_occurrences(
    payload: AlertOccurrenceBulkUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS, SCOPE_READ_ITEMS)),
):
    if payload.disposition is not None:
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A disposition can be supplied only when closing alert occurrences.",
            error_code="alert_occurrence_disposition_unexpected",
        )
    return _bulk_mutate_occurrences(
        db,
        user=user,
        payload=payload,
        target_state="acknowledged",
        audit_action="alerts.occurrences.bulk_acknowledge",
    )


@router.post(
    "/occurrences/bulk/close",
    response_model=AlertOccurrenceBulkResponse,
)
def bulk_close_alert_occurrences(
    payload: AlertOccurrenceBulkUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS, SCOPE_READ_ITEMS)),
):
    if payload.disposition is None:
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A disposition is required when closing alert occurrences.",
            error_code="alert_occurrence_disposition_required",
        )
    return _bulk_mutate_occurrences(
        db,
        user=user,
        payload=payload,
        target_state="closed",
        audit_action="alerts.occurrences.bulk_close",
    )


router.include_router(alert_operations_router)


@router.get(
    "/occurrences/{occurrence_id}",
    response_model=AlertOccurrenceResponse,
)
def get_alert_occurrence_detail(
    occurrence_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS, SCOPE_READ_ITEMS)),
):
    try:
        return get_alert_occurrence(db, user=user, occurrence_id=occurrence_id)
    except Exception as exc:
        return _raise_occurrence_error(db, exc)


@router.get(
    "/occurrences/{occurrence_id}/activity",
    response_model=AlertOccurrenceActivityListResponse,
)
def get_alert_occurrence_activity(
    occurrence_id: uuid.UUID,
    page: AlertPage = 1,
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS)),
):
    try:
        result = list_alert_occurrence_activity(
            db,
            user=user,
            occurrence_id=occurrence_id,
            page=page,
            page_size=page_size,
        )
        return {
            "items": result.items,
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
        }
    except Exception as exc:
        return _raise_occurrence_error(db, exc)


@router.patch(
    "/occurrences/{occurrence_id}/lifecycle",
    response_model=AlertOccurrenceResponse,
)
def patch_alert_occurrence_lifecycle(
    occurrence_id: uuid.UUID,
    payload: AlertOccurrenceLifecycleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS, SCOPE_READ_ITEMS)),
):
    try:
        occurrence = update_alert_occurrence_lifecycle(
            db,
            user=user,
            occurrence_id=occurrence_id,
            expected_version=payload.expected_version,
            target_state=payload.state,
            disposition=payload.disposition,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="alerts.occurrence.lifecycle",
            resource_type="alert_occurrence",
            resource_id=str(occurrence.id),
            metadata={
                "state": occurrence.lifecycle_state,
                "version": occurrence.version,
                "disposition": occurrence.closure_disposition,
            },
        )
        db.commit()
        db.refresh(occurrence)
        return occurrence
    except Exception as exc:
        return _raise_occurrence_error(db, exc)


@router.patch(
    "/occurrences/{occurrence_id}/snooze",
    response_model=AlertOccurrenceResponse,
)
def patch_alert_occurrence_snooze(
    occurrence_id: uuid.UUID,
    payload: AlertOccurrenceSnoozeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS, SCOPE_READ_ITEMS)),
):
    try:
        occurrence = update_alert_occurrence_snooze(
            db,
            user=user,
            occurrence_id=occurrence_id,
            expected_version=payload.expected_version,
            snoozed_until=payload.snoozed_until,
            reason=payload.reason,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="alerts.occurrence.snooze",
            resource_type="alert_occurrence",
            resource_id=str(occurrence.id),
            metadata={
                "snoozed": occurrence.snoozed_until is not None,
                "version": occurrence.version,
            },
        )
        db.commit()
        db.refresh(occurrence)
        return occurrence
    except Exception as exc:
        return _raise_occurrence_error(db, exc)


@router.patch("/{alert_id}", response_model=AlertInterestResponse)
def update_alert_interest(
    alert_id: uuid.UUID,
    payload: AlertInterestUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS)),
):
    alert = db.scalar(
        select(AlertInterest)
        .where(
            AlertInterest.id == alert_id,
            AlertInterest.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alert interest not found"
        )
    expected_row_version = _expected_alert_row_version(payload)
    if expected_row_version is not None and expected_row_version != alert.row_version:
        _raise_alert_revision_conflict(alert)

    now = datetime.now(timezone.utc)
    changed_fields: set[str] = set()
    revision_changed = False
    rule_mutated = False
    fields_set = payload.model_fields_set
    suppression_until = (
        payload.suppression_until
        if "suppression_until" in fields_set
        else alert.suppression_until
    )
    suppression_reason = (
        _normalize_optional_reason(payload.suppression_reason)
        if "suppression_reason" in fields_set
        else alert.suppression_reason
    )
    if (
        "suppression_until" in fields_set
        and payload.suppression_until is None
        and "suppression_reason" not in fields_set
    ):
        suppression_reason = None
    if {"suppression_until", "suppression_reason"} & fields_set:
        _validate_rule_suppression(suppression_until, suppression_reason)

    if payload.name is not None:
        normalized = _normalize_name(payload.name)
        if normalized != alert.name:
            alert.name = normalized
            revision_changed = True
            rule_mutated = True
            changed_fields.add("name")
    if payload.category is not None:
        normalized = _normalize_category(payload.category)
        if normalized != alert.category:
            alert.category = normalized
            revision_changed = True
            rule_mutated = True
            changed_fields.add("category")
    if payload.keywords is not None:
        normalized_keywords = _normalize_keywords(payload.keywords)
        if normalized_keywords != alert.keywords:
            alert.keywords = normalized_keywords
            revision_changed = True
            rule_mutated = True
            changed_fields.add("keywords")
    if payload.severity is not None and payload.severity != alert.severity:
        alert.severity = payload.severity
        revision_changed = True
        rule_mutated = True
        changed_fields.add("severity")

    if (
        "suppression_until" in fields_set
        and suppression_until != alert.suppression_until
    ):
        alert.suppression_until = suppression_until
        rule_mutated = True
        changed_fields.add("suppression_until")
        if (
            payload.suppression_until is None
            and "suppression_reason" not in fields_set
            and alert.suppression_reason is not None
        ):
            alert.suppression_reason = None
            rule_mutated = True
            changed_fields.add("suppression_reason")
    if (
        "suppression_reason" in fields_set
        and suppression_reason != alert.suppression_reason
    ):
        alert.suppression_reason = suppression_reason
        rule_mutated = True
        changed_fields.add("suppression_reason")

    if payload.enabled is not None and payload.enabled != alert.enabled:
        alert.enabled = payload.enabled
        rule_mutated = True
        changed_fields.add("enabled")
        if payload.enabled:
            revision_changed = True
    if revision_changed:
        alert.revision = max(1, int(alert.revision or 1)) + 1
        changed_fields.add("revision")
    if alert.enabled:
        if revision_changed or alert.durable_since is None:
            alert.durable_since = now
            rule_mutated = True
            changed_fields.add("durable_since")
    elif alert.durable_since is not None:
        alert.durable_since = None
        rule_mutated = True
        changed_fields.add("durable_since")
    if rule_mutated:
        alert.row_version = max(1, int(alert.row_version or 1)) + 1
        changed_fields.add("row_version")

    db.add(alert)
    db.flush()
    if rule_mutated and expected_row_version is None:
        _record_unversioned_alert_mutation(
            db,
            actor_user_id=user.id,
            alert=alert,
            operation="update",
        )
    record_audit(
        db,
        actor_user_id=user.id,
        action="alerts.update",
        resource_type="alert_interest",
        resource_id=str(alert.id),
        metadata={
            "name": alert.name,
            "category": alert.category,
            "keyword_count": len(alert.keywords),
            "enabled": alert.enabled,
            "severity": alert.severity,
            "revision": alert.revision,
            "row_version": alert.row_version,
            "changed_fields": sorted(changed_fields),
            "expected_row_version_supplied": expected_row_version is not None,
        },
    )
    db.commit()
    db.refresh(alert)
    return alert


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert_interest(
    alert_id: uuid.UUID,
    expected_revision: int | None = Query(default=None, ge=1),
    expected_row_version: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS)),
):
    alert = db.scalar(
        select(AlertInterest)
        .where(
            AlertInterest.id == alert_id,
            AlertInterest.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alert interest not found"
        )
    expected_version = _coalesce_expected_alert_row_version(
        expected_revision,
        expected_row_version,
    )
    if expected_version is not None and expected_version != alert.row_version:
        _raise_alert_revision_conflict(alert)

    if expected_version is None:
        _record_unversioned_alert_mutation(
            db,
            actor_user_id=user.id,
            alert=alert,
            operation="delete",
        )
    db.delete(alert)
    record_audit(
        db,
        actor_user_id=user.id,
        action="alerts.delete",
        resource_type="alert_interest",
        resource_id=str(alert_id),
        metadata={"expected_row_version_supplied": expected_version is not None},
    )
    db.commit()


def _bulk_mutate_occurrences(
    db: Session,
    *,
    user: User,
    payload: AlertOccurrenceBulkUpdate,
    target_state: str,
    audit_action: str,
):
    try:
        occurrences = bulk_update_alert_occurrence_lifecycle(
            db,
            user=user,
            entries=[
                (entry.occurrence_id, entry.expected_version) for entry in payload.items
            ],
            target_state=target_state,
            disposition=payload.disposition,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action=audit_action,
            resource_type="alert_occurrence",
            metadata={
                "count": len(occurrences),
                "state": target_state,
                "disposition": payload.disposition,
            },
        )
        db.commit()
        for occurrence in occurrences:
            db.refresh(occurrence)
        return {"items": occurrences, "updated": len(occurrences)}
    except Exception as exc:
        return _raise_occurrence_error(db, exc)


def _require_alert_admin(user: User) -> None:
    if user.role != ROLE_ADMIN:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Alert reconciliation and backfill require the administrator role.",
            error_code="alert_backfill_admin_required",
        )


def _raise_occurrence_error(db: Session, exc: Exception):
    db.rollback()
    if isinstance(exc, AlertOccurrenceNotFoundError):
        raise ApiHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            error_code=exc.code,
        ) from exc
    if isinstance(exc, AlertOccurrenceConflictError):
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            error_code=exc.code,
            headers={
                "X-Error-Code": exc.code,
                "X-Current-Version": str(exc.current_version),
            },
        ) from exc
    if isinstance(exc, AlertOccurrenceValidationError):
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
            error_code=exc.code,
        ) from exc
    raise exc


def _normalize_optional_reason(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    return normalized or None


def _expected_alert_row_version(payload: AlertInterestUpdate) -> int | None:
    return _coalesce_expected_alert_row_version(
        payload.expected_revision,
        payload.expected_row_version,
    )


def _coalesce_expected_alert_row_version(
    expected_revision: int | None,
    expected_row_version: int | None,
) -> int | None:
    if (
        expected_revision is not None
        and expected_row_version is not None
        and expected_revision != expected_row_version
    ):
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="expected_revision and expected_row_version must match when both are supplied.",
            error_code="alert_expected_version_invalid",
        )
    return (
        expected_row_version if expected_row_version is not None else expected_revision
    )


def _raise_alert_revision_conflict(alert: AlertInterest) -> None:
    row_version = max(1, int(alert.row_version or 1))
    rule_revision = max(1, int(alert.revision or 1))
    raise ApiHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": (
                "The alert rule changed after it was loaded. Refresh the rule "
                "and review the latest values before saving again."
            ),
            # current_revision remains the compatibility counterpart to expected_revision.
            "current_revision": row_version,
            "current_row_version": row_version,
            "current_rule_revision": rule_revision,
        },
        error_code="alert_revision_conflict",
        headers={
            "X-Current-Revision": str(row_version),
            "X-Current-Row-Version": str(row_version),
            "X-Current-Rule-Revision": str(rule_revision),
        },
    )


def _record_unversioned_alert_mutation(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    alert: AlertInterest,
    operation: str,
) -> None:
    logger.warning(
        "alert_rule_unversioned_mutation operation=%s alert_id=%s actor_user_id=%s row_version=%s",
        operation,
        alert.id,
        actor_user_id,
        alert.row_version,
    )
    record_audit(
        db,
        actor_user_id=actor_user_id,
        action="alerts.compatibility.unversioned_mutation",
        resource_type="alert_interest",
        resource_id=str(alert.id),
        metadata={
            "operation": operation,
            "row_version": max(1, int(alert.row_version or 1)),
            "deprecation": "expected_row_version_will_be_required",
        },
    )


def _validate_rule_suppression(until: datetime | None, reason: str | None) -> None:
    if until is None:
        if reason is not None:
            raise ApiHTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A suppression reason requires suppression_until.",
                error_code="alert_suppression_until_required",
            )
        return
    normalized_until = (
        until if until.tzinfo is not None else until.replace(tzinfo=timezone.utc)
    )
    if normalized_until <= datetime.now(timezone.utc):
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="suppression_until must be in the future.",
            error_code="alert_suppression_until_invalid",
        )
    if reason is None:
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A reason is required when suppressing alert notifications.",
            error_code="alert_suppression_reason_required",
        )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
