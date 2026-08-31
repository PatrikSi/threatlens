from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import ColumnElement, delete, false, func, select, true
from sqlalchemy.orm import Session

from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.data_policy import (
    DataAccessEnvelopeLabel,
    DataPolicyRoleGrant,
    DataPolicyState,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.iam import IAMRole
from app.schemas.data_policy import (
    DataPolicyBlockerResponse,
    DataPolicyMode,
    DataPolicyModeUpdateResponse,
    DataPolicyOverviewResponse,
    DataPolicyPreflightResponse,
    DataPolicyStateResponse,
    FeedHandlingLabelAssignmentResponse,
    HandlingLabelCreateRequest,
    HandlingLabelMutationResponse,
    HandlingLabelResponse,
    HandlingLabelRoleGrantsRequest,
    HandlingLabelStatusRequest,
    HandlingLabelUpdateRequest,
)
from app.services.authorization import AuthorizationContext


# Migration 0070 raises both values after every read and egress surface has been
# wired. Keeping this at zero makes the foundation safe to ship incrementally.
APPLICATION_DATA_POLICY_COVERAGE_VERSION = 0
REQUIRED_ENFORCEMENT_COVERAGE_VERSION = 1
_DATA_POLICY_SNAPSHOT_ATTEMPTS = 3


class DataPolicyError(RuntimeError):
    code = "data_policy_error"
    status_code = 409

    def __init__(
        self,
        detail: str,
        *,
        context: dict[str, object] | None = None,
        current_revision: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.context = context
        self.current_revision = current_revision


class DataPolicyUnavailable(DataPolicyError):
    code = "data_policy_unavailable"
    status_code = 503


class DataPolicyRevisionConflict(DataPolicyError):
    code = "data_policy_revision_conflict"
    status_code = 409


class HandlingLabelRevisionConflict(DataPolicyError):
    code = "handling_label_revision_conflict"
    status_code = 409


class HandlingLabelNotFound(DataPolicyError):
    code = "handling_label_not_found"
    status_code = 404


class DataPolicyFeedNotFound(DataPolicyError):
    code = "data_policy_feed_not_found"
    status_code = 404


class DataPolicyValidationError(DataPolicyError):
    code = "data_policy_validation_failed"
    status_code = 422


class DataPolicyConflict(DataPolicyError):
    code = "data_policy_conflict"
    status_code = 409


class DataPolicyActivationBlocked(DataPolicyError):
    code = "data_policy_activation_blocked"
    status_code = 409


@dataclass(frozen=True)
class DataAccessContext:
    mode: DataPolicyMode
    policy_revision: int
    coverage_version: int
    principal_type: str
    principal_id: uuid.UUID
    principal_eligible: bool
    allowed_label_ids: frozenset[uuid.UUID]

    @property
    def enforced(self) -> bool:
        return self.mode == "enforced"

    @property
    def auditing(self) -> bool:
        return self.mode == "audit"

    def allows(self, label_id: uuid.UUID) -> bool:
        if not self.principal_eligible:
            return False
        if self.mode != "enforced":
            return True
        return label_id in self.allowed_label_ids

    def would_deny(self, label_id: uuid.UUID) -> bool:
        return self.mode == "audit" and (
            not self.principal_eligible or label_id not in self.allowed_label_ids
        )


def data_policy_overview(db: Session) -> DataPolicyOverviewResponse:
    state = _policy_state(db)
    labels = _label_responses(db)
    return DataPolicyOverviewResponse(
        state=_state_response(state),
        labels=labels,
        preflight=data_policy_preflight(db, state=state),
    )


def get_handling_label(db: Session, label_id: uuid.UUID) -> HandlingLabelResponse:
    label = db.get(HandlingLabel, label_id)
    if label is None:
        raise HandlingLabelNotFound("Handling label not found.")
    return _label_response(db, label)


def current_data_policy_revision(db: Session) -> int:
    return _policy_revision(db)


def data_policy_preflight(
    db: Session, *, state: DataPolicyState | None = None
) -> DataPolicyPreflightResponse:
    current_state = state or _policy_state(db)
    blockers: list[DataPolicyBlockerResponse] = []

    effective_coverage = min(
        current_state.coverage_version,
        APPLICATION_DATA_POLICY_COVERAGE_VERSION,
    )
    if effective_coverage < REQUIRED_ENFORCEMENT_COVERAGE_VERSION:
        blockers.append(
            DataPolicyBlockerResponse(
                code="coverage_incomplete",
                detail=(
                    "The installed application has not yet declared complete data-policy "
                    "coverage across reads, derived artifacts, and outbound delivery."
                ),
            )
        )

    unrestricted = db.get(HandlingLabel, UNRESTRICTED_HANDLING_LABEL_ID)
    unrestricted_valid = bool(
        unrestricted is not None
        and unrestricted.key == "unrestricted"
        and unrestricted.is_unrestricted
        and unrestricted.is_system
        and unrestricted.is_active
    )
    if not unrestricted_valid:
        blockers.append(
            DataPolicyBlockerResponse(
                code="unrestricted_label_invalid",
                detail=(
                    "The required unrestricted handling label is missing or invalid. "
                    "Restore it from a known-good backup before enabling policy."
                ),
            )
        )

    quarantine = db.get(HandlingLabel, QUARANTINE_HANDLING_LABEL_ID)
    quarantine_valid = bool(
        quarantine is not None
        and quarantine.key == "quarantine"
        and not quarantine.is_unrestricted
        and quarantine.is_system
        and quarantine.is_active
    )
    if not quarantine_valid:
        blockers.append(
            DataPolicyBlockerResponse(
                code="quarantine_label_invalid",
                detail=(
                    "The required quarantine handling label is missing or invalid. "
                    "Restore it from a known-good backup before enabling policy."
                ),
            )
        )

    inactive_feed_count = int(
        db.scalar(
            select(func.count(Feed.id))
            .join(HandlingLabel, HandlingLabel.id == Feed.handling_label_id)
            .where(HandlingLabel.is_active.is_(False))
        )
        or 0
    )
    if inactive_feed_count:
        blockers.append(
            DataPolicyBlockerResponse(
                code="feeds_use_inactive_labels",
                detail="One or more feeds use archived handling labels.",
                count=inactive_feed_count,
            )
        )

    admin_role_id = SYSTEM_ROLE_IDS["admin"]
    missing_admin_grant_count = int(
        db.scalar(
            select(func.count(HandlingLabel.id)).where(
                HandlingLabel.is_active.is_(True),
                HandlingLabel.is_unrestricted.is_(False),
                ~select(DataPolicyRoleGrant.label_id)
                .where(
                    DataPolicyRoleGrant.label_id == HandlingLabel.id,
                    DataPolicyRoleGrant.role_id == admin_role_id,
                )
                .exists(),
            )
        )
        or 0
    )
    if missing_admin_grant_count:
        blockers.append(
            DataPolicyBlockerResponse(
                code="restricted_labels_missing_admin_grant",
                detail=(
                    "Every active restricted label must explicitly grant the built-in "
                    "administrator role to preserve a recovery path."
                ),
                count=missing_admin_grant_count,
            )
        )

    labels_without_grants_count = int(
        db.scalar(
            select(func.count(HandlingLabel.id)).where(
                HandlingLabel.is_active.is_(True),
                HandlingLabel.is_unrestricted.is_(False),
                ~select(DataPolicyRoleGrant.label_id)
                .where(DataPolicyRoleGrant.label_id == HandlingLabel.id)
                .exists(),
            )
        )
        or 0
    )
    if labels_without_grants_count:
        blockers.append(
            DataPolicyBlockerResponse(
                code="restricted_labels_without_roles",
                detail="Every active restricted label must grant at least one role.",
                count=labels_without_grants_count,
            )
        )

    audit_blocker_codes = {
        "coverage_incomplete",
        "unrestricted_label_invalid",
        "quarantine_label_invalid",
        "feeds_use_inactive_labels",
    }
    ready_for_audit = not any(
        blocker.code in audit_blocker_codes for blocker in blockers
    )
    return DataPolicyPreflightResponse(
        ready_for_audit=ready_for_audit,
        ready_for_enforcement=not blockers,
        current_coverage_version=effective_coverage,
        required_coverage_version=REQUIRED_ENFORCEMENT_COVERAGE_VERSION,
        blockers=blockers,
    )


def create_handling_label(
    db: Session,
    *,
    payload: HandlingLabelCreateRequest,
    actor_user_id: uuid.UUID,
) -> HandlingLabelMutationResponse:
    state = _lock_policy_state(db)
    _assert_policy_revision(state, payload.expected_policy_revision)
    if payload.key in {"unrestricted", "quarantine"}:
        raise DataPolicyValidationError(
            "This key is reserved for a built-in handling label."
        )
    existing = db.scalar(
        select(HandlingLabel.id).where(HandlingLabel.key == payload.key)
    )
    if existing is not None:
        raise DataPolicyConflict(
            "A handling label with this key already exists.",
            context={"key": payload.key},
        )

    role_ids = set(payload.role_ids)
    role_ids.add(SYSTEM_ROLE_IDS["admin"])
    _validate_role_ids(db, role_ids)
    label = HandlingLabel(
        key=payload.key,
        name=_required_text(payload.name, field="name"),
        description=_optional_text(payload.description),
        color=payload.color.upper(),
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
        created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    db.add(label)
    db.flush()
    for role_id in sorted(role_ids, key=str):
        db.add(
            DataPolicyRoleGrant(
                label_id=label.id,
                role_id=role_id,
                granted_by_user_id=actor_user_id,
            )
        )
    _bump_policy_state(state, actor_user_id=actor_user_id)
    db.flush()
    db.refresh(label)
    return HandlingLabelMutationResponse(
        label=_label_response(db, label),
        policy_revision=state.revision,
        changed=True,
    )


def update_handling_label(
    db: Session,
    *,
    label_id: uuid.UUID,
    payload: HandlingLabelUpdateRequest,
    actor_user_id: uuid.UUID,
) -> HandlingLabelMutationResponse:
    state = _lock_policy_state(db)
    label = _lock_label(db, label_id)
    _assert_label_revision(
        label,
        payload.expected_revision,
        policy_revision=state.revision,
    )

    updates = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
    if label.is_system and updates:
        raise DataPolicyConflict("Built-in handling labels cannot be changed.")
    if "name" in updates:
        updates["name"] = _required_text(updates["name"], field="name")
    if "description" in updates:
        updates["description"] = _optional_text(updates["description"])
    if "color" in updates:
        updates["color"] = updates["color"].upper()
    changed = any(getattr(label, key) != value for key, value in updates.items())
    if changed:
        for key, value in updates.items():
            setattr(label, key, value)
        label.revision += 1
        label.updated_by_user_id = actor_user_id
        _bump_policy_state(state, actor_user_id=actor_user_id)
        db.add(label)
        db.flush()
        db.refresh(label)
    return HandlingLabelMutationResponse(
        label=_label_response(db, label),
        policy_revision=state.revision,
        changed=changed,
    )


def replace_handling_label_role_grants(
    db: Session,
    *,
    label_id: uuid.UUID,
    payload: HandlingLabelRoleGrantsRequest,
    actor_user_id: uuid.UUID,
) -> HandlingLabelMutationResponse:
    state = _lock_policy_state(db)
    label = _lock_label(db, label_id)
    _assert_label_revision(
        label,
        payload.expected_revision,
        policy_revision=state.revision,
    )
    if label.is_system:
        raise DataPolicyConflict("Built-in handling-label grants cannot be changed.")
    if not label.is_active:
        raise DataPolicyConflict(
            "Restore this handling label before changing its role grants."
        )

    requested_role_ids = set(payload.role_ids)
    admin_role_id = SYSTEM_ROLE_IDS["admin"]
    if admin_role_id not in requested_role_ids:
        raise DataPolicyValidationError(
            "Restricted labels must explicitly grant the built-in administrator role.",
            context={"required_role_id": str(admin_role_id)},
        )
    _validate_role_ids(db, requested_role_ids)
    existing_role_ids = set(
        db.scalars(
            select(DataPolicyRoleGrant.role_id).where(
                DataPolicyRoleGrant.label_id == label.id
            )
        ).all()
    )
    changed = requested_role_ids != existing_role_ids
    if changed:
        db.execute(
            delete(DataPolicyRoleGrant).where(DataPolicyRoleGrant.label_id == label.id)
        )
        for role_id in sorted(requested_role_ids, key=str):
            db.add(
                DataPolicyRoleGrant(
                    label_id=label.id,
                    role_id=role_id,
                    granted_by_user_id=actor_user_id,
                )
            )
        label.revision += 1
        label.updated_by_user_id = actor_user_id
        _bump_policy_state(state, actor_user_id=actor_user_id)
        db.add(label)
        db.flush()
        db.refresh(label)
    return HandlingLabelMutationResponse(
        label=_label_response(db, label),
        policy_revision=state.revision,
        changed=changed,
    )


def set_handling_label_status(
    db: Session,
    *,
    label_id: uuid.UUID,
    payload: HandlingLabelStatusRequest,
    actor_user_id: uuid.UUID,
) -> HandlingLabelMutationResponse:
    state = _lock_policy_state(db)
    label = _lock_label(db, label_id)
    _assert_label_revision(
        label,
        payload.expected_revision,
        policy_revision=state.revision,
    )
    if label.is_system:
        raise DataPolicyConflict("Built-in handling labels cannot be archived.")
    if label.is_active == payload.active:
        return HandlingLabelMutationResponse(
            label=_label_response(db, label),
            policy_revision=state.revision,
            changed=False,
        )
    if not payload.active:
        assigned_feed_count = int(
            db.scalar(
                select(func.count(Feed.id)).where(Feed.handling_label_id == label.id)
            )
            or 0
        )
        if assigned_feed_count:
            raise DataPolicyConflict(
                "Reassign every feed using this handling label before archiving it.",
                context={"assigned_feed_count": assigned_feed_count},
            )
        derived_reference_count = int(
            db.scalar(
                select(func.count(DataAccessEnvelopeLabel.envelope_id)).where(
                    DataAccessEnvelopeLabel.label_id == label.id
                )
            )
            or 0
        )
        from app.models.alert_occurrence import AlertOccurrenceMetricCohortLabel
        from app.models.integration import IntegrationDeliveryMetricCohortLabel

        metric_reference_count = int(
            db.scalar(
                select(func.count(AlertOccurrenceMetricCohortLabel.cohort_id)).where(
                    AlertOccurrenceMetricCohortLabel.label_id == label.id
                )
            )
            or 0
        )
        derived_reference_count += metric_reference_count
        integration_metric_reference_count = int(
            db.scalar(
                select(
                    func.count(IntegrationDeliveryMetricCohortLabel.cohort_id)
                ).where(
                    IntegrationDeliveryMetricCohortLabel.label_id == label.id
                )
            )
            or 0
        )
        derived_reference_count += integration_metric_reference_count
        if derived_reference_count:
            raise DataPolicyConflict(
                "This handling label is retained by derived intelligence. Keep it active or remove the derived records first.",
                context={"derived_reference_count": derived_reference_count},
            )
    else:
        role_ids = set(
            db.scalars(
                select(DataPolicyRoleGrant.role_id).where(
                    DataPolicyRoleGrant.label_id == label.id
                )
            ).all()
        )
        if SYSTEM_ROLE_IDS["admin"] not in role_ids:
            raise DataPolicyConflict(
                "Restore requires an explicit built-in administrator role grant."
            )

    label.is_active = payload.active
    label.revision += 1
    label.updated_by_user_id = actor_user_id
    _bump_policy_state(state, actor_user_id=actor_user_id)
    db.add(label)
    db.flush()
    db.refresh(label)
    return HandlingLabelMutationResponse(
        label=_label_response(db, label),
        policy_revision=state.revision,
        changed=True,
    )


def assign_feed_handling_label(
    db: Session,
    *,
    feed_id: uuid.UUID,
    handling_label_id: uuid.UUID,
    expected_policy_revision: int,
    actor_user_id: uuid.UUID,
) -> FeedHandlingLabelAssignmentResponse:
    state = _lock_policy_state(db)
    _assert_policy_revision(state, expected_policy_revision)
    feed = db.scalar(
        select(Feed)
        .where(Feed.id == feed_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if feed is None:
        raise DataPolicyFeedNotFound("Feed not found.")
    label = db.scalar(
        select(HandlingLabel).where(HandlingLabel.id == handling_label_id)
    )
    if label is None or not label.is_active:
        raise HandlingLabelNotFound(
            "Active handling label not found.",
            context={"handling_label_id": str(handling_label_id)},
        )
    previous_handling_label_id = feed.handling_label_id
    changed = previous_handling_label_id != label.id
    if changed:
        from app.services.data_access_envelopes import (
            taint_data_access_envelopes_for_feed,
        )
        from app.services.alert_metric_data_policy import (
            taint_alert_occurrence_metrics_for_feed,
        )
        from app.services.integration_metric_data_policy import (
            taint_integration_delivery_metrics_for_feed,
        )

        feed.handling_label_id = label.id
        db.add(feed)
        _bump_policy_state(state, actor_user_id=actor_user_id)
        db.flush()
        taint_data_access_envelopes_for_feed(
            db,
            feed_id=feed.id,
            handling_label_id=label.id,
            policy_revision=state.revision,
        )
        taint_alert_occurrence_metrics_for_feed(
            db,
            feed_id=feed.id,
            handling_label_id=label.id,
        )
        taint_integration_delivery_metrics_for_feed(
            db,
            feed_id=feed.id,
            handling_label_id=label.id,
        )
    return FeedHandlingLabelAssignmentResponse(
        feed_id=feed.id,
        previous_handling_label_id=previous_handling_label_id,
        handling_label_id=feed.handling_label_id,
        policy_revision=state.revision,
        changed=changed,
    )


def update_data_policy_mode(
    db: Session,
    *,
    mode: DataPolicyMode,
    expected_revision: int,
    actor_user_id: uuid.UUID,
) -> DataPolicyModeUpdateResponse:
    state = _lock_policy_state(db)
    _assert_policy_revision(state, expected_revision)
    preflight = data_policy_preflight(db, state=state)
    if mode == "audit" and not preflight.ready_for_audit:
        raise DataPolicyActivationBlocked(
            "Data-policy audit mode cannot be enabled until the preflight blockers are resolved.",
            context={"blockers": [item.model_dump() for item in preflight.blockers]},
        )
    if mode == "enforced" and not preflight.ready_for_enforcement:
        raise DataPolicyActivationBlocked(
            "Data-policy enforcement cannot be enabled until every preflight blocker is resolved.",
            context={"blockers": [item.model_dump() for item in preflight.blockers]},
        )
    changed = state.mode != mode
    if changed:
        state.mode = mode
        if mode == "enforced":
            state.enforced_at = datetime.now(timezone.utc)
            state.enforced_by_user_id = actor_user_id
        else:
            state.enforced_at = None
            state.enforced_by_user_id = None
        _bump_policy_state(state, actor_user_id=actor_user_id)
        db.flush()
        db.refresh(state)
    return DataPolicyModeUpdateResponse(
        state=_state_response(state),
        changed=changed,
        preflight=preflight,
    )


def data_access_context_for_authorization(
    db: Session, authorization: AuthorizationContext
) -> DataAccessContext:
    for _attempt in range(_DATA_POLICY_SNAPSHOT_ATTEMPTS):
        state_before = _policy_state(db)
        revision_before = state_before.revision
        mode = state_before.mode
        coverage_version = state_before.coverage_version
        if mode in {"audit", "enforced"} and (
            coverage_version < REQUIRED_ENFORCEMENT_COVERAGE_VERSION
            or APPLICATION_DATA_POLICY_COVERAGE_VERSION < coverage_version
        ):
            raise DataPolicyUnavailable(
                "Data access policy coverage is incompatible with this application process. Complete the deployment or disable policy enforcement.",
                context={
                    "mode": mode,
                    "database_coverage_version": coverage_version,
                    "application_coverage_version": APPLICATION_DATA_POLICY_COVERAGE_VERSION,
                },
            )

        if mode in {"audit", "enforced"}:
            preflight = data_policy_preflight(db, state=state_before)
            ready = (
                preflight.ready_for_enforcement
                if mode == "enforced"
                else preflight.ready_for_audit
            )
            if not ready:
                raise DataPolicyUnavailable(
                    "Data access policy invariants are invalid. Policy evaluation is disabled until an administrator repairs or disables the policy.",
                    context={
                        "mode": mode,
                        "blockers": [
                            blocker.model_dump() for blocker in preflight.blockers
                        ],
                    },
                )

        role_ids = {
            role.id
            for role in authorization.roles
            if role.id is not None and not role.source.startswith("elevation:")
        }
        allowed_label_ids = {UNRESTRICTED_HANDLING_LABEL_ID}
        if authorization.account_eligible and role_ids:
            allowed_label_ids.update(
                db.scalars(
                    select(DataPolicyRoleGrant.label_id)
                    .join(
                        HandlingLabel,
                        HandlingLabel.id == DataPolicyRoleGrant.label_id,
                    )
                    .where(
                        DataPolicyRoleGrant.role_id.in_(role_ids),
                        HandlingLabel.is_active.is_(True),
                    )
                ).all()
            )
        revision_after = _policy_revision(db)
        if revision_before == revision_after:
            return DataAccessContext(
                mode=mode,
                policy_revision=revision_before,
                coverage_version=coverage_version,
                principal_type=authorization.principal_type,
                principal_id=authorization.principal_id,
                principal_eligible=authorization.account_eligible,
                allowed_label_ids=(
                    frozenset(allowed_label_ids)
                    if authorization.account_eligible
                    else frozenset()
                ),
            )
    raise DataPolicyUnavailable(
        "Data access policy changed repeatedly while access was evaluated. Retry the request."
    )


def handling_label_access_predicate(
    label_column, context: DataAccessContext
) -> ColumnElement[bool]:
    if not context.principal_eligible:
        return false()
    if not context.enforced:
        return true()
    return label_column.in_(context.allowed_label_ids)


def fence_data_access_context(
    db: Session,
    context: DataAccessContext,
) -> None:
    """Hold the policy revision stable for the caller's transaction."""

    current_revision = db.scalar(
        select(DataPolicyState.revision)
        .where(DataPolicyState.id == 1)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if current_revision is None:
        raise DataPolicyUnavailable(
            "Data policy state is missing. Restore the singleton row from a known-good backup before serving data."
        )
    if int(current_revision) != context.policy_revision:
        raise DataPolicyRevisionConflict(
            "Data access policy changed while the request was being authorized. Retry the request.",
            current_revision=int(current_revision),
            context={
                "expected_revision": context.policy_revision,
                "current_revision": int(current_revision),
            },
        )


def _policy_state(db: Session) -> DataPolicyState:
    state = db.scalar(
        select(DataPolicyState)
        .where(DataPolicyState.id == 1)
        .execution_options(populate_existing=True)
    )
    if state is None:
        raise DataPolicyUnavailable(
            "Data policy state is missing. Restore the singleton row from a known-good backup before serving data."
        )
    return state


def _policy_revision(db: Session) -> int:
    revision = db.scalar(
        select(DataPolicyState.revision).where(DataPolicyState.id == 1)
    )
    if revision is None:
        raise DataPolicyUnavailable(
            "Data policy state is missing. Restore the singleton row from a known-good backup before serving data."
        )
    return int(revision)


def _lock_policy_state(db: Session) -> DataPolicyState:
    state = db.scalar(
        select(DataPolicyState)
        .where(DataPolicyState.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is None:
        raise DataPolicyUnavailable(
            "Data policy state is missing. Restore the singleton row from a known-good backup before changing policy."
        )
    return state


def _lock_label(db: Session, label_id: uuid.UUID) -> HandlingLabel:
    label = db.scalar(
        select(HandlingLabel)
        .where(HandlingLabel.id == label_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if label is None:
        raise HandlingLabelNotFound("Handling label not found.")
    return label


def _assert_policy_revision(state: DataPolicyState, expected_revision: int) -> None:
    if state.revision != expected_revision:
        raise DataPolicyRevisionConflict(
            "The data policy changed after it was loaded. Reload it and retry.",
            current_revision=state.revision,
            context={
                "expected_revision": expected_revision,
                "current_revision": state.revision,
            },
        )


def _assert_label_revision(
    label: HandlingLabel,
    expected_revision: int,
    *,
    policy_revision: int,
) -> None:
    if label.revision != expected_revision:
        raise HandlingLabelRevisionConflict(
            "The handling label changed after it was loaded. Reload it and retry.",
            current_revision=policy_revision,
            context={
                "expected_revision": expected_revision,
                "current_label_revision": label.revision,
                "current_policy_revision": policy_revision,
            },
        )


def _bump_policy_state(state: DataPolicyState, *, actor_user_id: uuid.UUID) -> None:
    state.revision += 1
    state.updated_by_user_id = actor_user_id


def _validate_role_ids(db: Session, role_ids: set[uuid.UUID]) -> None:
    if not role_ids:
        raise DataPolicyValidationError(
            "Restricted handling labels must grant at least one role."
        )
    existing = set(
        db.scalars(
            select(IAMRole.id)
            .where(IAMRole.id.in_(role_ids))
            .with_for_update(key_share=True)
        ).all()
    )
    missing = sorted(role_ids - existing, key=str)
    if missing:
        raise DataPolicyValidationError(
            "One or more role IDs do not exist.",
            context={"unknown_role_ids": [str(value) for value in missing]},
        )


def _required_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise DataPolicyValidationError(
            f"{field.replace('_', ' ').title()} cannot be empty."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise DataPolicyValidationError(
            f"{field.replace('_', ' ').title()} cannot contain control characters."
        )
    return normalized


def _optional_text(value: str) -> str:
    normalized = value.strip()
    if any(
        ord(character) < 32 and character not in "\n\r\t" for character in normalized
    ):
        raise DataPolicyValidationError(
            "Description cannot contain unsupported control characters."
        )
    return normalized


def _state_response(state: DataPolicyState) -> DataPolicyStateResponse:
    return DataPolicyStateResponse(
        mode=state.mode,
        revision=state.revision,
        coverage_version=state.coverage_version,
        required_coverage_version=REQUIRED_ENFORCEMENT_COVERAGE_VERSION,
        enforced_at=state.enforced_at,
        enforced_by_user_id=state.enforced_by_user_id,
        updated_by_user_id=state.updated_by_user_id,
        updated_at=state.updated_at,
    )


def _label_responses(db: Session) -> list[HandlingLabelResponse]:
    labels = db.scalars(
        select(HandlingLabel).order_by(
            HandlingLabel.is_unrestricted.desc(),
            HandlingLabel.is_active.desc(),
            HandlingLabel.name,
            HandlingLabel.id,
        )
    ).all()
    role_rows = db.execute(
        select(DataPolicyRoleGrant.label_id, DataPolicyRoleGrant.role_id).order_by(
            DataPolicyRoleGrant.label_id, DataPolicyRoleGrant.role_id
        )
    ).all()
    roles_by_label: dict[uuid.UUID, list[uuid.UUID]] = {}
    for label_id, role_id in role_rows:
        roles_by_label.setdefault(label_id, []).append(role_id)
    feed_counts = dict(
        db.execute(
            select(Feed.handling_label_id, func.count(Feed.id)).group_by(
                Feed.handling_label_id
            )
        ).all()
    )
    return [
        _label_response_from_values(
            label,
            role_ids=roles_by_label.get(label.id, []),
            assigned_feed_count=int(feed_counts.get(label.id, 0)),
        )
        for label in labels
    ]


def _label_response(db: Session, label: HandlingLabel) -> HandlingLabelResponse:
    role_ids = list(
        db.scalars(
            select(DataPolicyRoleGrant.role_id)
            .where(DataPolicyRoleGrant.label_id == label.id)
            .order_by(DataPolicyRoleGrant.role_id)
        ).all()
    )
    assigned_feed_count = int(
        db.scalar(select(func.count(Feed.id)).where(Feed.handling_label_id == label.id))
        or 0
    )
    return _label_response_from_values(
        label,
        role_ids=role_ids,
        assigned_feed_count=assigned_feed_count,
    )


def _label_response_from_values(
    label: HandlingLabel,
    *,
    role_ids: list[uuid.UUID],
    assigned_feed_count: int,
) -> HandlingLabelResponse:
    return HandlingLabelResponse(
        id=label.id,
        key=label.key,
        name=label.name,
        description=label.description,
        color=label.color,
        is_unrestricted=label.is_unrestricted,
        is_system=label.is_system,
        is_active=label.is_active,
        revision=label.revision,
        role_ids=role_ids,
        assigned_feed_count=assigned_feed_count,
        created_at=label.created_at,
        updated_at=label.updated_at,
    )


__all__ = [
    "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
    "DataAccessContext",
    "DataPolicyActivationBlocked",
    "DataPolicyConflict",
    "DataPolicyError",
    "DataPolicyRevisionConflict",
    "DataPolicyUnavailable",
    "DataPolicyValidationError",
    "HandlingLabelNotFound",
    "HandlingLabelRevisionConflict",
    "REQUIRED_ENFORCEMENT_COVERAGE_VERSION",
    "assign_feed_handling_label",
    "create_handling_label",
    "current_data_policy_revision",
    "data_access_context_for_authorization",
    "fence_data_access_context",
    "data_policy_overview",
    "data_policy_preflight",
    "get_handling_label",
    "handling_label_access_predicate",
    "replace_handling_label_role_grants",
    "set_handling_label_status",
    "update_data_policy_mode",
    "update_handling_label",
]
