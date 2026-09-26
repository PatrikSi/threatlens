"""Claim transitions follow the assessment's existing policy/team lock order."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.models.team_hunt_claim import TeamHuntClaim
from app.models.team_item_assessment import TeamItemAssessment
from app.schemas.team_hunt_worklist import HuntClaimCommand, HuntClaimResponse
from app.services.audit import record_audit
from app.services.team_assessment_audit import assessment_audit_labels
from app.services.team_access import assert_current_team_access, team_access_predicate
from app.services.team_assessment_access import (
    AssessmentRequest,
    fence_assessment_request,
    load_assessment_state,
    result_is_stale,
)


def claim_is_owned_elsewhere(
    db: Session, row: TeamHuntClaim | None, actor: AssessmentRequest, team_id: uuid.UUID
) -> bool:
    if row is None or row.owner_user_id in (None, actor.user.id):
        return False
    owner_current = bool(
        db.scalar(select(team_access_predicate(team_id, row.owner_user_id)))
    )
    manager = bool(
        db.scalar(select(team_access_predicate(team_id, actor.user.id, manage=True)))
    )
    return owner_current and not manager


def assert_hunt_claim_access(
    db: Session, *, row: TeamItemAssessment, hunt_id: str, actor: AssessmentRequest
) -> None:
    claim = db.get(
        TeamHuntClaim, (row.id, hunt_id), with_for_update=True, populate_existing=True
    )
    if claim_is_owned_elsewhere(db, claim, actor, row.team_id):
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_claimed_elsewhere",
            detail="Another team member has claimed this suggestion. Ask them to release it, or ask a team manager to reassign it.",
        )


def change_hunt_claim(
    db: Session,
    *,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    assessment_id: uuid.UUID,
    hunt_id: str,
    payload: HuntClaimCommand,
) -> HuntClaimResponse:
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
    if assessment.version != payload.expected_assessment_version:
        raise ApiHTTPException(
            status_code=409,
            error_code="team_assessment_version_conflict",
            detail="This assessment changed. Refresh the queue before changing ownership.",
        )
    if (
        payload.action == "claim"
        and state.run is not None
        and state.run.status in {"queued", "running"}
    ):
        raise ApiHTTPException(
            status_code=409,
            error_code="team_assessment_in_progress",
            detail="Wait for the queued assessment to finish before claiming its suggestions.",
        )
    if payload.action == "claim" and (
        result_is_stale(state) or hunt.get("investigation_id")
    ):
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_claim_unavailable",
            detail="Refresh stale evidence before claiming a suggestion. Promoted hunts use investigation assignment.",
        )
    claim = db.get(
        TeamHuntClaim,
        (assessment_id, hunt_id),
        with_for_update=True,
        populate_existing=True,
    )
    version = claim.version if claim else 0
    if version != payload.expected_version:
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_claim_version_conflict",
            detail="Hunt ownership changed. Refresh the queue before retrying.",
        )
    if claim_is_owned_elsewhere(db, claim, actor, team_id):
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_claimed_elsewhere",
            detail="Another team member owns this suggestion. Only its owner or a team manager can release it.",
        )
    if claim is None:
        claim = TeamHuntClaim(assessment_id=assessment_id, hunt_id=hunt_id, version=1)
        db.add(claim)
    else:
        claim.version += 1
    claim.owner_user_id = actor.user.id if payload.action == "claim" else None
    db.flush()
    record_audit(
        db,
        actor_user_id=actor.user.id,
        action=f"ai.hunt.{payload.action}",
        resource_type="item",
        data_access_governed=True,
        data_access_label_ids=assessment_audit_labels(db, assessment),
        resource_id=str(item_id),
        metadata={
            "team_id": str(team_id),
            "assessment_id": str(assessment_id),
            "hunt_id": hunt_id,
            "claim_version": claim.version,
            "owner_user_id": str(claim.owner_user_id) if claim.owner_user_id else None,
        },
    )
    fence_assessment_request(db, actor, write=True)
    assert_current_team_access(db, team_id=team_id, user_id=actor.user.id)
    return HuntClaimResponse(version=claim.version, owner_user_id=claim.owner_user_id)
