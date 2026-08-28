from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alert_interest import AlertInterest
from app.models.user import User


ALERT_RULES_PER_USER_LIMIT = 100


class AlertRuleQuotaExceededError(RuntimeError):
    code = "alert_rule_quota_exceeded"

    def __init__(self, *, limit: int = ALERT_RULES_PER_USER_LIMIT) -> None:
        self.limit = limit
        super().__init__(
            f"An account can have at most {limit} alert rules. Delete an existing rule before creating another."
        )


class AlertRuleOwnerUnavailableError(RuntimeError):
    code = "alert_rule_owner_unavailable"

    def __init__(self) -> None:
        super().__init__(
            "The alert-rule owner is no longer active and approved. Sign in again before creating a rule."
        )


def lock_alert_rule_creation_slot(
    db: Session,
    *,
    owner_user_id: uuid.UUID,
    limit: int = ALERT_RULES_PER_USER_LIMIT,
) -> int:
    """Serialize an owner's rule creations and reserve capacity until commit."""

    owner = db.scalar(
        select(User)
        .where(User.id == owner_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if owner is None or not owner.is_active or not owner.is_approved:
        raise AlertRuleOwnerUnavailableError()

    current_count = int(
        db.scalar(
            select(func.count(AlertInterest.id)).where(
                AlertInterest.user_id == owner_user_id
            )
        )
        or 0
    )
    if current_count >= max(1, int(limit)):
        raise AlertRuleQuotaExceededError(limit=max(1, int(limit)))
    return current_count
