from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.models.report_template import ReportTemplate
from app.schemas.exports import ArticleExportFilters
from app.schemas.reports import (
    ReportCreateRequest,
    ReportDetailResponse,
    ReportListItem,
    ReportPromptConfig,
    ReportSectionConfig,
    ReportSectionResponse,
    ReportSourceResponse,
)
from app.services.ai_config import ActiveAISettings
from app.services.report_sources import ReportSourcePlan


class ReportStorageError(ValueError):
    pass


def create_report_from_plan(
    db: Session,
    *,
    user_id: uuid.UUID,
    payload: ReportCreateRequest,
    plan: ReportSourcePlan,
    template: ReportTemplate | None,
    active: ActiveAISettings,
    trigger_source: str = "manual",
    schedule_id: uuid.UUID | None = None,
    generation_key: str | None = None,
) -> Report:
    if not plan.included_sources:
        raise ReportStorageError("No matching articles fit the selected filters and AI context guardrails.")
    title = payload.title or _default_report_title(template, payload.period_start, payload.period_end)
    report = Report(
        template_id=template.id if template else payload.template_id,
        schedule_id=schedule_id,
        owner_user_id=user_id,
        title=title,
        report_type=template.report_type if template else "custom",
        status="queued",
        trigger_source=trigger_source,
        generation_stage="queued",
        generation_key=generation_key,
        period_start=payload.period_start,
        period_end=payload.period_end,
        filters_json=payload.filters.model_dump(mode="json"),
        prompt_config_json=payload.prompt.model_dump(mode="json"),
        sections_config_json=[section.model_dump(mode="json") for section in payload.sections],
        metrics_json=plan.metrics,
        coverage_json={
            "total_matches": plan.total_matches,
            "included_sources": len(plan.included_sources),
            "omitted_sources": plan.omitted_source_count,
            "coverage_percent": round(100 * len(plan.included_sources) / plan.total_matches, 1)
            if plan.total_matches
            else 100.0,
            "warnings": list(plan.warnings),
        },
        source_count=plan.total_matches,
        included_source_count=len(plan.included_sources),
        excluded_source_count=plan.omitted_source_count,
        estimated_input_tokens=plan.estimated_source_tokens,
        context_window_tokens=plan.budget.context_window_tokens,
        generation_batches=plan.batch_count,
        provider=active.provider_type,
        model=active.model,
        delivery_requested=payload.deliver_when_ready,
    )
    db.add(report)
    db.flush()
    _replace_report_sources(db, report=report, plan=plan)
    _replace_report_sections(db, report=report, sections=payload.sections)
    return report


def reset_report_for_retry(db: Session, *, report: Report) -> None:
    if report.status not in {"error", "skipped"}:
        raise ReportStorageError("Only failed or skipped reports can be retried.")
    report.status = "queued"
    report.trigger_source = "retry"
    report.generation_stage = "queued"
    report.error_code = None
    report.error = None
    report.started_at = None
    report.generated_at = None
    report.queued_at = datetime.now(timezone.utc)
    report.prompt_tokens = None
    report.completion_tokens = None
    report.total_tokens = None
    report.model_calls = 0
    for section in db.scalars(select(ReportSection).where(ReportSection.report_id == report.id)).all():
        section.status = "pending"
        section.body_markdown = ""
        section.key_points_json = []
        section.citations_json = []
        section.error = None
        db.add(section)
    db.add(report)


def report_list_item(report: Report) -> ReportListItem:
    return ReportListItem(
        id=report.id,
        template_id=report.template_id,
        schedule_id=report.schedule_id,
        owner_user_id=report.owner_user_id,
        title=report.title,
        report_type=report.report_type,
        status=report.status,
        trigger_source=report.trigger_source,
        generation_stage=report.generation_stage,
        period_start=report.period_start,
        period_end=report.period_end,
        source_count=report.source_count,
        included_source_count=report.included_source_count,
        model_calls=report.model_calls,
        provider=report.provider,
        model=report.model,
        error_code=report.error_code,
        error=report.error,
        generated_at=report.generated_at,
        created_at=report.created_at,
    )


def report_detail_response(db: Session, *, report: Report) -> ReportDetailResponse:
    sections = list(
        db.scalars(
            select(ReportSection)
            .where(ReportSection.report_id == report.id)
            .order_by(ReportSection.position.asc(), ReportSection.id.asc())
        ).all()
    )
    sources = list(
        db.scalars(
            select(ReportSourceItem)
            .where(ReportSourceItem.report_id == report.id)
            .order_by(ReportSourceItem.rank.asc(), ReportSourceItem.id.asc())
        ).all()
    )
    return ReportDetailResponse(
        **report_list_item(report).model_dump(),
        filters=ArticleExportFilters.model_validate(report.filters_json or {}),
        prompt=ReportPromptConfig.model_validate(report.prompt_config_json or {}),
        sections_config=[ReportSectionConfig.model_validate(entry) for entry in report.sections_config_json or []],
        metrics=dict(report.metrics_json or {}),
        coverage=dict(report.coverage_json or {}),
        summary_text=report.summary_text,
        estimated_input_tokens=report.estimated_input_tokens,
        prompt_tokens=report.prompt_tokens,
        completion_tokens=report.completion_tokens,
        total_tokens=report.total_tokens,
        context_window_tokens=report.context_window_tokens,
        generation_batches=report.generation_batches,
        delivery_requested=report.delivery_requested,
        sections=[
            ReportSectionResponse(
                key=section.section_key,
                title=section.title,
                position=section.position,
                status=section.status,
                body_markdown=section.body_markdown,
                key_points=list(section.key_points_json or []),
                citations=list(section.citations_json or []),
                error=section.error,
            )
            for section in sections
        ],
        sources=[
            ReportSourceResponse(
                citation_key=source.citation_key,
                item_id=source.item_id,
                included=source.included,
                rank=source.rank,
                exclusion_reason=source.exclusion_reason,
                title=source.title_snapshot,
                feed_name=source.feed_name_snapshot,
                url=source.url_snapshot,
                classification=source.classification_snapshot,
                relevance_score=source.relevance_score_snapshot,
                relevance_label=source.relevance_label_snapshot,
                published_at=source.published_at_snapshot,
                first_seen_at=source.first_seen_at_snapshot,
                tags=list(source.tags_snapshot_json or []),
                iocs=list(source.iocs_snapshot_json or []),
                estimated_tokens=source.estimated_tokens,
            )
            for source in sources
        ],
    )


def delete_report(db: Session, *, report: Report) -> None:
    db.delete(report)


def _replace_report_sources(db: Session, *, report: Report, plan: ReportSourcePlan) -> None:
    db.execute(delete(ReportSourceItem).where(ReportSourceItem.report_id == report.id))
    for rank, source in enumerate(plan.sources, start=1):
        record = source.record
        db.add(
            ReportSourceItem(
                report_id=report.id,
                item_id=record.id,
                citation_key=source.citation_key,
                included=source.included,
                rank=rank,
                exclusion_reason=source.exclusion_reason,
                title_snapshot=record.title,
                feed_name_snapshot=record.feed_name,
                url_snapshot=record.url,
                classification_snapshot=record.classification.primary_category if record.classification else None,
                relevance_score_snapshot=record.ai.relevance_score if record.ai else None,
                relevance_label_snapshot=record.ai.relevance_label if record.ai else None,
                published_at_snapshot=record.published_at,
                first_seen_at_snapshot=record.first_seen_at,
                tags_snapshot_json=[tag.name for tag in record.tags],
                iocs_snapshot_json=[
                    {"type": ioc.type, "value": ioc.value, "confidence": ioc.confidence} for ioc in record.iocs
                ],
                evidence_text=source.evidence_text,
                estimated_tokens=source.estimated_tokens,
            )
        )


def _replace_report_sections(
    db: Session,
    *,
    report: Report,
    sections: list[ReportSectionConfig],
) -> None:
    db.execute(delete(ReportSection).where(ReportSection.report_id == report.id))
    for position, section in enumerate((section for section in sections if section.enabled), start=1):
        db.add(
            ReportSection(
                report_id=report.id,
                section_key=section.key,
                title=section.title,
                position=position,
                status="pending",
            )
        )


def _default_report_title(
    template: ReportTemplate | None,
    period_start: datetime,
    period_end: datetime,
) -> str:
    name = template.name if template else "Intelligence Report"
    return f"{name}: {period_start.date().isoformat()} to {period_end.date().isoformat()}"
