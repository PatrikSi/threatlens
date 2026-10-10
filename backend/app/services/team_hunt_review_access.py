"""Common current evidence/version/claim fence for review scheduling actions."""

import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.api_errors import ApiHTTPException
from app.models.team_hunt_claim import TeamHuntClaim
from app.models.team_item_assessment import TeamItemAssessment
from app.services.team_assessment_access import (
    AssessmentRequest,
    fence_assessment_request,
    load_assessment_state,
)
from app.services.team_hunt_claims import claim_is_owned_elsewhere


def lock_review_hunt(
    db: Session,
    *,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    assessment_id: uuid.UUID,
    hunt_id: str,
    expected_assessment_version: int,
) -> tuple[TeamItemAssessment, TeamHuntClaim | None]:
    fence_assessment_request(db, actor, write=True)
    item_id = db.scalar(
        select(TeamItemAssessment.item_id).where(
            TeamItemAssessment.id == assessment_id,
            TeamItemAssessment.team_id == team_id,
        )
    )
    if item_id is None:
        raise ApiHTTPException(
            status_code=404,
            error_code="hunt_not_found",
            detail="Hunt suggestion not found.",
        )
    state = load_assessment_state(
        db, actor=actor, team_id=team_id, item_id=item_id, write=True
    )
    assessment = state.assessment
    hunts = (
        (assessment.result_json or {}).get("hunts", [])
        if assessment and state.result_visible
        else []
    )
    hunt = next(
        (
            entry
            for entry in hunts
            if isinstance(entry, dict) and entry.get("id") == hunt_id
        ),
        None,
    )
    if hunt is None:
        raise ApiHTTPException(
            status_code=404,
            error_code="hunt_not_found",
            detail="Hunt suggestion not found.",
        )
    if assessment.version != expected_assessment_version:
        raise ApiHTTPException(
            status_code=409,
            error_code="team_assessment_version_conflict",
            detail="This assessment changed. Refresh the queue before updating review work.",
        )
    if (
        hunt.get("investigation_id")
        or hunt.get("review_status", "suggested") != "suggested"
    ):
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_review_schedule_unavailable",
            detail="Review deadlines apply to pending suggestions. Use investigation assignment for promoted hunts.",
        )
    claim = db.get(
        TeamHuntClaim,
        (assessment_id, hunt_id),
        with_for_update=True,
        populate_existing=True,
    )
    if claim_is_owned_elsewhere(db, claim, actor, team_id):
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_claimed_elsewhere",
            detail="Only the current owner or a team manager can change this hunt's review deadline.",
        )
    return assessment, claim
