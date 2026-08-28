from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.alert_evaluation_request import AlertEvaluationRequestActivity


def record_alert_evaluation_activity(
    db: Session,
    *,
    request_id: uuid.UUID,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> AlertEvaluationRequestActivity:
    activity = AlertEvaluationRequestActivity(
        request_id=request_id,
        actor_user_id=actor_user_id,
        action=action[:64],
        details_json=details or {},
    )
    db.add(activity)
    return activity
