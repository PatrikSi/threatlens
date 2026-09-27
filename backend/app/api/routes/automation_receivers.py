"""Write-only execution receipts and opaque withdrawals for machine receivers."""

import uuid
from contextlib import contextmanager
from sqlalchemy.exc import OperationalError
from app.db.budgets import DatabaseDeadlineExceeded, database_operation
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.automation_execution import AutomationExecution, AutomationPolicyUpdate
from app.schemas.automation_execution import ExecutionCallback, PolicyUpdateResponse
from app.services.automation_receiver_auth import (
    authenticate_receiver,
    assert_receiver_current,
)
from app.services.automation_executions import apply_callback, acknowledge_policy_update

router = APIRouter(prefix="/notifications/automation/receivers", tags=["notifications"])


@contextmanager
def _operation(db: Session):
    try:
        with database_operation(db, operation="interactive"):
            yield
    except (DatabaseDeadlineExceeded, OperationalError) as exc:
        if isinstance(exc, DatabaseDeadlineExceeded) or getattr(
            exc.orig, "sqlstate", None
        ) in {"55P03", "57014", "40P01"}:
            raise HTTPException(
                503,
                "Receiver state is busy; retry the same callback or acknowledgement after a delay",
                headers={"Retry-After": "5"},
            ) from exc
        raise


@router.post("/executions/{execution_id}/callbacks")
def receive_machine_callback(
    execution_id: uuid.UUID,
    payload: ExecutionCallback,
    request: Request,
    db: Session = Depends(get_db),
):
    with _operation(db):
        credential = authenticate_receiver(db, request)
        row = db.scalar(
            select(AutomationExecution)
            .where(
                AutomationExecution.id == execution_id,
                AutomationExecution.webhook_id == credential.webhook_id,
                AutomationExecution.team_id == credential.team_id,
            )
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "Execution not found for this receiver")
        assert_receiver_current(db, credential)
        apply_callback(db, row, payload)
        result = {
            "execution_id": row.id,
            "sequence": row.sequence,
            "status": row.status,
        }
        db.commit()
        return result


@router.get("/updates")
def machine_policy_updates(
    request: Request,
    after: uuid.UUID | None = None,
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    with _operation(db):
        credential = authenticate_receiver(db, request)
        query = (
            select(AutomationPolicyUpdate, AutomationExecution.action_id)
            .join(AutomationExecution)
            .where(
                AutomationExecution.webhook_id == credential.webhook_id,
                AutomationExecution.team_id == credential.team_id,
                AutomationPolicyUpdate.acknowledged_at.is_(None),
            )
        )
        if after:
            query = query.where(AutomationPolicyUpdate.id > after)
        rows = db.execute(
            query.order_by(AutomationPolicyUpdate.id).limit(limit + 1)
        ).all()
        assert_receiver_current(db, credential)
        return {
            "items": [
                {
                    **PolicyUpdateResponse.model_validate(
                        row.AutomationPolicyUpdate
                    ).model_dump(mode="json"),
                    "action_id": row.action_id,
                    "webhook_id": str(credential.webhook_id),
                }
                for row in rows[:limit]
            ],
            "next_cursor": str(rows[limit - 1].AutomationPolicyUpdate.id)
            if len(rows) > limit
            else None,
        }


@router.post("/updates/{update_id}/ack", response_model=PolicyUpdateResponse)
def machine_acknowledge_policy(
    update_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
):
    with _operation(db):
        credential = authenticate_receiver(db, request)
        row = db.scalar(
            select(AutomationPolicyUpdate)
            .join(AutomationExecution)
            .where(
                AutomationPolicyUpdate.id == update_id,
                AutomationExecution.webhook_id == credential.webhook_id,
                AutomationExecution.team_id == credential.team_id,
            )
        )
        if row is None:
            raise HTTPException(404, "Policy update not found for this receiver")
        execution = db.scalar(
            select(AutomationExecution)
            .where(AutomationExecution.id == row.execution_id)
            .with_for_update()
        )
        row = db.scalar(
            select(AutomationPolicyUpdate)
            .where(AutomationPolicyUpdate.id == update_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert_receiver_current(db, credential)
        acknowledge_policy_update(execution, row)
        result = PolicyUpdateResponse.model_validate(row)
        db.commit()
        return result
