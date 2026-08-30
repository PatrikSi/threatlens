from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.token_scopes import SCOPE_WRITE_INVESTIGATIONS
from app.models.iam import IAMGroup
from app.models.service_account import ServiceAccount
from app.models.temporary_elevation import TemporaryElevation
from app.models.user import User
from app.schemas.temporary_elevation import ElevationCloseRequest
from app.services.access_review_apply import (
    AccessReviewApplyContext,
    AccessReviewMutationResult,
)
from app.services.access_reviews import AccessReviewConflict, AccessReviewError
from app.services.auth_sessions import lock_user_auth_states
from app.services.authorization import (
    AuthorizationContext,
    authorization_context_for_user,
    lock_iam_policy_for_mutation,
)
from app.services.iam_groups import IAMGroupError, remove_group_member
from app.services.iam_roles import IAMRoleError, remove_role_from_user
from app.services.investigation_ownership import (
    InvestigationOwnerReassignmentRequired,
    reconcile_user_investigation_permission_reduction,
)
from app.services.service_accounts import (
    ServiceAccountError,
    remove_role_assignment,
)
from app.services.temporary_elevations import (
    TemporaryElevationError,
    close_temporary_elevation,
)
from app.services.user_access import revoke_user_credentials_with_counts


class AccessReviewMutationBlocked(AccessReviewConflict):
    code = "access_review_mutation_blocked"

    def __init__(
        self, detail: str, *, context: dict[str, object] | None = None
    ) -> None:
        self.context = context or {}
        super().__init__(detail)


@dataclass(frozen=True)
class _HumanReductionEvidence:
    revoked_api_tokens: int = 0
    revoked_auth_sessions: int = 0
    cancelled_pending_mfa_enrollments: int = 0
    cleared_investigation_assignments: int = 0


def coordinate_access_review_revocation(
    db: Session, context: AccessReviewApplyContext
) -> AccessReviewMutationResult:
    """Apply one locally owned access reduction using database-only side effects."""

    item = context.item
    try:
        if item.item_type == "direct_user_role":
            evidence, resource_revision = _remove_direct_user_role(db, context)
        elif item.item_type == "group_membership":
            evidence, resource_revision = _remove_group_membership(db, context)
        elif item.item_type == "service_account_role":
            evidence, resource_revision = _remove_service_account_role(db, context)
        elif item.item_type == "live_elevation":
            evidence, resource_revision = _revoke_temporary_elevation(db, context)
        else:
            raise AccessReviewError(
                f"Access-review item type {item.item_type!r} is not locally mutable. No access was changed."
            )
    except InvestigationOwnerReassignmentRequired as exc:
        count = len(exc.investigations)
        noun = "investigation" if count == 1 else "investigations"
        raise AccessReviewMutationBlocked(
            f"The reviewed account is the only eligible owner of {count} {noun}. Add another active, approved owner before reducing this account's access, then retry.",
            context={
                "reason": "investigation_owner_reassignment_required",
                "affected_investigation_count": count,
            },
        ) from exc
    except (
        IAMRoleError,
        IAMGroupError,
        ServiceAccountError,
        TemporaryElevationError,
    ) as exc:
        raise AccessReviewMutationBlocked(
            f"The underlying access assignment could not be removed: {exc}",
            context={
                "reason": getattr(exc, "code", "access_assignment_conflict"),
                "item_type": item.item_type,
                "assignment_id": str(item.assignment_id),
            },
        ) from exc

    policy_state = lock_iam_policy_for_mutation(db)
    return AccessReviewMutationResult(
        mutation_performed=True,
        detail_code="assignment_revoked",
        detail="The reviewed assignment was removed and dependent access state was reconciled.",
        result_snapshot={
            "schema_version": 1,
            "item_type": item.item_type,
            "assignment_id": str(item.assignment_id),
            "principal_type": item.principal_type,
            "principal_id": str(item.principal_id_snapshot),
            "target_type": item.target_type,
            "target_id": str(item.target_id_snapshot),
            "resource_revision": resource_revision,
            "iam_policy_revision": int(policy_state.revision),
            "revoked_api_tokens": evidence.revoked_api_tokens,
            "revoked_auth_sessions": evidence.revoked_auth_sessions,
            "cancelled_pending_mfa_enrollments": (
                evidence.cancelled_pending_mfa_enrollments
            ),
            "cleared_investigation_assignments": (
                evidence.cleared_investigation_assignments
            ),
        },
    )


def _remove_direct_user_role(
    db: Session, context: AccessReviewApplyContext
) -> tuple[_HumanReductionEvidence, int | None]:
    item = context.item
    user, before = _lock_human_before_reduction(db, item.principal_id_snapshot)
    remove_role_from_user(
        db,
        user_id=user.id,
        assignment_id=item.assignment_id,
    )
    return _reconcile_human_reduction(db, user, before, context), None


def _remove_group_membership(
    db: Session, context: AccessReviewApplyContext
) -> tuple[_HumanReductionEvidence, int | None]:
    item = context.item
    user, before = _lock_human_before_reduction(db, item.principal_id_snapshot)
    remove_group_member(
        db,
        group_id=item.target_id_snapshot,
        membership_id=item.assignment_id,
    )
    group = db.get(IAMGroup, item.target_id_snapshot)
    return (
        _reconcile_human_reduction(db, user, before, context),
        int(group.revision) if group is not None else None,
    )


def _remove_service_account_role(
    db: Session, context: AccessReviewApplyContext
) -> tuple[_HumanReductionEvidence, int | None]:
    item = context.item
    account = db.get(ServiceAccount, item.principal_id_snapshot)
    if account is None:
        raise AccessReviewMutationBlocked(
            "The service account disappeared while its role assignment was being removed.",
            context={"reason": "service_account_not_found"},
        )
    current_revision = context.current_assignment.provenance.get(
        "service_account_revision"
    )
    if isinstance(current_revision, bool) or not isinstance(current_revision, int):
        raise AccessReviewError(
            "The service-account review snapshot has no valid account revision. No access was changed."
        )
    remove_role_assignment(
        db,
        service_account_id=account.id,
        assignment_id=item.assignment_id,
        expected_revision=current_revision,
    )
    return _HumanReductionEvidence(), int(account.revision)


def _revoke_temporary_elevation(
    db: Session, context: AccessReviewApplyContext
) -> tuple[_HumanReductionEvidence, int | None]:
    item = context.item
    user, before = _lock_human_before_reduction(db, item.principal_id_snapshot)
    assignment_revision = context.current_assignment.assignment_revision
    if assignment_revision is None:
        raise AccessReviewError(
            "The temporary-elevation review snapshot has no revision. No access was changed."
        )
    close_temporary_elevation(
        db,
        elevation_id=item.assignment_id,
        actor=context.actor,
        can_manage_others=True,
        payload=ElevationCloseRequest(
            expected_revision=assignment_revision,
            reason=f"Access review {context.campaign.id} revoke decision",
        ),
    )
    elevation = db.get(TemporaryElevation, item.assignment_id)
    return (
        _reconcile_human_reduction(db, user, before, context),
        int(elevation.revision) if elevation is not None else None,
    )


def _lock_human_before_reduction(
    db: Session, user_id: uuid.UUID
) -> tuple[User, AuthorizationContext]:
    user = lock_user_auth_states(db, [user_id]).get(user_id)
    if user is None:
        raise AccessReviewMutationBlocked(
            "The reviewed user no longer exists.",
            context={"reason": "reviewed_user_not_found"},
        )
    return user, authorization_context_for_user(db, user)


def _reconcile_human_reduction(
    db: Session,
    user,
    before: AuthorizationContext,
    context: AccessReviewApplyContext,
) -> _HumanReductionEvidence:
    after = authorization_context_for_user(db, user)
    if not _access_was_reduced(before, after):
        return _HumanReductionEvidence()
    cleared = 0
    if (
        SCOPE_WRITE_INVESTIGATIONS in before.permissions
        and SCOPE_WRITE_INVESTIGATIONS not in after.permissions
    ):
        result = reconcile_user_investigation_permission_reduction(
            db,
            user=user,
            actor_user_id=context.actor.id,
        )
        cleared = result.cleared_assignment_count
    revoked = revoke_user_credentials_with_counts(
        db,
        user,
        reason="access_review_revoke_decision",
    )
    return _HumanReductionEvidence(
        revoked_api_tokens=revoked.api_tokens,
        revoked_auth_sessions=revoked.auth_sessions,
        cancelled_pending_mfa_enrollments=revoked.pending_mfa_enrollments,
        cleared_investigation_assignments=cleared,
    )


def _access_was_reduced(
    before: AuthorizationContext, after: AuthorizationContext
) -> bool:
    before_roles = {(role.id, role.key) for role in before.roles}
    after_roles = {(role.id, role.key) for role in after.roles}
    return bool(
        before.permissions - after.permissions
        or set(before.groups) - set(after.groups)
        or before_roles - after_roles
    )


__all__ = ["AccessReviewMutationBlocked", "coordinate_access_review_revocation"]
