"""Reject superseded automation revisions and fence sources during external I/O."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, load_only

from app.models.article import Article
from app.models.feed import Feed
from app.models.intel_assessment import ItemIntelState, TeamIntelState
from app.models.item import Item
from app.models.team import Team
from app.models.team_item_assessment import TeamItemAssessment
from app.services.team_indicator_policy import team_indicator_snapshot


class IntelEventBusy(RuntimeError):
    """The current source is changing; routing/delivery may retry safely."""


def automation_event_current(
    db: Session, payload: dict, event_type: str, *, lock: bool = False
) -> bool:
    """NOWAIT prevents inversion with writers holding source/team locks.

    Successful share locks survive the savepoint through the caller's external
    request transaction. Failed acquisition rolls back only this check and is
    explicitly retryable, never a permanent eligibility rejection.
    """
    try:
        with db.begin_nested():
            return _current(db, payload, event_type, lock=lock)
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code in {"55P03", "40P01", "40001"}:
            raise IntelEventBusy(
                "Intelligence evidence is changing; retry delivery shortly."
            ) from exc
        raise


def _query(db: Session, statement, *, lock: bool):
    if lock:
        statement = statement.with_for_update(read=True, nowait=True)
    return db.scalar(statement.execution_options(populate_existing=True))


def _valid_indicator_payload(payload: dict) -> bool:
    """Reject malformed retained snapshots before loading or locking their sources."""
    if not isinstance(payload, dict):
        return False
    indicators = payload.get("indicators")
    if not isinstance(indicators, list) or len(indicators) > 250:
        return False
    try:
        for indicator in indicators:
            if not isinstance(indicator, dict):
                return False
            uuid.UUID(indicator["id"])
            if not isinstance(indicator.get("type"), str) or not isinstance(
                indicator.get("value"), str
            ):
                return False
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
    return True


def _current(db: Session, payload: dict, event_type: str, *, lock: bool) -> bool:
    if not _valid_indicator_payload(payload):
        return False
    try:
        item_id = uuid.UUID(payload["item_id"])
        source_revision = int(payload["source_revision"])
        extraction_revision = int(payload["extraction_revision"])
    except (ValueError, TypeError, KeyError):
        return False
    if payload.get("indicators_complete") is not True:
        return False
    team_id = None
    if payload.get("team_id") is not None:
        try:
            team_id = uuid.UUID(payload["team_id"])
        except (ValueError, TypeError, KeyError):
            return False
        team = _query(
            db,
            select(Team)
            .where(Team.id == team_id)
            .options(load_only(Team.id, Team.active)),
            lock=lock,
        )
        if team is None or not team.active:
            return False
    if event_type == "hunt.approved":
        if team_id is None:
            return False
        try:
            assessment_id = uuid.UUID(payload["assessment_id"])
        except (ValueError, TypeError, KeyError):
            return False
        assessment = _query(
            db,
            select(TeamItemAssessment).where(
                TeamItemAssessment.id == assessment_id,
                TeamItemAssessment.team_id == team_id,
                TeamItemAssessment.item_id == item_id,
            ),
            lock=lock,
        )
        if assessment is None:
            return False
        hunt = next(
            (
                entry
                for entry in (assessment.result_json or {}).get("hunts", [])
                if entry.get("id") == payload.get("hunt_id")
            ),
            None,
        )
        if (
            hunt is None
            or hunt.get("review_status") != "accepted"
            or hunt.get("approval_id") != payload.get("approval_id")
        ):
            return False
        from app.services.hunt_approval import hunt_approval_fingerprint

        if hunt_approval_fingerprint(assessment, hunt) != payload.get(
            "hunt_approval_fingerprint"
        ):
            return False
        from app.models.team_ai_context import TeamAIContext

        current_context = (
            db.scalar(
                select(TeamAIContext.version).where(TeamAIContext.team_id == team_id)
            )
            or 0
        )
        if current_context != payload.get("context_version"):
            return False
    item = _query(
        db,
        select(Item)
        .where(Item.id == item_id)
        .options(
            load_only(Item.id, Item.feed_id, Item.classification_required_version)
        ),
        lock=lock,
    )
    if item is None or item.classification_required_version != source_revision:
        return False
    article = _query(
        db,
        select(Article)
        .where(Article.item_id == item_id)
        .options(
            load_only(Article.id, Article.retrieved_at, Article.content_purged_at)
        ),
        lock=lock,
    )
    try:
        expected_time = (
            datetime.fromisoformat(payload["article_retrieved_at"])
            if payload.get("article_retrieved_at")
            else None
        )
    except (ValueError, TypeError):
        return False
    actual_purge = (
        article.content_purged_at.isoformat()
        if article and article.content_purged_at
        else None
    )
    if actual_purge != payload.get("article_content_purged_at"):
        return False
    if (str(article.id) if article else None) != payload.get("article_id") or (
        article.retrieved_at if article else None
    ) != expected_time:
        return False
    state = _query(
        db, select(ItemIntelState).where(ItemIntelState.item_id == item_id), lock=lock
    )
    if state is None or state.revision != extraction_revision:
        return False
    if team_id is not None:
        if payload.get("team_intel_revision") is not None:
            team_state = _query(
                db,
                select(TeamIntelState).where(
                    TeamIntelState.team_id == team_id, TeamIntelState.item_id == item_id
                ),
                lock=lock,
            )
            if (
                team_state is None
                or team_state.revision != payload["team_intel_revision"]
            ):
                return False
        _indicators, policy_hash = team_indicator_snapshot(
            db,
            team_id=team_id,
            item_id=item_id,
            indicators=payload.get("indicators") or [],
            source_revision=source_revision,
            extraction_revision=extraction_revision,
            handling_label_id=db.scalar(
                select(Feed.handling_label_id).where(Feed.id == item.feed_id)
            ),
        )
        if policy_hash != payload.get("team_indicator_policy_hash"):
            return False
    return True
