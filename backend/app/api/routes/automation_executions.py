"""Receiver callbacks and durable control receipts use current scoped credentials."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import String, cast, exists, or_, select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_authorization_context,
    get_current_user,
    get_data_access_context,
    require_token_scopes,
)
from app.db.session import get_db
from app.models.automation_execution import AutomationExecution, AutomationPolicyUpdate
from app.models.integration import IntegrationEvent
from app.models.investigation import Investigation
from app.models.user import User
from app.models.team import Team
from app.schemas.automation_execution import (
    AttachFindings,
    ExecutionCallback,
    ExecutionResponse,
    PolicyUpdateResponse,
)
from app.services.audit import record_audit
from app.services.authorization import AuthorizationContext
from app.services.data_access_envelopes import (
    data_access_envelope_predicate,
    copy_data_access_envelope_lineage,
)
from app.services.data_access_policy import DataAccessContext
from app.services.automation_executions import acknowledge_policy_update, apply_callback, reconcile_executions
from app.services.team_access import lock_team_for_current_access, team_access_predicate
from app.services.webhook_request_authority import fence_webhook_request

router = APIRouter(prefix="/notifications/automation", tags=["notifications"])


def _fence(
    db: Session,
    request: Request,
    authorization: AuthorizationContext,
    access: DataAccessContext,
    permission: str,
) -> None:
    fence_webhook_request(
        db,
        request=request,
        authorization=authorization,
        data_access=access,
        permission=permission,
    )


def _owner_predicate(user: User, authorization: AuthorizationContext):
    personal = (AutomationExecution.owner_user_id == user.id) & AutomationExecution.team_id.is_(None)
    if not authorization.has("read:teams"):
        return personal
    return or_(personal, team_access_predicate(AutomationExecution.team_id, user.id))


def _execution(
    db: Session,
    identity: uuid.UUID,
    user: User,
    authorization: AuthorizationContext,
    access: DataAccessContext,
    *,
    lock: bool = False,
) -> AutomationExecution:
    if not authorization.has("read:items"):
        raise HTTPException(403, "Reading automation evidence requires read:items")
    query = select(AutomationExecution).where(
        AutomationExecution.id == identity,
        _owner_predicate(user, authorization),
        data_access_envelope_predicate(
            "integration_event", AutomationExecution.event_id, access
        ),
    )
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Automation execution not found or unavailable")
    event = db.get(IntegrationEvent, row.event_id)
    event_team_id = (event.payload_json or {}).get("team_id") if event else None
    team_id = event_team_id or (str(row.team_id) if row.team_id else None)
    if team_id:
        if not authorization.has("read:teams") or (event_team_id and not authorization.has("read:ai")):
            raise HTTPException(404, "Automation execution not found or unavailable")
        if (
            lock_team_for_current_access(
                db, team_id=uuid.UUID(team_id), user_id=user.id
            )
            is None
        ):
            raise HTTPException(404, "Automation execution not found or unavailable")
    if lock:
        row = db.scalar(
            query.with_for_update().execution_options(populate_existing=True)
        )
        if team_id and not db.scalar(
            select(team_access_predicate(uuid.UUID(team_id), user.id))
        ):
            raise HTTPException(404, "Automation execution not found or unavailable")
    if row is None:
        raise HTTPException(404, "Automation execution not found or unavailable")
    return row


@router.get("/executions")
def list_automation_executions(
    request: Request,
    after: uuid.UUID | None = None,
    include_archived: bool = False,
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    access: DataAccessContext = Depends(get_data_access_context),
    _scope=Depends(require_token_scopes("read:notifications", "read:items")),
):
    _fence(db, request, authorization, access, "read:notifications")
    if not authorization.has("read:items"):
        raise HTTPException(403, "Reading automation evidence requires read:items")
    event_team = IntegrationEvent.payload_json["team_id"].as_string()
    team_visible = event_team.is_(None)
    if authorization.has("read:teams") and authorization.has("read:ai"):
        team_visible = or_(
            team_visible,
            exists(
                select(Team.id).where(
                    cast(Team.id, String) == event_team,
                    team_access_predicate(Team.id, user.id),
                )
            ),
        )
    query = (
        select(AutomationExecution)
        .join(IntegrationEvent)
        .where(
            _owner_predicate(user, authorization),
            data_access_envelope_predicate(
                "integration_event", AutomationExecution.event_id, access
            ),
            team_visible,
        )
    )
    if not include_archived:
        query = query.where(AutomationExecution.archived_at.is_(None))
    if after:
        query = query.where(AutomationExecution.id > after)
    rows = db.scalars(query.order_by(AutomationExecution.id).limit(limit + 1)).all()
    return {
        "items": [ExecutionResponse.model_validate(row) for row in rows[:limit]],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post("/executions/{execution_id}/callbacks", response_model=ExecutionResponse)
def receive_automation_callback(
    execution_id: uuid.UUID,
    payload: ExecutionCallback,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    access: DataAccessContext = Depends(get_data_access_context),
    _scope=Depends(require_token_scopes("write:notifications", "read:items")),
):
    _fence(db, request, authorization, access, "write:notifications")
    row = _execution(db, execution_id, user, authorization, access, lock=True)
    if apply_callback(db, row, payload):
        record_audit(
            db,
            actor_user_id=user.id,
            action="automation.callback",
            resource_type="integration_event",
            resource_id=row.event_id,
            metadata={
                "execution_id": str(row.id),
                "sequence": row.sequence,
                "status": row.status,
            },
        )
    db.flush()
    response = ExecutionResponse.model_validate(row)
    db.commit()
    return response


@router.post("/executions/{execution_id}/findings", response_model=ExecutionResponse)
def attach_automation_findings(
    execution_id: uuid.UUID,
    payload: AttachFindings,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    access: DataAccessContext = Depends(get_data_access_context),
    _scope=Depends(
        require_token_scopes(
            "write:notifications", "read:items", "write:investigations"
        )
    ),
):
    from app.services.investigations import (
        add_note,
        InvestigationConflictError,
        InvestigationNotFoundError,
        InvestigationPermissionError,
        InvestigationValidationError,
    )

    if not authorization.has("write:investigations"):
        raise HTTPException(403, "Attaching findings requires write:investigations")
    _fence(db, request, authorization, access, "write:notifications")
    row = _execution(db, execution_id, user, authorization, access, lock=True)
    if row.status != "completed" or not row.findings:
        raise HTTPException(409, "This execution has no completed findings to attach")
    if row.sequence != payload.expected_sequence:
        raise HTTPException(409, "Receiver findings changed; reload before attaching")
    if row.investigation_note_id:
        raise HTTPException(
            409, "Findings have already been attached to an investigation"
        )
    team_id = db.scalar(
        select(Investigation.team_id).where(
            Investigation.id == payload.investigation_id
        )
    )
    if team_id is not None and not authorization.has("write:teams"):
        raise HTTPException(
            403, "Changing a team investigation also requires write:teams"
        )
    try:
        note = add_note(
            db,
            investigation_id=payload.investigation_id,
            user=user,
            data_access=access,
            body=f"External automation findings\nAction: {row.action_id}\nJob: {row.external_job_id}\n\n{row.findings}",
            expected_version=payload.expected_investigation_version,
        )
    except InvestigationNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except InvestigationPermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except InvestigationConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except InvestigationValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Copy all captured labels, including historical reviews, into the destination.
    copy_data_access_envelope_lineage(
        db,
        source_resource_type="integration_event",
        source_resource_id=row.event_id,
        target_resource_type="investigation",
        target_resource_id=payload.investigation_id,
        operation="merge",
    )
    row.investigation_note_id = note.id
    record_audit(
        db,
        actor_user_id=user.id,
        action="automation.findings.attach",
        resource_type="integration_event",
        resource_id=row.event_id,
        metadata={
            "execution_id": str(row.id),
            "investigation_id": str(payload.investigation_id),
        },
    )
    response = ExecutionResponse.model_validate(row)
    db.commit()
    return response


@router.get("/updates")
def list_automation_policy_updates(
    request: Request,
    after: uuid.UUID | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    access: DataAccessContext = Depends(get_data_access_context),
    _scope=Depends(require_token_scopes("read:notifications")),
):
    _fence(db, request, authorization, access, "read:notifications")
    # Opaque withdrawal receipts contain no indicators, findings, team or source.
    # They remain consumable after loss of source-label access so receivers can
    # remove already received intelligence instead of retaining it indefinitely.
    query = (
        select(
            AutomationPolicyUpdate,
            AutomationExecution.action_id,
            AutomationExecution.webhook_id,
        )
        .join(
            AutomationExecution,
            AutomationExecution.id == AutomationPolicyUpdate.execution_id,
        )
        .where(
            _owner_predicate(user, authorization),
            AutomationPolicyUpdate.acknowledged_at.is_(None),
        )
    )
    if after:
        query = query.where(AutomationPolicyUpdate.id > after)
    rows = db.execute(query.order_by(AutomationPolicyUpdate.id).limit(limit + 1)).all()
    return {
        "items": [
            {
                **PolicyUpdateResponse.model_validate(
                    row.AutomationPolicyUpdate
                ).model_dump(mode="json"),
                "action_id": row.action_id,
                "webhook_id": str(row.webhook_id),
            }
            for row in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].AutomationPolicyUpdate.id)
        if len(rows) > limit
        else None,
    }


@router.post("/updates/{update_id}/ack", response_model=PolicyUpdateResponse)
def acknowledge_automation_policy_update(
    update_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    access: DataAccessContext = Depends(get_data_access_context),
    _scope=Depends(require_token_scopes("write:notifications")),
):
    _fence(db, request, authorization, access, "write:notifications")
    row = db.scalar(
        select(AutomationPolicyUpdate)
        .join(AutomationExecution)
        .where(
            AutomationPolicyUpdate.id == update_id,
            _owner_predicate(user, authorization),
        )
    )
    if row is None:
        raise HTTPException(404, "Policy update not found")
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
    if acknowledge_policy_update(execution, row):
        record_audit(
            db,
            actor_user_id=user.id,
            action="automation.policy.ack",
            resource_type="automation_execution",
            resource_id=row.execution_id,
            metadata={"update_id": str(row.id), "revision": row.revision},
        )
    response = PolicyUpdateResponse.model_validate(row)
    db.commit()
    return response


@router.post("/reconcile")
def reconcile_automation_executions(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: AuthorizationContext = Depends(get_authorization_context),
    access: DataAccessContext = Depends(get_data_access_context),
    _scope=Depends(require_token_scopes("write:notifications")),
):
    _fence(db, request, authorization, access, "write:notifications")
    checked = reconcile_executions(db, owner_user_id=user.id)
    db.commit()
    return {"checked": checked, "limit": 100}
