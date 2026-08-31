from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.access_review import (
    ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES,
    AccessReviewAssignmentSnapshot,
    AccessReviewApplyReceipt,
    AccessReviewCampaign,
    AccessReviewDecision,
    AccessReviewItem,
)
from app.models.user import User
from app.services.access_reviews import (
    AccessReviewApplyCoordinatorMissing,
    AccessReviewConflict,
    AccessReviewError,
    AccessReviewIncomplete,
    AccessReviewNotFound,
    AccessReviewRevisionConflict,
    AccessReviewStateConflict,
    current_access_review_assignment,
    require_independent_access_review_actor,
)
from app.services.authorization import database_clock, lock_iam_policy_for_mutation


_DETAIL_CODE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_RESOLVABLE_OUTCOMES = frozenset({"drifted", "failed", "manual_action_required"})


@dataclass(frozen=True)
class AccessReviewApplyContext:
    campaign: AccessReviewCampaign
    item: AccessReviewItem
    decision: AccessReviewDecision
    actor: User
    current_assignment: AccessReviewAssignmentSnapshot


@dataclass(frozen=True)
class AccessReviewMutationResult:
    mutation_performed: bool
    detail_code: str = "assignment_revoked"
    detail: str = "The reviewed assignment was removed."
    result_snapshot: dict[str, object] | None = None


@dataclass(frozen=True)
class AccessReviewApplyResult:
    receipt: AccessReviewApplyReceipt
    changed: bool
    campaign_revision: int


AccessReviewMutationCoordinator = Callable[
    [Session, AccessReviewApplyContext], AccessReviewMutationResult
]


class _CoordinatorPostconditionFailed(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def apply_access_review_item(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    item_id: uuid.UUID,
    actor: User,
    expected_revision: int,
    expected_item_fingerprint: str,
    coordinator: AccessReviewMutationCoordinator | None = None,
) -> AccessReviewApplyResult:
    # Every apply revalidation is serialized with the IAM mutation paths.
    lock_iam_policy_for_mutation(db)
    campaign = _lock_campaign_for_item_apply(db, campaign_id)
    item = _lock_item(db, campaign.id, item_id)
    _require_item_fingerprint(item, expected_item_fingerprint)
    latest_receipt = _latest_receipt_for_item(db, item.id)
    if (
        latest_receipt is not None
        and latest_receipt.outcome in ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES
    ):
        return AccessReviewApplyResult(
            receipt=latest_receipt,
            changed=False,
            campaign_revision=campaign.revision,
        )
    _require_apply_preconditions(
        campaign,
        item,
        expected_revision=expected_revision,
        expected_item_fingerprint=expected_item_fingerprint,
    )
    decision = _latest_decision_for_item(db, item.id)
    if decision is None:
        raise AccessReviewIncomplete(
            "This review item has no decision and cannot be applied."
        )

    now = _database_now(db)
    attempt = (latest_receipt.attempt if latest_receipt is not None else 0) + 1
    current = current_access_review_assignment(db, item=item, now=now, lock=True)
    result_snapshot: dict[str, object] = {}
    if current is None:
        outcome, observed, mutated, code, detail = (
            "already_absent",
            None,
            False,
            "assignment_already_absent",
            "The reviewed assignment no longer exists; no access mutation was needed.",
        )
    elif not current.matches_item(item):
        outcome, observed, mutated, code, detail = (
            "drifted",
            current,
            False,
            "assignment_drifted",
            "The assignment or its effective access changed after the campaign snapshot. Review the current state before resolving this item.",
        )
    elif decision.decision == "retain":
        outcome, observed, mutated, code, detail = (
            "retained",
            current,
            False,
            "assignment_retained",
            "The reviewer retained this assignment; no access was changed.",
        )
    elif _requires_manual_action(item):
        outcome, observed, mutated, code, detail = (
            "manual_action_required",
            current,
            False,
            "external_access_change_required",
            _manual_action_detail(item),
        )
    else:
        if coordinator is None:
            raise AccessReviewApplyCoordinatorMissing(
                "This revoke decision passed revalidation, but no access-reduction coordinator was supplied. No access was changed and no receipt was recorded."
            )
        try:
            with db.begin_nested():
                result = coordinator(
                    db,
                    AccessReviewApplyContext(
                        campaign=campaign,
                        item=item,
                        decision=decision,
                        actor=actor,
                        current_assignment=current,
                    ),
                )
                _validate_mutation_result(result)
                db.flush()
                remaining = current_access_review_assignment(
                    db, item=item, now=now, lock=False
                )
                if remaining is not None:
                    raise _CoordinatorPostconditionFailed(
                        "assignment_still_present",
                        "The access-reduction coordinator returned, but the exact assignment is still present. Its transaction was rolled back.",
                    )
                if not result.mutation_performed:
                    raise _CoordinatorPostconditionFailed(
                        "coordinator_result_inconsistent",
                        "The coordinator removed the assignment without confirming the mutation. Its transaction was rolled back.",
                    )
        except (_CoordinatorPostconditionFailed, AccessReviewError) as exc:
            failure_code = _coordinator_failure_code(exc)
            failure_detail = _coordinator_failure_detail(exc)
            outcome, observed, mutated, code, detail = (
                "failed",
                current,
                False,
                failure_code,
                failure_detail,
            )
            result_snapshot = _coordinator_failure_snapshot(exc, failure_code)
        else:
            outcome, observed, mutated, code, detail = (
                "revoked",
                current,
                True,
                result.detail_code,
                result.detail,
            )
            result_snapshot = dict(result.result_snapshot or {})

    receipt = _new_receipt(
        campaign,
        item,
        decision,
        actor,
        attempt=attempt,
        outcome=outcome,
        current=observed,
        mutation_performed=mutated,
        detail_code=code,
        detail=detail,
        result_snapshot=result_snapshot,
        now=now,
    )
    db.add(receipt)
    db.flush()
    return AccessReviewApplyResult(
        receipt=receipt,
        changed=True,
        campaign_revision=campaign.revision,
    )


def resolve_access_review_item(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    item_id: uuid.UUID,
    actor: User,
    expected_revision: int,
    expected_item_fingerprint: str,
    expected_receipt_attempt: int,
    reason: str,
) -> AccessReviewApplyReceipt:
    lock_iam_policy_for_mutation(db)
    campaign = _lock_campaign_for_item_apply(db, campaign_id)
    item = _lock_item(db, campaign.id, item_id)
    _require_apply_preconditions(
        campaign,
        item,
        expected_revision=expected_revision,
        expected_item_fingerprint=expected_item_fingerprint,
    )
    require_independent_access_review_actor(
        db,
        items=[item],
        actor_id=actor.id,
        operation_label="resolve",
    )
    latest_receipt = _latest_receipt_for_item(db, item.id)
    if latest_receipt is None or latest_receipt.attempt != expected_receipt_attempt:
        raise AccessReviewConflict(
            "The apply result changed after it was loaded. Reload the item before resolving it."
        )
    if latest_receipt.outcome not in _RESOLVABLE_OUTCOMES:
        raise AccessReviewStateConflict(
            f"A {latest_receipt.outcome} apply result cannot be superseded."
        )
    decision = _latest_decision_for_item(db, item.id)
    if decision is None:
        raise AccessReviewIncomplete(
            "This review item has no decision and cannot be resolved."
        )
    now = _database_now(db)
    current = current_access_review_assignment(db, item=item, now=now, lock=False)
    receipt = _new_receipt(
        campaign,
        item,
        decision,
        actor,
        attempt=latest_receipt.attempt + 1,
        outcome="superseded",
        current=current,
        mutation_performed=False,
        detail_code="review_item_superseded",
        detail=reason,
        result_snapshot={
            "previous_receipt_id": str(latest_receipt.id),
            "previous_outcome": latest_receipt.outcome,
            "resolution_reason": reason,
        },
        now=now,
    )
    db.add(receipt)
    db.flush()
    return receipt


def _lock_campaign_for_item_apply(
    db: Session, campaign_id: uuid.UUID
) -> AccessReviewCampaign:
    campaign = db.scalar(
        select(AccessReviewCampaign)
        .where(AccessReviewCampaign.id == campaign_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if campaign is None:
        raise AccessReviewNotFound("Access-review campaign not found.")
    return campaign


def _lock_item(
    db: Session, campaign_id: uuid.UUID, item_id: uuid.UUID
) -> AccessReviewItem:
    item = db.scalar(
        select(AccessReviewItem)
        .where(
            AccessReviewItem.id == item_id,
            AccessReviewItem.campaign_id == campaign_id,
        )
        .with_for_update()
    )
    if item is None:
        raise AccessReviewNotFound("Access-review item not found in this campaign.")
    return item


def _require_apply_preconditions(
    campaign: AccessReviewCampaign,
    item: AccessReviewItem,
    *,
    expected_revision: int,
    expected_item_fingerprint: str,
) -> None:
    if campaign.revision != expected_revision:
        raise AccessReviewRevisionConflict(campaign)
    if campaign.status != "applying" or campaign.apply_run_id is None:
        raise AccessReviewStateConflict(
            f"Review items can be applied only while a campaign is applying; this campaign is {campaign.status}."
        )
    _require_item_fingerprint(item, expected_item_fingerprint)


def _require_item_fingerprint(
    item: AccessReviewItem, expected_item_fingerprint: str
) -> None:
    if item.assignment_fingerprint != expected_item_fingerprint:
        raise AccessReviewConflict(
            "The review item fingerprint does not match this campaign snapshot. Reload the item and retry."
        )


def _latest_decision_for_item(
    db: Session, item_id: uuid.UUID
) -> AccessReviewDecision | None:
    return db.scalar(
        select(AccessReviewDecision)
        .where(AccessReviewDecision.item_id == item_id)
        .order_by(AccessReviewDecision.sequence.desc())
        .limit(1)
    )


def _latest_receipt_for_item(
    db: Session, item_id: uuid.UUID
) -> AccessReviewApplyReceipt | None:
    return db.scalar(
        select(AccessReviewApplyReceipt)
        .where(AccessReviewApplyReceipt.item_id == item_id)
        .order_by(AccessReviewApplyReceipt.attempt.desc())
        .limit(1)
    )


def _requires_manual_action(item: AccessReviewItem) -> bool:
    return item.assignment_source in {"oidc", "legacy"} or item.item_type in {
        "oidc_role_mapping",
        "oidc_group_mapping",
        "legacy_user_role",
    }


def _manual_action_detail(item: AccessReviewItem) -> str:
    if item.item_type == "legacy_user_role":
        return (
            "The built-in compatibility role cannot be removed. Change the user's "
            "base role through user administration, then retry or supersede this item."
        )
    if item.item_type in {"oidc_role_mapping", "oidc_group_mapping"}:
        return (
            "This access originates in ThreatLens OIDC mapping policy. Update the "
            "mapping in OIDC settings, then retry or supersede this item."
        )
    return (
        "This assignment is managed by the identity provider. Change the user's "
        "upstream claim or the ThreatLens OIDC mapping, then retry or supersede this item."
    )


def _new_receipt(
    campaign: AccessReviewCampaign,
    item: AccessReviewItem,
    decision: AccessReviewDecision,
    actor: User,
    *,
    attempt: int,
    outcome: str,
    current: AccessReviewAssignmentSnapshot | None,
    mutation_performed: bool,
    detail_code: str,
    detail: str,
    result_snapshot: dict[str, object],
    now: datetime,
) -> AccessReviewApplyReceipt:
    _validate_receipt_evidence(
        detail_code=detail_code,
        detail=detail,
        result_snapshot=result_snapshot,
    )
    return AccessReviewApplyReceipt(
        campaign_id=campaign.id,
        item_id=item.id,
        item_fingerprint=item.assignment_fingerprint,
        decision_id=decision.id,
        apply_run_id=campaign.apply_run_id,
        attempt=attempt,
        outcome=outcome,
        expected_assignment_revision=item.assignment_revision_snapshot,
        observed_assignment_revision=(
            current.assignment_revision if current is not None else None
        ),
        expected_target_revision=item.target_revision_snapshot,
        observed_target_revision=(
            current.target_revision if current is not None else None
        ),
        observed_fingerprint=current.fingerprint if current is not None else None,
        mutation_performed=mutation_performed,
        detail_code=detail_code,
        detail=detail,
        result_snapshot=result_snapshot,
        applied_by_user_id=actor.id,
        applied_by_email_snapshot=actor.email,
        created_at=now,
    )


def _coordinator_failure_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _DETAIL_CODE.fullmatch(code):
        return code
    return "coordinator_failed"


def _coordinator_failure_detail(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if not isinstance(detail, str):
        detail = str(exc)
    detail = detail.strip()
    if len(detail) < 3:
        detail = "The access-reduction coordinator could not apply this review item."
    if len(detail) > 2_000:
        detail = detail[:1_997].rstrip() + "..."
    return detail


def _coordinator_failure_snapshot(exc: Exception, error_code: str) -> dict[str, object]:
    raw_context = getattr(exc, "context", None)
    safe_context: dict[str, object] = {}
    if isinstance(raw_context, dict):
        for key in (
            "reason",
            "item_type",
            "assignment_id",
            "affected_investigation_count",
        ):
            value = raw_context.get(key)
            if isinstance(value, (str, int, bool)):
                safe_context[key] = value
    snapshot: dict[str, object] = {"error_code": error_code}
    if safe_context:
        snapshot["error_context"] = safe_context
    return snapshot


def _validate_mutation_result(result: AccessReviewMutationResult) -> None:
    if not isinstance(result, AccessReviewMutationResult):
        raise AccessReviewError(
            "The access-reduction coordinator returned an invalid result. Its transaction was rolled back."
        )
    if not result.mutation_performed:
        return
    if not _DETAIL_CODE.fullmatch(result.detail_code):
        raise AccessReviewError(
            "The access-reduction coordinator returned an invalid detail code. Its transaction was rolled back."
        )
    detail = result.detail.strip()
    if not 3 <= len(detail) <= 2_000 or detail != result.detail:
        raise AccessReviewError(
            "The access-reduction coordinator returned an invalid detail message. Its transaction was rolled back."
        )
    snapshot = result.result_snapshot
    if not isinstance(snapshot, dict):
        raise AccessReviewError(
            "The access-reduction coordinator omitted its result evidence. Its transaction was rolled back."
        )
    revision = snapshot.get("iam_policy_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise AccessReviewError(
            "The access-reduction coordinator omitted a valid IAM policy revision. Its transaction was rolled back."
        )
    for key in (
        "revoked_api_tokens",
        "revoked_auth_sessions",
        "cancelled_pending_mfa_enrollments",
        "cleared_investigation_assignments",
    ):
        value = snapshot.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AccessReviewError(
                "The access-reduction coordinator returned invalid revocation evidence. Its transaction was rolled back."
            )


def _validate_receipt_evidence(
    *,
    detail_code: str,
    detail: str,
    result_snapshot: dict[str, object],
) -> None:
    if not _DETAIL_CODE.fullmatch(detail_code):
        raise AccessReviewError(
            "The access-review apply result has an invalid detail code. No receipt was recorded."
        )
    if not 3 <= len(detail) <= 2_000 or detail != detail.strip():
        raise AccessReviewError(
            "The access-review apply result has an invalid detail message. No receipt was recorded."
        )
    try:
        encoded = json.dumps(
            result_snapshot,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AccessReviewError(
            "The access-review apply result contains non-JSON evidence. No receipt was recorded."
        ) from exc
    if len(encoded) > 65_536:
        raise AccessReviewError(
            "The access-review apply result exceeds the 65,536-byte evidence limit. No receipt was recorded."
        )


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(database_clock(db)))
    if not isinstance(value, datetime):
        raise AccessReviewError(
            "The database clock could not be read. No access-review state was changed."
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "AccessReviewApplyContext",
    "AccessReviewApplyResult",
    "AccessReviewMutationCoordinator",
    "AccessReviewMutationResult",
    "apply_access_review_item",
    "resolve_access_review_item",
]
