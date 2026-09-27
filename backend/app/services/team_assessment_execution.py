"""Authorization and provenance fences for durable team assessments.

Lock order: IAM/data policy, accepting actor/credential, team, assessment,
item/article, then AI run/receipt. The shared team lock protects even an absent
context row, because context writes first take the team's exclusive lock.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ai_settings import AISettings
from app.models.ai_task_run import AITaskRun
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.team_item_assessment import TeamItemAssessment
from app.schemas.team_ai_context import TeamAIContextResponse
from app.services.ai_egress_data_policy import AIEgressPolicyError, lock_ai_egress_policy_fence
from app.services.ai_execution_ownership import require_ai_execution
from app.services.ai_ops import ai_task_run_stop_reason
from app.services.data_access_policy import handling_label_access_predicate
from app.services.export_job_access import (
    ExportJobAccessDenied,
    assert_export_sources_visible,
    authorize_export_job,
    load_export_sources,
)
from app.services.team_access import lock_team_for_current_access, team_access_predicate
from app.services.team_ai_context import team_ai_context_snapshot

FEATURE = "team_assessment"
REQUIRED_PERMISSIONS = ("read:items", "read:teams", "write:teams")


@dataclass(frozen=True)
class AssessmentSource:
    title: str
    summary: str
    article_text: str
    article_id: uuid.UUID | None
    article_retrieved_at: datetime | None
    source_version: int
    truncated: bool
    evidence_selection: dict = field(default_factory=dict)


def _blocked(message: str) -> AIEgressPolicyError:
    return AIEgressPolicyError(message, retryable=False)


def fence_team_assessment(
    db: Session, *, run_id: uuid.UUID,
) -> tuple[TeamItemAssessment, TeamAIContextResponse, AssessmentSource, bool]:
    """Recheck the accepting credential, membership, input versions and delivery.

    All rows remain locked until the surrounding request/receipt transaction
    finishes. Call again after each commit and before publication.
    """
    lock_ai_egress_policy_fence(db)
    work = db.scalar(select(TeamItemAssessment).where(
        TeamItemAssessment.task_run_id == run_id,
    ).execution_options(populate_existing=True))
    if work is None:
        raise _blocked("This assessment was replaced or removed. Generate a new assessment.")
    try:
        _authorization, access = authorize_export_job(
            db, work, lock=True, required_permissions=REQUIRED_PERMISSIONS,
        )
    except ExportJobAccessDenied as exc:
        raise _blocked("The accepting session or token no longer authorizes this team assessment. Sign in and generate again.") from exc
    if lock_team_for_current_access(db, team_id=work.team_id, user_id=work.principal_id) is None:
        raise _blocked("Team access changed before assessment generation. Review your membership and try again.")
    work = db.scalar(select(TeamItemAssessment).where(
        TeamItemAssessment.id == work.id,
        TeamItemAssessment.task_run_id == run_id,
    ).with_for_update().execution_options(populate_existing=True))
    if work is None:
        raise _blocked("This assessment was replaced. Generate a new assessment.")
    context = team_ai_context_snapshot(db, team_id=work.team_id)
    if context.version != work.context_version:
        raise _blocked("The team AI context changed after this assessment was queued. Review it and generate again.")
    item = db.execute(select(
        Item.id, Item.classification_required_version,
        func.left(Item.title, 1000).label("title"),
        func.left(Item.summary, 2000).label("summary"),
    ).join(Feed, Feed.id == Item.feed_id).where(
        Item.id == work.item_id,
        handling_label_access_predicate(Feed.handling_label_id, access),
    ).with_for_update(of=Item)).one_or_none()
    if item is None:
        raise _blocked("The source article is unavailable under the current access policy.")
    try:
        captured_sources = load_export_sources(work)
        if len(captured_sources) != 1 or captured_sources[0].item_id != work.item_id:
            raise ExportJobAccessDenied("The accepting source snapshot is unavailable")
        assert_export_sources_visible(db, work, access)
    except ExportJobAccessDenied as exc:
        raise _blocked("The source article's captured handling access changed. Review access and generate again.") from exc
    article = db.execute(select(
        Article.id, Article.retrieved_at,
        func.left(Article.text, 16000).label("text"),
        func.length(Article.text).label("length"),
    ).where(Article.item_id == work.item_id).with_for_update(read=True)).one_or_none()
    article_id = article.id if article else None
    retrieved_at = article.retrieved_at if article else None
    if (work.source_version != item.classification_required_version
            or work.article_id != article_id or work.article_retrieved_at != retrieved_at):
        raise _blocked("The source article changed after this assessment was queued. Generate again using the current evidence.")
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id)
                    .with_for_update().execution_options(populate_existing=True))
    if run is None:
        raise _blocked("The assessment task no longer exists. Generate again.")
    require_ai_execution(run)
    metadata = run.metadata_json or {}
    if (run.task_type != FEATURE or run.item_id != work.item_id
            or run.actor_user_id != work.principal_id or run.status != "running"
            or metadata.get("assessment_id") != str(work.id)
            or metadata.get("team_id") != str(work.team_id)
            or type(metadata.get("assessment_version")) is not int
            or metadata["assessment_version"] != work.version
            or ai_task_run_stop_reason(run) is not None):
        raise _blocked("This assessment task was canceled or replaced.")
    settings = db.scalar(select(AISettings).where(AISettings.singleton_key == 1)
                         .execution_options(populate_existing=True))
    hunts_enabled = bool(settings and settings.hunt_suggestions_enabled)
    if hunts_enabled != bool(metadata.get("hunt_suggestions_enabled")):
        raise _blocked("Hunt suggestion settings changed after this assessment was queued. Generate again.")
    # Expiry is clock based and can happen while waiting for any of the locks.
    if not db.scalar(select(team_access_predicate(work.team_id, work.principal_id))):
        raise _blocked("Your team membership expired. Refresh your session before generating again.")
    try:
        authorize_export_job(db, work, lock=True, required_permissions=REQUIRED_PERMISSIONS)
    except ExportJobAccessDenied as exc:
        raise _blocked("The accepting session or token expired while waiting. Sign in and generate again.") from exc
    from app.models.item_ai_enrichment import ItemAIEnrichment
    from app.services.team_evidence_selection import select_assessment_passages
    extraction = db.scalar(select(ItemAIEnrichment.structured_extraction_json).where(
        ItemAIEnrichment.item_id == work.item_id, ItemAIEnrichment.status == "ready"))
    evidence_text, evidence_selection = select_assessment_passages(extraction,
        source_version=item.classification_required_version, article_id=str(article_id),
        retrieved_at=retrieved_at.isoformat() if retrieved_at else "",
        context=context.model_dump(), prefix=(article.text or "") if article else "")
    return work, context, AssessmentSource(
        title=item.title or "", summary=item.summary or "", article_text=evidence_text, evidence_selection=evidence_selection,
        article_id=article_id, article_retrieved_at=retrieved_at,
        source_version=item.classification_required_version,
        truncated=bool(article and (article.length or 0) > 16000),
    ), hunts_enabled
