from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.authorization import (
    AuthorizationContext,
    authorization_context_for_user,
    lock_iam_policy_for_mutation,
)


class GovernanceAuthorizationDenied(PermissionError):
    code = "governance_authorization_denied"

    def __init__(
        self,
        detail: str,
        *,
        reason: str,
        required_permission: str | None = None,
    ) -> None:
        self.reason = reason
        self.required_permission = required_permission
        super().__init__(detail)


def lock_and_authorize_governance_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    credential_scopes: Iterable[str] | None,
    required_permission: str | None,
    durable: bool = False,
) -> tuple[User, AuthorizationContext]:
    lock_iam_policy_for_mutation(db)
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active or not user.is_approved:
        raise GovernanceAuthorizationDenied(
            "Your account is no longer active and approved. Sign in again before retrying this governance operation.",
            reason="actor_missing_or_ineligible",
            required_permission=required_permission,
        )
    authorization = authorization_context_for_user(
        db,
        user,
        credential_scopes=credential_scopes,
    )
    if required_permission is None:
        return user, authorization
    allowed = (
        authorization.has_durable(required_permission)
        if durable
        else authorization.has(required_permission)
    )
    if not allowed:
        raise GovernanceAuthorizationDenied(
            (
                f"This governance operation requires durably assigned {required_permission} access."
                if durable
                else f"This governance operation requires {required_permission} access."
            ),
            reason=("durable_authority_required" if durable else "permission_changed"),
            required_permission=required_permission,
        )
    return user, authorization


__all__ = [
    "GovernanceAuthorizationDenied",
    "lock_and_authorize_governance_user",
]
