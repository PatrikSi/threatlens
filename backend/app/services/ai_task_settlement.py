from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.report import Report
from app.services.ai_ops_common import (
    AI_PROVIDER_CLAIM_DAILY_BRIEF,
    AI_PROVIDER_CLAIM_ITEM_ENRICHMENT,
    AI_PROVIDER_CLAIM_METADATA_KEY,
    AI_STATUS_ERROR,
    AI_STATUS_QUEUED,
    AI_STATUS_RUNNING,
    AI_STATUS_SKIPPED,
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TASK_TYPE_REPORT,
    _coerce_utc,
)


def settle_pending_ai_resource(
    db: Session,
    *,
    run: AITaskRun,
    status: str,
    reason: str | None,
    error: str | None,
    settled_at: datetime,
) -> None:
    if status != AI_STATUS_ERROR and reason != "canceled":
        return
    if run.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT:
        _settle_item_enrichment(
            db, run=run, reason=reason, error=error, settled_at=settled_at
        )
    elif run.task_type == AI_TASK_TYPE_DAILY_BRIEF:
        _settle_daily_brief(
            db, run=run, reason=reason, error=error, settled_at=settled_at
        )
    elif run.task_type == AI_TASK_TYPE_REPORT:
        _settle_report(db, run=run, reason=reason, error=error, settled_at=settled_at)


def _settle_item_enrichment(
    db: Session,
    *,
    run: AITaskRun,
    reason: str | None,
    error: str | None,
    settled_at: datetime,
) -> None:
    if run.item_id is None:
        return
    enrichment = db.scalar(
        select(ItemAIEnrichment)
        .where(ItemAIEnrichment.item_id == run.item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if enrichment is None or enrichment.status != "pending":
        return
    if not _provider_claim_matches(
        run,
        resource_type=AI_PROVIDER_CLAIM_ITEM_ENRICHMENT,
        resource_id=run.item_id,
        resource_updated_at=enrichment.updated_at,
    ):
        return
    enrichment.status = AI_STATUS_ERROR
    enrichment.error = error or reason or "task_failed"
    enrichment.generated_at = settled_at
    db.add(enrichment)


def _settle_daily_brief(
    db: Session,
    *,
    run: AITaskRun,
    reason: str | None,
    error: str | None,
    settled_at: datetime,
) -> None:
    if run.daily_brief_id is None:
        return
    brief = db.scalar(
        select(AIDailyBrief)
        .where(AIDailyBrief.id == run.daily_brief_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if brief is None or brief.status != "pending":
        return
    if not _provider_claim_matches(
        run,
        resource_type=AI_PROVIDER_CLAIM_DAILY_BRIEF,
        resource_id=run.daily_brief_id,
        resource_updated_at=brief.updated_at,
    ):
        return
    brief.status = AI_STATUS_ERROR
    brief.error = error or reason or "task_failed"
    brief.generated_at = settled_at
    db.add(brief)


def _settle_report(
    db: Session,
    *,
    run: AITaskRun,
    reason: str | None,
    error: str | None,
    settled_at: datetime,
) -> None:
    if run.report_id is None:
        return
    report = db.scalar(
        select(Report)
        .where(Report.id == run.report_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if report is None or report.status not in {AI_STATUS_QUEUED, AI_STATUS_RUNNING}:
        return
    if reason == "canceled":
        report.status = AI_STATUS_SKIPPED
        report.generation_stage = "canceled"
        report.error_code = "canceled"
        report.error = "Report generation was canceled."
    else:
        report.status = AI_STATUS_ERROR
        report.generation_stage = "failed"
        report.error_code = str(reason or "task_failed")[:64]
        report.error = (
            error
            or "Report generation stopped before completion. Review the AI task history and retry."
        )
    report.generated_at = settled_at
    db.add(report)


def _provider_claim_matches(
    run: AITaskRun,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    resource_updated_at: datetime,
) -> bool:
    raw_claim = (run.metadata_json or {}).get(AI_PROVIDER_CLAIM_METADATA_KEY)
    if raw_claim is None:
        return True
    if not isinstance(raw_claim, dict):
        return False
    return (
        raw_claim.get("resource_type") == resource_type
        and raw_claim.get("resource_id") == str(resource_id)
        and raw_claim.get("updated_at") == _coerce_utc(resource_updated_at).isoformat()
    )
