"""Current team and source authorization plus bounded assessment read contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.core.api_errors import ApiHTTPException
from app.models.ai_task_run import AITaskRun
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.team_item_assessment import TeamItemAssessment
from app.models.user import User
from app.schemas.team_assessments import TeamAssessmentEnvelope, TeamAssessmentResponse
from app.services.ai_config import ActiveAISettings
from app.services.ai_workflow_dispatch import as_utc
from app.services.authorization import AuthorizationContext, fence_authorization_context
from app.services.data_access_policy import (
    DataAccessContext,
    fence_data_access_context,
    handling_label_access_predicate,
)
from app.services.export_job_access import (
    ExportJobAccessDenied,
    capture_export_authorization,
    assert_export_sources_visible,
    fence_export_authorization,
    load_export_sources,
)
from app.services.export_job_contracts import ExportAuthorizationSnapshot
from app.services.team_access import (
    assert_current_team_access,
    lock_team_for_current_access,
)
from app.services.team_ai_context import team_ai_context_snapshot


@dataclass
class AssessmentRequest:
    user: User
    authorization: AuthorizationContext
    access: DataAccessContext
    snapshot: ExportAuthorizationSnapshot


@dataclass
class RequestPrincipal:
    principal_id: uuid.UUID
    principal_type: str = "user"
    authorization_encrypted: dict = field(default_factory=dict)
    source_encrypted: dict | None = None


@dataclass(frozen=True)
class AssessmentState:
    item: Item
    context_version: int
    article_id: uuid.UUID | None
    article_retrieved_at: datetime | None
    assessment: TeamItemAssessment | None
    run: AITaskRun | None
    result_visible: bool = True
    error_visible: bool = True


def assessment_source_visible(
    db: Session,
    *,
    row: TeamItemAssessment,
    snapshot: dict | None,
    access: DataAccessContext,
) -> bool:
    """Retain the derived result's original source label across later relabels."""
    if snapshot is None:
        return False
    boundary = RequestPrincipal(row.principal_id, source_encrypted=snapshot)
    try:
        sources = load_export_sources(boundary)
        if len(sources) != 1 or sources[0].item_id != row.item_id:
            return False
        assert_export_sources_visible(db, boundary, access)
    except ExportJobAccessDenied:
        return False
    return True


def assessment_request(
    request: Request,
    *,
    user: User,
    authorization: AuthorizationContext,
    access: DataAccessContext,
) -> AssessmentRequest:
    try:
        snapshot = capture_export_authorization(request, authorization, access)
    except ExportJobAccessDenied as exc:
        raise credential_error() from exc
    return AssessmentRequest(user, authorization, access, snapshot)


def credential_error() -> ApiHTTPException:
    return ApiHTTPException(
        status_code=403,
        error_code="team_assessment_access_changed",
        detail="Your credentials or article access changed. Refresh your session and retry.",
    )


def fence_assessment_request(
    db: Session, actor: AssessmentRequest, *, write: bool, exclusive_actor: bool = False
) -> None:
    permissions = ("read:items", "read:teams", *(("write:teams",) if write else ()))
    if exclusive_actor:
        # Investigation helpers require an exclusive actor lock. Take it before
        # credential/team locks, never by upgrading a previously held shared lock.
        fence_authorization_context(db, actor.authorization)
        fence_data_access_context(db, actor.access)
        db.scalar(select(User.id).where(User.id == actor.user.id).with_for_update())
    try:
        fence_export_authorization(
            db,
            RequestPrincipal(actor.user.id),
            actor.authorization,
            actor.access,
            snapshot=actor.snapshot,
            required_permissions=permissions,
        )
    except ExportJobAccessDenied as exc:
        raise credential_error() from exc


def load_assessment_state(
    db: Session,
    *,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    item_id: uuid.UUID,
    write: bool,
    run_status_only: bool = False,
) -> AssessmentState:
    # Global policy and credential fences precede team, assessment, item, run.
    fence_assessment_request(db, actor, write=write)
    team = lock_team_for_current_access(
        db,
        team_id=team_id,
        user_id=actor.user.id,
        for_update=write,
    )
    if team is None:
        raise ApiHTTPException(
            status_code=404,
            error_code="team_not_found",
            detail="Team not found or current group membership does not permit this action.",
        )
    query = select(TeamItemAssessment).where(
        TeamItemAssessment.team_id == team_id,
        TeamItemAssessment.item_id == item_id,
    )
    if write:
        query = query.with_for_update()
    assessment = db.scalar(query.execution_options(populate_existing=True))
    item_query = (
        select(Item)
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            Item.id == item_id,
            handling_label_access_predicate(Feed.handling_label_id, actor.access),
        )
        .options(load_only(Item.id, Item.feed_id, Item.classification_required_version))
    )
    if write:
        item_query = item_query.with_for_update(read=True, of=Item)
    item = db.scalar(item_query.execution_options(populate_existing=True))
    if item is None:
        raise ApiHTTPException(
            status_code=404, error_code="item_not_found", detail="Item not found."
        )
    article = db.execute(
        select(Article.id, Article.retrieved_at).where(Article.item_id == item_id)
    ).one_or_none()
    run = None
    if assessment is not None and assessment.task_run_id is not None:
        run_query = select(AITaskRun).where(AITaskRun.id == assessment.task_run_id)
        if run_status_only:
            run_query = run_query.options(load_only(AITaskRun.id, AITaskRun.status, raiseload=True))
        if write:
            run_query = run_query.with_for_update()
        run = db.scalar(run_query.execution_options(populate_existing=True))
    context = team_ai_context_snapshot(db, team_id=team_id)
    assert_current_team_access(db, team_id=team_id, user_id=actor.user.id)
    result_visible = (
        assessment is None
        or assessment.result_json is None
        or assessment_source_visible(
            db,
            row=assessment,
            snapshot=assessment.result_source_encrypted,
            access=actor.access,
        )
    )
    error_visible = assessment is None or assessment_source_visible(
        db,
        row=assessment,
        snapshot=assessment.source_encrypted,
        access=actor.access,
    )
    return AssessmentState(
        item,
        context.version,
        article.id if article else None,
        article.retrieved_at if article else None,
        assessment,
        run,
        result_visible,
        error_visible,
    )


def result_is_stale(state: AssessmentState) -> bool:
    row = state.assessment
    if row is None or row.result_json is None:
        return False
    old_time, current_time = row.result_article_retrieved_at, state.article_retrieved_at
    return (
        not state.result_visible
        or row.result_context_version != state.context_version
        or row.result_source_version != state.item.classification_required_version
        or row.result_article_id != state.article_id
        or (as_utc(old_time) if old_time else None)
        != (as_utc(current_time) if current_time else None)
    )


def assessment_envelope(
    state: AssessmentState, *, actor: AssessmentRequest, active: ActiveAISettings
) -> TeamAssessmentEnvelope:
    row = state.assessment
    response = None
    if row is not None:
        status = (
            state.run.status
            if state.run is not None
            else "ready"
            if row.result_json
            else "error"
        )
        error = (
            state.run.error
            if state.run is not None and state.error_visible and state.result_visible
            else None
        )
        if status == "skipped" and error is None:
            reason = state.run.reason if state.run is not None else None
            error = (
                "This assessment was stopped after recovery. Generate again using a current session."
                if reason == "restore_quarantine"
                else "This assessment was canceled. Generate again when ready."
                if reason in {"cancel_requested", "cancelled", "canceled"}
                else "This assessment was skipped. Review the current team and article, then generate again."
            )
        response = TeamAssessmentResponse(
            id=row.id,
            team_id=row.team_id,
            item_id=row.item_id,
            version=row.version,
            status=status,
            stale=result_is_stale(state),
            error=error,
            generated_at=row.generated_at if state.result_visible else None,
            context_version=row.result_context_version
            if row.result_json is not None and state.result_visible
            else row.context_version,
            result=row.result_json if state.result_visible else None,
        )
    return TeamAssessmentEnvelope(
        assessment=response,
        ai_enabled=active.ai_enabled,
        configured=active.ai_configured,
        hunt_suggestions_enabled=active.hunt_suggestions_enabled,
        can_generate=actor.authorization.has("write:teams"),
    )
