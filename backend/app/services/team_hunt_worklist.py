"""Bounded keyset hunt reads with current and captured evidence permissions."""

from datetime import datetime, timezone
import json
import uuid

from pydantic import ValidationError
from sqlalchemy import case, cast, func, literal, or_, select, true, tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, load_only

from app.core.api_errors import ApiHTTPException
from app.models.article import Article
from app.models.feed import Feed
from app.models.investigation import Investigation
from app.models.item import Item
from app.models.team_ai_context import TeamAIContext
from app.models.team_hunt_claim import TeamHuntClaim
from app.models.team_item_assessment import TeamItemAssessment
from app.models.user import User
from app.schemas.team_assessments import HuntSuggestionResponse
from app.schemas.team_hunt_worklist import (
    HuntReviewSchedule,
    HuntClaimResponse,
    HuntInvestigationOutcome,
    HuntWorklistEntry,
    HuntWorklistPage,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INVESTIGATION,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import handling_label_access_predicate
from app.services.export_job_access import ExportJobAccessDenied, load_export_sources
from app.services.secret_storage import decrypt_text, encrypt_text, is_encrypted_text
from app.services.team_access import (
    assert_current_team_access,
    lock_team_for_current_access,
    team_access_predicate,
)
from app.services.team_assessment_access import (
    AssessmentRequest,
    RequestPrincipal,
    fence_assessment_request,
)


def _cursor_error() -> ApiHTTPException:
    return ApiHTTPException(
        status_code=422,
        error_code="hunt_cursor_invalid",
        detail="This hunt page cursor is invalid or belongs to different filters. Return to the first page.",
    )


def _read_cursor(value: str, scope: list[str]) -> tuple[datetime, uuid.UUID, str]:
    try:
        if not is_encrypted_text(value):
            raise ValueError("unsigned cursor")
        data = json.loads(decrypt_text(value))
        if not isinstance(data, list) or len(data) != 4 or data[0] != scope:
            raise ValueError("scope")
        timestamp = datetime.fromisoformat(data[1])
        if (
            timestamp.tzinfo is None
            or not isinstance(data[3], str)
            or not 1 <= len(data[3]) <= 80
        ):
            raise ValueError("position")
        return timestamp, uuid.UUID(data[2]), data[3]
    except (ValueError, TypeError, KeyError):
        raise _cursor_error() from None


def _source_visible(
    row: TeamItemAssessment, feed_id: uuid.UUID, actor: AssessmentRequest
) -> bool:
    try:
        sources = load_export_sources(
            RequestPrincipal(
                row.principal_id, source_encrypted=row.result_source_encrypted
            )
        )
        return (
            len(sources) == 1
            and sources[0].item_id == row.item_id
            and sources[0].feed_id == feed_id
            and actor.access.allows(sources[0].captured_label_id)
        )
    except ExportJobAccessDenied:
        return False


def _outcomes(
    db: Session,
    entries: list[HuntWorklistEntry],
    *,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
) -> None:
    ids = {
        entry.hunt.investigation_id for entry in entries if entry.hunt.investigation_id
    }
    outcomes = {}
    if ids and actor.authorization.has("read:investigations"):
        rows = db.execute(
            select(
                Investigation.id,
                Investigation.status,
                Investigation.disposition,
                Investigation.assignee_user_id,
            )
            .where(
                Investigation.id.in_(ids),
                Investigation.team_id == team_id,
                data_access_envelope_predicate(
                    DATA_ACCESS_RESOURCE_INVESTIGATION, Investigation.id, actor.access
                ),
            )
            .with_for_update(read=True, of=Investigation)
        ).all()
        outcomes = {row.id: HuntInvestigationOutcome(**row._mapping) for row in rows}
    for entry in entries:
        entry.investigation = outcomes.get(entry.hunt.investigation_id)
        if entry.investigation is None:
            entry.hunt.investigation_id = None


def list_team_hunts(
    db: Session,
    *,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    status: str | None,
    ownership: str,
    cursor: str | None,
    limit: int,
    order: str = "newest",
    priority: str | None = None,
    overdue: bool = False,
) -> HuntWorklistPage:
    fence_assessment_request(db, actor, write=False)
    if lock_team_for_current_access(db, team_id=team_id, user_id=actor.user.id) is None:
        raise ApiHTTPException(
            status_code=404,
            error_code="team_not_found",
            detail="Team not found or membership changed.",
        )
    manager = bool(
        db.scalar(select(team_access_predicate(team_id, actor.user.id, manage=True)))
    )
    scope = [str(team_id), status or "all", ownership]
    if order != "newest" or priority or overdue:
        scope.extend([order, priority or "all", str(overdue)])
    row = TeamItemAssessment
    raw_hunts = cast(row.result_json, JSONB).op("->")("hunts")
    safe_hunts = case(
        (func.jsonb_typeof(raw_hunts) == "array", raw_hunts),
        else_=cast(literal("[]"), JSONB),
    )
    hunts = func.jsonb_array_elements(safe_hunts).table_valued("value").lateral("hunts")
    value = cast(hunts.c.value, JSONB)
    hunt_id = value["id"].astext
    ordering_time = func.coalesce(row.generated_at, row.created_at)
    if order == "due":
        ordering_time = func.coalesce(
            TeamHuntClaim.review_due_at, datetime(9999, 1, 1, tzinfo=timezone.utc)
        )
    stale = or_(
        row.result_context_version.is_distinct_from(
            func.coalesce(TeamAIContext.version, 0)
        ),
        row.result_source_version.is_distinct_from(
            Item.classification_required_version
        ),
        row.result_article_id.is_distinct_from(Article.id),
        row.result_article_retrieved_at.is_distinct_from(Article.retrieved_at),
    )
    effective_status = case(
        (stale, "stale"),
        (
            func.coalesce(value["review_status"].astext, "suggested") == "suggested",
            "pending",
        ),
        else_=value["review_status"].astext,
    )
    claim = TeamHuntClaim
    owner_current = team_access_predicate(team_id, claim.owner_user_id)
    query = (
        select(
            row,
            value.label("hunt"),
            Item.title,
            Item.feed_id,
            ordering_time.label("ordering_time"),
            effective_status.label("effective_status"),
            claim.owner_user_id,
            claim.version.label("claim_version"),
            claim.review_version,
            claim.priority,
            claim.review_due_at,
            claim.reminded_at,
            claim.reminder_acknowledged_at,
            owner_current.label("owner_current"),
        )
        .join(Item, Item.id == row.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(Article, Article.item_id == Item.id)
        .outerjoin(TeamAIContext, TeamAIContext.team_id == row.team_id)
        .join(hunts, true())
        .outerjoin(claim, (claim.assessment_id == row.id) & (claim.hunt_id == hunt_id))
        .where(
            row.team_id == team_id,
            handling_label_access_predicate(Feed.handling_label_id, actor.access),
            func.jsonb_typeof(value.op("->")("id")) == "string",
            func.length(hunt_id).between(1, 80),
            hunt_id.op("~")(r"^[a-zA-Z0-9_-]+$"),
            effective_status.in_(["pending", "stale", "accepted", "rejected"]),
        )
        .options(
            load_only(
                row.id,
                row.item_id,
                row.team_id,
                row.version,
                row.generated_at,
                row.principal_id,
                row.result_source_encrypted,
                row.result_article_retrieved_at,
                raiseload=True,
            )
        )
    )
    if status:
        query = query.where(effective_status == status)
    if ownership == "mine":
        query = query.where(claim.owner_user_id == actor.user.id)
    elif ownership == "unclaimed":
        query = query.where(or_(claim.owner_user_id.is_(None), ~owner_current))
    if priority:
        query = query.where(func.coalesce(claim.priority, "normal") == priority)
    if overdue:
        query = query.where(
            claim.review_due_at <= func.clock_timestamp(),
            effective_status.in_(["pending", "stale"]),
        )
    if cursor:
        position = tuple_(*_read_cursor(cursor, scope))
        key = tuple_(ordering_time, row.id, hunt_id)
        query = query.where(key < position if order == "newest" else key > position)
    scan_limit = limit * 4
    candidates = db.execute(
        query.order_by(
            *(
                [ordering_time.desc(), row.id.desc(), hunt_id.desc()]
                if order == "newest"
                else [ordering_time.asc(), row.id.asc(), hunt_id.asc()]
            )
        ).limit(scan_limit + 1)
    ).all()
    entries: list[HuntWorklistEntry] = []
    last = None
    processed = 0
    now = datetime.now(timezone.utc)
    for candidate in candidates[:scan_limit]:
        processed += 1
        assessment = candidate[0]
        last = [
            scope,
            candidate.ordering_time.isoformat(),
            str(assessment.id),
            candidate.hunt.get("id"),
        ]
        if not _source_visible(assessment, candidate.feed_id, actor):
            continue
        try:
            hunt = HuntSuggestionResponse.model_validate(candidate.hunt)
        except (ValidationError, TypeError):
            continue
        owner = candidate.owner_user_id
        can_control = (
            owner in (None, actor.user.id) or manager or not candidate.owner_current
        )
        generated = assessment.generated_at
        entries.append(
            HuntWorklistEntry(
                assessment_id=assessment.id,
                assessment_version=assessment.version,
                item_id=assessment.item_id,
                item_title=candidate.title,
                team_id=team_id,
                status=candidate.effective_status,
                generated_at=generated,
                evidence_age_seconds=max(
                    0,
                    int((now - assessment.result_article_retrieved_at).total_seconds()),
                )
                if assessment.result_article_retrieved_at
                else None,
                hunt=hunt,
                claim=HuntClaimResponse(
                    version=candidate.claim_version or 0, owner_user_id=owner
                ),
                owner_name=None,
                reviewer_name=None,
                reviewed_at=hunt.reviewed_at,
                can_claim=actor.authorization.has("write:teams")
                and can_control
                and candidate.effective_status != "stale"
                and hunt.investigation_id is None,
                can_release=actor.authorization.has("write:teams")
                and can_control
                and owner is not None,
                investigation=None,
                review_schedule=HuntReviewSchedule(
                    version=candidate.review_version or 0,
                    priority=candidate.priority or "normal",
                    due_at=candidate.review_due_at,
                    overdue=bool(
                        candidate.review_due_at
                        and candidate.review_due_at <= now
                        and candidate.effective_status in {"pending", "stale"}
                    ),
                    reminded_at=candidate.reminded_at,
                    reminder_acknowledged_at=candidate.reminder_acknowledged_at,
                ),
                can_schedule=actor.authorization.has("write:teams")
                and can_control
                and hunt.investigation_id is None
                and candidate.effective_status in {"pending", "stale"},
            )
        )
        if len(entries) == limit:
            break
    user_ids = {
        value
        for entry in entries
        for value in (entry.claim.owner_user_id, entry.hunt.reviewed_by_user_id)
        if value
    }
    names = (
        dict(db.execute(select(User.id, User.email).where(User.id.in_(user_ids))).all())
        if user_ids
        else {}
    )
    for entry in entries:
        entry.owner_name = names.get(entry.claim.owner_user_id)
        entry.reviewer_name = names.get(entry.hunt.reviewed_by_user_id)
    _outcomes(db, entries, actor=actor, team_id=team_id)
    assert_current_team_access(db, team_id=team_id, user_id=actor.user.id)
    fence_assessment_request(db, actor, write=False)
    has_more = len(candidates) > processed
    next_cursor = encrypt_text(json.dumps(last)) if has_more and last else None
    return HuntWorklistPage(
        items=entries, next_cursor=next_cursor, has_more=has_more, limit=limit
    )
