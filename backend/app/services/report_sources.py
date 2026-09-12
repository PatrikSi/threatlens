from __future__ import annotations

from app.services.ai_provider_protocol import provider_report_context_budget

import uuid
import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.orm import Session

from app.schemas.exports import ArticleExportFilters
from app.schemas.reports import (
    ReportArticleFilters,
    ReportContextEstimate,
    ReportPreviewItem,
    ReportPreviewResponse,
    ReportPromptConfig,
    ReportSectionConfig,
)
from app.services.ai_config import ActiveAISettings
from app.services.ai_context_budget import (
    AIContextBudget,
    AIContextBudgetError,
    estimate_tokens,
)
from app.services.ai_prompting import build_company_context
from app.services.ai_enrichment_provenance import STALE_ENRICHMENT_WARNING
from app.services.data_access_policy import DataAccessContext
from app.services.export_models import ExportRecord
from app.services.export_artifacts import ExportSizeLimitError
from app.services.export_query import (
    ExportTextProjection,
    build_export_query_context,
    build_preview_items,
    iter_export_records,
    load_export_counts,
    load_export_item_ids,
)
from app.services.report_prompt_budget import (
    CONTEXT_COMPACTION_WARNING,
    extend_evidence_message_batch_plan,
    fit_evidence_to_stage,
    plan_evidence_message_batches,
)


DETERMINISTIC_SECTION_KEYS = frozenset({"scope_evidence", "observables", "sources"})
REPORT_SOURCE_PAYLOAD_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class PlannedReportSource:
    record: ExportRecord
    citation_key: str
    evidence_text: str
    estimated_tokens: int
    included: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class ReportSourcePlan:
    sources: tuple[PlannedReportSource, ...]
    total_matches: int
    articles_with_text: int
    items_with_iocs: int
    budget: AIContextBudget
    fixed_prompt_tokens: int
    batch_count: int
    estimated_model_calls: int
    estimated_source_tokens: int
    largest_batch_input_tokens: int
    omitted_source_count: int
    warnings: tuple[str, ...]
    metrics: dict
    data_policy_revision: int

    @property
    def included_sources(self) -> tuple[PlannedReportSource, ...]:
        return tuple(source for source in self.sources if source.included)


def filters_for_report_period(
    filters: ReportArticleFilters,
    *,
    period_start: datetime,
    period_end: datetime,
) -> ReportArticleFilters:
    payload = filters.model_dump(mode="python")
    payload["since"] = period_start
    payload["until"] = period_end
    return ReportArticleFilters.model_validate(payload)


def build_report_source_plan(
    db: Session,
    *,
    user_id: uuid.UUID,
    filters: ArticleExportFilters,
    excluded_item_ids: list[uuid.UUID],
    prompt: ReportPromptConfig,
    sections: list[ReportSectionConfig],
    active: ActiveAISettings,
    data_access: DataAccessContext,
) -> ReportSourcePlan:
    budget = provider_report_context_budget(active)
    prompt_payload = prompt.model_dump(mode="json")
    generation_context = {
        "company_context": build_company_context(active)
        if prompt.use_company_context
        else {},
        "global_instructions": active.global_instructions,
    }
    empty_batch_plan = plan_evidence_message_batches(
        [],
        prompt=prompt_payload,
        generation_context=generation_context,
        budget=budget,
    )

    context = build_export_query_context(
        user_id=user_id,
        filters=filters,
        data_access=data_access,
    )
    counts = load_export_counts(db, context=context)
    excluded_ids = set(excluded_item_ids)
    load_limit = min(2000, active.report_max_sources + len(excluded_ids) + 250)
    item_ids = load_export_item_ids(db, context=context, limit=load_limit)
    records = _iter_report_records(
        db,
        item_ids=item_ids,
        context=context,
        text_projection=ExportTextProjection(
            # The estimator charges at least one token per 3.2 characters. One
            # extra character guarantees oversized sources retain the existing
            # explicit truncation marker without fetching their complete body.
            character_limit=math.ceil(active.report_source_token_cap * 3.2) + 1,
            excluded_item_ids=frozenset(excluded_ids),
        ),
    )

    model_section_count = sum(
        1
        for section in sections
        if section.enabled and section.key not in DETERMINISTIC_SECTION_KEYS
    )
    max_batches = active.report_max_model_calls - model_section_count
    if max_batches < 1:
        raise AIContextBudgetError(
            "The enabled report sections use every allowed model call, leaving no call for evidence synthesis. "
            "Disable an AI-generated section or increase the report model-call limit."
        )
    selected_count = 0
    selected_tokens = 0
    planned: list[PlannedReportSource] = []
    context_omitted = 0
    batch_plan = empty_batch_plan

    for record in records:
        citation_key = f"S{len(planned) + 1}"
        evidence = ""
        token_count = 0
        reason: str | None = None
        if record.id in excluded_ids:
            reason = "excluded_by_user"
        elif selected_count >= active.report_max_sources:
            reason = "source_limit"
        else:
            evidence, _truncated = fit_evidence_to_stage(
                _build_evidence_text(record, citation_key=citation_key),
                source_token_cap=active.report_source_token_cap,
                prompt=prompt_payload,
                generation_context=generation_context,
                budget=budget,
            )
            token_count = estimate_tokens(evidence)
            candidate_plan = extend_evidence_message_batch_plan(
                batch_plan,
                evidence,
                prompt=prompt_payload,
                generation_context=generation_context,
                budget=budget,
            )
            if candidate_plan.batch_count > max_batches:
                reason = "context_budget"
                context_omitted += 1
            else:
                selected_count += 1
                selected_tokens += token_count
                batch_plan = candidate_plan
        planned.append(
            PlannedReportSource(
                record=_compact_source_record(record),
                citation_key=citation_key,
                evidence_text=evidence if reason is None else "",
                estimated_tokens=token_count,
                included=reason is None,
                exclusion_reason=reason,
            )
        )

    omitted = max(0, counts.total - selected_count)
    warnings: list[str] = []
    if any(source.included and source.record.ai and not source.record.ai.source_current for source in planned):
        warnings.append(STALE_ENRICHMENT_WARNING)
    if batch_plan.context_compacted:
        warnings.append(CONTEXT_COMPACTION_WARNING)
    if counts.total > len(planned):
        warnings.append(
            f"Only the highest-ranked {len(planned):,} candidates were inspected; {counts.total - len(planned):,} were outside the planning window."
        )
    if selected_count >= active.report_max_sources and counts.total > selected_count:
        warnings.append(
            f"The configured source limit selected {selected_count:,} of {counts.total:,} matching articles."
        )
    if context_omitted:
        warnings.append(
            f"The context and model-call guardrails omitted {context_omitted:,} candidate articles after the evidence budget was filled."
        )
    if any(
        "truncated by context guardrail" in source.evidence_text.lower()
        for source in planned
        if source.included
    ):
        warnings.append(
            "Long source text and summaries are represented by bounded excerpts; titles, metadata, and citations remain intact."
        )
    if not selected_count and counts.total:
        warnings.append("No articles fit the current exclusions and context budget.")

    return ReportSourcePlan(
        sources=tuple(planned),
        total_matches=counts.total,
        articles_with_text=counts.with_article_text,
        items_with_iocs=counts.with_iocs,
        budget=budget,
        fixed_prompt_tokens=batch_plan.fixed_prompt_tokens,
        batch_count=batch_plan.batch_count,
        estimated_model_calls=batch_plan.batch_count + model_section_count,
        estimated_source_tokens=selected_tokens,
        omitted_source_count=omitted,
        warnings=tuple(warnings),
        metrics=_build_metrics(
            [source.record for source in planned if source.included]
        ),
        largest_batch_input_tokens=batch_plan.largest_batch_input_tokens,
        data_policy_revision=data_access.policy_revision,
    )


def _iter_report_records(db: Session, **kwargs):
    try:
        yield from iter_export_records(
            db,
            include_iocs=True,
            max_payload_bytes=REPORT_SOURCE_PAYLOAD_MAX_BYTES,
            **kwargs,
        )
    except ExportSizeLimitError as exc:
        raise AIContextBudgetError(
            "A source exceeds the report planning byte budget. Exclude the source or narrow the selection."
        ) from exc


def _compact_source_record(record: ExportRecord) -> ExportRecord:
    """Keep citation/preview metadata, with evidence as the sole retained body."""
    return replace(
        record,
        summary=None,
        article=replace(record.article, text=None) if record.article else None,
        ai=replace(record.ai, summary=None) if record.ai else None,
    )


def report_preview_from_plan(
    plan: ReportSourcePlan, *, preview_limit: int
) -> ReportPreviewResponse:
    preview_sources = list(plan.sources[:preview_limit])
    preview_items = build_preview_items([source.record for source in preview_sources])
    selected_by_id = {source.record.id: source for source in preview_sources}
    return ReportPreviewResponse(
        total_matches=plan.total_matches,
        articles_with_text=plan.articles_with_text,
        items_with_iocs=plan.items_with_iocs,
        items=[
            ReportPreviewItem(
                **item.model_dump(),
                estimated_tokens=selected_by_id[item.id].estimated_tokens,
                selected=selected_by_id[item.id].included,
                exclusion_reason=selected_by_id[item.id].exclusion_reason,
            )
            for item in preview_items
        ],
        estimate=ReportContextEstimate(
            context_window_tokens=plan.budget.context_window_tokens,
            reserved_output_tokens=plan.budget.reserved_output_tokens,
            safety_margin_tokens=plan.budget.safety_margin_tokens,
            usable_input_tokens=plan.budget.usable_input_tokens,
            estimated_source_tokens=plan.estimated_source_tokens,
            estimated_fixed_prompt_tokens=plan.fixed_prompt_tokens,
            estimated_peak_input_tokens=plan.largest_batch_input_tokens,
            estimated_batches=plan.batch_count,
            estimated_model_calls=plan.estimated_model_calls,
            selected_source_count=len(plan.included_sources),
            omitted_source_count=plan.omitted_source_count,
            coverage_percent=round(
                100 * len(plan.included_sources) / plan.total_matches, 1
            )
            if plan.total_matches
            else 100.0,
            warnings=list(plan.warnings),
        ),
    )


def _build_evidence_text(record: ExportRecord, *, citation_key: str) -> str:
    date_value = record.published_at or record.first_seen_at
    classification = (
        record.classification.primary_category
        if record.classification
        else "unclassified"
    )
    ai_summary = record.ai.summary if record.ai and record.ai.source_current and record.ai.status == "ready" else None
    article_text = (
        record.article.text if record.article and record.article.text else None
    )
    iocs = ", ".join(f"{ioc.type}:{ioc.value}" for ioc in record.iocs[:30])
    parts = [
        f"[{citation_key}] {record.title}",
        f"Feed: {record.feed_name}",
        f"Date: {date_value.isoformat()}",
        f"Classification: {classification}",
        f"Tags: {', '.join(tag.name for tag in record.tags) or 'none'}",
        f"AI relevance: {record.ai.relevance_label if record.ai and record.ai.source_current else 'not scored'}"
        + (
            f" ({record.ai.relevance_score:.2f})"
            if record.ai and record.ai.source_current and record.ai.relevance_score is not None
            else ""
        ),
        f"Source URL: {record.url}",
    ]
    if record.summary:
        parts.append(f"Publisher summary: {record.summary}")
    if article_text:
        parts.append(f"Extracted article text: {article_text}")
    if ai_summary:
        parts.append(f"Prior AI summary (source version checked; not independently verified): {ai_summary}")
    if iocs:
        parts.append(f"Extracted observables: {iocs}")
    return "\n".join(parts)


def _build_metrics(records: list[ExportRecord]) -> dict:
    feeds = Counter(record.feed_name for record in records)
    classifications = Counter(
        record.classification.primary_category
        if record.classification
        else "unclassified"
        for record in records
    )
    relevance = Counter(
        record.ai.relevance_label
        if record.ai and record.ai.source_current and record.ai.relevance_label
        else "not_scored"
        for record in records
    )
    tags = Counter(tag.name for record in records for tag in record.tags)
    ioc_types = Counter(ioc.type for record in records for ioc in record.iocs)
    return {
        "article_count": len(records),
        "articles_with_extracted_text": sum(
            bool(
                record.article
                and (record.article.text_available or record.article.text)
            )
            for record in records
        ),
        "articles_with_iocs": sum(bool(record.iocs) for record in records),
        "ioc_count": sum(len(record.iocs) for record in records),
        "feeds": dict(feeds.most_common()),
        "classifications": dict(classifications.most_common()),
        "relevance_labels": dict(relevance.most_common()),
        "top_tags": dict(tags.most_common(20)),
        "ioc_types": dict(ioc_types.most_common()),
    }
