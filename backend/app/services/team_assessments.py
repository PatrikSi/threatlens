"""Accept and review team-scoped assessments without broadening source access."""

from __future__ import annotations

from copy import deepcopy
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.models.ai_task_run import AITaskRun
from app.models.team_item_assessment import TeamAssessmentRevision, TeamItemAssessment
from app.schemas.team_assessments import (
    HuntReviewCommand,
    TeamAssessmentCommand,
    TeamAssessmentEnvelope,
)
from app.services.ai_config import load_active_ai_settings
from app.services.ai_ops import queue_ai_task_run
from app.services.audit import record_audit
from app.services.export_job_access import (
    ExportJobAccessDenied,
    export_source_snapshot,
    fence_export_authorization,
)
from app.services.secret_storage import encrypt_json
from app.services.investigations import add_evidence, add_note, create_investigation
from app.services.team_access import assert_current_team_access
from app.services.team_hunt_snapshot import hunt_snapshot_notes

from app.services.team_assessment_access import (
    AssessmentRequest,
    AssessmentState,
    RequestPrincipal,
    credential_error,
    fence_assessment_request,
    load_assessment_state,
    result_is_stale,
    assessment_envelope,
)

ACTIVE_STATUSES = frozenset({"queued", "running"})
MAX_ACTIVE_ASSESSMENTS = 100
MAX_ACTIVE_ASSESSMENTS_PER_USER = 10


def get_assessment(
    db: Session, *, actor: AssessmentRequest, team_id: uuid.UUID, item_id: uuid.UUID
) -> TeamAssessmentEnvelope:
    state = load_assessment_state(
        db, actor=actor, team_id=team_id, item_id=item_id, write=False
    )
    return assessment_envelope(
        state,
        actor=actor,
        active=load_active_ai_settings(db, feature_type="team_assessment"),
    )


def _require_version(state: AssessmentState, expected_version: int) -> None:
    version = state.assessment.version if state.assessment is not None else 0
    if version != expected_version:
        raise ApiHTTPException(
            status_code=409,
            error_code="team_assessment_version_conflict",
            detail="This assessment changed. Refresh it and review the current revision before retrying.",
            error_context={"current_version": version},
        )


def _require_idle(state: AssessmentState) -> None:
    if state.run is not None and state.run.status in ACTIVE_STATUSES:
        raise ApiHTTPException(
            status_code=409,
            error_code="team_assessment_in_progress",
            detail="This team's assessment is already queued or running. Wait for it to finish.",
        )


def archive_assessment_revision(
    db: Session,
    row: TeamItemAssessment,
    *,
    actor_user_id: uuid.UUID | None,
    change_kind: str,
) -> None:
    """Snapshot the prior result under the assessment lock, once per revision."""
    if row.result_json is None:
        return
    if db.get(TeamAssessmentRevision, (row.id, row.version)) is not None:
        return
    db.add(
        TeamAssessmentRevision(
            assessment_id=row.id,
            version=row.version,
            result_json=deepcopy(row.result_json),
            result_source_encrypted=deepcopy(row.result_source_encrypted),
            result_context_version=row.result_context_version,
            result_source_version=row.result_source_version,
            result_article_id=row.result_article_id,
            result_article_retrieved_at=row.result_article_retrieved_at,
            generated_at=row.generated_at,
            actor_user_id=actor_user_id,
            change_kind=change_kind,
        )
    )
    db.flush()


def _admit_generation(db: Session, actor: AssessmentRequest) -> None:
    # The transaction-level lock serializes admission counts across different teams.
    # Workers never take this lock, so no queue capacity wait blocks publication.
    db.execute(text("SELECT pg_advisory_xact_lock(302, 109)"))
    total, owned = db.execute(
        select(
            func.count(AITaskRun.id),
            func.count(AITaskRun.id).filter(AITaskRun.actor_user_id == actor.user.id),
        ).where(
            AITaskRun.task_type == "team_assessment",
            AITaskRun.status.in_(ACTIVE_STATUSES),
        )
    ).one()
    if total >= MAX_ACTIVE_ASSESSMENTS or owned >= MAX_ACTIVE_ASSESSMENTS_PER_USER:
        raise ApiHTTPException(
            status_code=429,
            error_code="team_assessment_capacity_full",
            detail="Team assessment capacity is full. Wait for queued assessments to finish before retrying.",
            headers={"Retry-After": "30"},
        )


def queue_assessment(
    db: Session,
    *,
    actor: AssessmentRequest,
    item_id: uuid.UUID,
    payload: TeamAssessmentCommand,
) -> tuple[TeamAssessmentEnvelope, uuid.UUID]:
    fence_assessment_request(db, actor, write=True)
    _admit_generation(db, actor)
    state = load_assessment_state(
        db, actor=actor, team_id=payload.team_id, item_id=item_id, write=True
    )
    _require_version(state, payload.expected_version)
    _require_idle(state)
    active = load_active_ai_settings(db, feature_type="team_assessment")
    if not active.ai_enabled or not active.ai_configured:
        raise ApiHTTPException(
            status_code=409,
            error_code="team_assessment_ai_unavailable",
            detail="Team assessments require enabled AI and a configured article provider. Ask an administrator to check AI settings.",
        )
    row = state.assessment
    if row is None:
        row = TeamItemAssessment(team_id=payload.team_id, item_id=item_id, version=1)
        db.add(row)
    else:
        archive_assessment_revision(
            db, row, actor_user_id=actor.user.id, change_kind="regenerate"
        )
        row.version += 1
    row.context_version = state.context_version
    row.source_version = state.item.classification_required_version
    row.article_id, row.article_retrieved_at = (
        state.article_id,
        state.article_retrieved_at,
    )
    row.principal_type, row.principal_id = "user", actor.user.id
    row.authorization_encrypted = encrypt_json(actor.snapshot.model_dump(mode="json"))
    row.source_encrypted = encrypt_json(export_source_snapshot(db, [item_id]))
    db.flush()
    run = queue_ai_task_run(
        db,
        task_type="team_assessment",
        trigger_source="manual",
        actor_user_id=actor.user.id,
        item_id=item_id,
        model=active.model,
        metadata={
            "assessment_id": str(row.id),
            "team_id": str(payload.team_id),
            "assessment_version": row.version,
            "hunt_suggestions_enabled": active.hunt_suggestions_enabled,
        },
    )
    row.task_run_id = run.id
    db.flush()
    _audit(db, row, actor, action="queued")
    fence_assessment_request(db, actor, write=True)
    assert_current_team_access(db, team_id=payload.team_id, user_id=actor.user.id)
    queued = AssessmentState(
        state.item,
        state.context_version,
        state.article_id,
        state.article_retrieved_at,
        row,
        run,
        state.result_visible,
        True,
    )
    return assessment_envelope(queued, actor=actor, active=active), run.id


def _audit(
    db: Session,
    row: TeamItemAssessment,
    actor: AssessmentRequest,
    *,
    action: str,
    hunt_id: str | None = None,
) -> None:
    record_audit(
        db,
        actor_user_id=actor.user.id,
        action=f"ai.team_assessment.{action}",
        resource_type="item",
        resource_id=str(row.item_id),
        metadata={
            "assessment_id": str(row.id),
            "team_id": str(row.team_id),
            "version": row.version,
            **({"hunt_id": hunt_id} if hunt_id else {}),
        },
    )


def _mutable_hunt(
    state: AssessmentState, *, hunt_id: str, expected_version: int
) -> tuple[TeamItemAssessment, dict, dict]:
    _require_version(state, expected_version)
    _require_idle(state)
    if result_is_stale(state):
        raise ApiHTTPException(
            status_code=409,
            error_code="team_assessment_stale",
            detail="The article or team context changed. Generate a new assessment before reviewing this suggestion.",
        )
    row = state.assessment
    result = deepcopy(row.result_json) if row is not None and row.result_json else {}
    hunt = next(
        (entry for entry in result.get("hunts", []) if entry.get("id") == hunt_id), None
    )
    if row is None or hunt is None:
        raise ApiHTTPException(
            status_code=404,
            error_code="hunt_not_found",
            detail="Hunt suggestion not found.",
        )
    return row, result, hunt


def review_hunt(
    db: Session,
    *,
    actor: AssessmentRequest,
    item_id: uuid.UUID,
    hunt_id: str,
    payload: HuntReviewCommand,
) -> TeamAssessmentEnvelope:
    state = load_assessment_state(
        db, actor=actor, team_id=payload.team_id, item_id=item_id, write=True
    )
    row, result, hunt = _mutable_hunt(
        state, hunt_id=hunt_id, expected_version=payload.expected_version
    )
    archive_assessment_revision(
        db, row, actor_user_id=actor.user.id, change_kind="review"
    )
    approving = payload.status == "accepted" and hunt.get("review_status") != "accepted"
    if approving:
        hunt["approval_id"] = uuid.uuid4().hex
    hunt["review_status"], hunt["review_note"] = payload.status, payload.note or None
    row.result_json = result
    row.version += 1
    db.flush()
    _audit(db, row, actor, action=f"hunt_{payload.status}", hunt_id=hunt_id)
    fence_assessment_request(db, actor, write=True)
    assert_current_team_access(db, team_id=payload.team_id, user_id=actor.user.id)
    if approving:
        from app.services.intel_events import emit_hunt_approved

        emit_hunt_approved(db, row=row, item=state.item, hunt=hunt, actor_user_id=actor.user.id)
    return assessment_envelope(
        state,
        actor=actor,
        active=load_active_ai_settings(db, feature_type="team_assessment"),
    )


def create_hunt_investigation(
    db: Session,
    *,
    actor: AssessmentRequest,
    item_id: uuid.UUID,
    hunt_id: str,
    payload: TeamAssessmentCommand,
) -> TeamAssessmentEnvelope:
    if not actor.authorization.has("write:investigations"):
        raise ApiHTTPException(
            status_code=403,
            error_code="hunt_investigation_permission_required",
            detail="Creating a hunt investigation requires investigation write access.",
        )
    fence_assessment_request(db, actor, write=True, exclusive_actor=True)
    state = load_assessment_state(
        db, actor=actor, team_id=payload.team_id, item_id=item_id, write=True
    )
    row, result, hunt = _mutable_hunt(
        state, hunt_id=hunt_id, expected_version=payload.expected_version
    )
    active = load_active_ai_settings(db, feature_type="team_assessment")
    if hunt.get("investigation_id"):
        return assessment_envelope(state, actor=actor, active=active)
    if hunt.get("review_status") != "accepted":
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_review_required",
            detail="Accept the hunt suggestion before creating an investigation.",
        )
    archive_assessment_revision(
        db, row, actor_user_id=actor.user.id, change_kind="investigation"
    )
    investigation = create_investigation(
        db,
        user=actor.user,
        title=f"Hunt: {hunt['title']}"[:255],
        description="An analyst-reviewed hunt suggestion grounded in the attached article evidence.",
        severity="medium",
        visibility="team",
        assignee_user_id=actor.user.id,
        team_id=row.team_id,
    )
    add_evidence(
        db,
        investigation_id=investigation.id,
        user=actor.user,
        data_access=actor.access,
        source_type="item",
        source_id=item_id,
        note="Primary evidence for the accepted hunt suggestion.",
        expected_version=investigation.version,
    )
    for body in hunt_snapshot_notes(hunt, row):
        add_note(
            db,
            investigation_id=investigation.id,
            user=actor.user,
            data_access=actor.access,
            body=body,
            expected_version=investigation.version,
        )
    hunt["investigation_id"] = str(investigation.id)
    row.result_json = result
    row.version += 1
    db.flush()
    _audit(db, row, actor, action="investigation_created", hunt_id=hunt_id)
    record_audit(
        db,
        actor_user_id=actor.user.id,
        action="investigations.create",
        resource_type="investigation",
        resource_id=str(investigation.id),
        metadata={"team_assessment_id": str(row.id), "hunt_id": hunt_id},
    )
    fence_assessment_request(db, actor, write=True)
    # Re-evaluate expiring investigation permission with the accepting credential cap.
    try:
        fence_export_authorization(
            db,
            RequestPrincipal(actor.user.id),
            actor.authorization,
            actor.access,
            snapshot=actor.snapshot,
            required_permissions=(
                "read:items",
                "read:teams",
                "write:teams",
                "write:investigations",
            ),
        )
    except ExportJobAccessDenied as exc:
        raise credential_error() from exc
    assert_current_team_access(db, team_id=payload.team_id, user_id=actor.user.id)
    return assessment_envelope(state, actor=actor, active=active)
