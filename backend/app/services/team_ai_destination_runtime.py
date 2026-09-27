"""Bind current team policy to the actual provider immediately before I/O."""

import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.ai_task_run import AITaskRun
from app.models.feed import Feed
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
from app.models.item import Item
from app.models.team_item_assessment import TeamItemAssessment
from app.services.ai_provider_client import AIIntegrationError
from app.services.export_job_access import ExportJobAccessDenied, load_export_sources
from app.services.team_ai_governance import assert_team_ai_destination


def enforce_task_team_destination(
    db: Session, *, task_run_id: uuid.UUID | None, provider_key: str
) -> None:
    if task_run_id is None:
        return
    run = db.get(AITaskRun, task_run_id)
    metadata = run.metadata_json if run is not None else None
    raw = metadata.get("team_id") if isinstance(metadata, dict) else None
    if raw is None:
        return
    try:
        team_id = uuid.UUID(str(raw))
    except (ValueError, TypeError):
        raise AIIntegrationError(
            "The queued AI task has an invalid team policy scope. Start a new task.",
            retryable=False,
            provider_io_outcome="not_sent",
        ) from None
    labels: set[uuid.UUID] = set()
    if run.task_type == "team_assessment":
        work = db.scalar(
            select(TeamItemAssessment).where(
                TeamItemAssessment.task_run_id == run.id,
                TeamItemAssessment.team_id == team_id,
            )
        )
        try:
            if work is None:
                raise ExportJobAccessDenied("Missing team assessment")
            sources = load_export_sources(work)
            if len(sources) != 1 or sources[0].item_id != work.item_id:
                raise ExportJobAccessDenied("Missing source provenance")
            labels.update(
                source.captured_label_id or QUARANTINE_HANDLING_LABEL_ID
                for source in sources
            )
            current = db.execute(
                select(Feed.handling_label_id)
                .join(Item, Item.feed_id == Feed.id)
                .where(Item.id == work.item_id)
            ).one_or_none()
            if current is None:
                raise ExportJobAccessDenied("Missing current source")
            labels.add(current.handling_label_id or QUARANTINE_HANDLING_LABEL_ID)
        except ExportJobAccessDenied as error:
            raise AIIntegrationError(
                "Team AI source policy is unavailable. Review source access and generate again.",
                retryable=False,
                provider_io_outcome="not_sent",
            ) from error
    assert_team_ai_destination(
        db, team_id=team_id, provider_key=provider_key, label_ids=labels
    )
