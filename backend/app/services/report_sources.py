from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.schemas.exports import ArticleExportFilters
from app.schemas.reports import (
    ReportContextEstimate,
    ReportPreviewItem,
    ReportPreviewResponse,
    ReportPromptConfig,
    ReportSectionConfig,
)
from app.services.ai_config import ActiveAISettings
from app.services.ai_context_budget import (
    AIContextBudget,
    build_context_budget,
    estimate_tokens,
    plan_evidence_batches,
    truncate_to_token_estimate,
)
from app.services.export_models import ExportRecord
from app.services.export_query import (
    build_export_query_context,
    build_preview_items,
    iter_export_records,
    load_export_counts,
    load_export_item_ids,
)


DETERMINISTIC_SECTION_KEYS = frozenset({"scope_evidence", "observables", "sources"})


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
    omitted_source_count: int
    warnings: tuple[str, ...]
    metrics: dict

    @property
    def included_sources(self) -> tuple[PlannedReportSource, ...]:
        return tuple(source for source in self.sources if source.included)


def filters_for_report_period(
    filters: ArticleExportFilters,
    *,
    period_start: datetime,
    period_end: datetime,
) -> ArticleExportFilters:
    payload = filters.model_dump(mode="python")
    payload["since"] = period_start
    payload["until"] = period_end
    return ArticleExportFilters.model_validate(payload)


def build_report_source_plan(
    db: Session,
    *,
    user_id: uuid.UUID,
    filters: ArticleExportFilters,
    excluded_item_ids: list[uuid.UUID],
    prompt: ReportPromptConfig,
    sections: list[ReportSectionConfig],
    active: ActiveAISettings,
) -> ReportSourcePlan:
    budget = build_context_budget(
        context_window_tokens=active.report_context_window_tokens,
        reserved_output_tokens=active.report_reserved_output_tokens,
        safety_percent=active.report_context_safety_percent,
    )
    fixed_prompt_tokens = _estimate_fixed_prompt_tokens(prompt, sections)
    batch_capacity = budget.usable_input_tokens - fixed_prompt_tokens
    if batch_capacity < 256:
        # Reuse the central error contract and actionable message.
        plan_evidence_batches([], budget=budget, fixed_prompt_tokens=fixed_prompt_tokens)

    context = build_export_query_context(user_id=user_id, filters=filters)
    counts = load_export_counts(db, context=context)
    excluded_ids = set(excluded_item_ids)
    load_limit = min(2000, active.report_max_sources + len(excluded_ids) + 250)
    item_ids = load_export_item_ids(db, context=context, limit=load_limit)
    records = list(iter_export_records(db, item_ids=item_ids, context=context, include_iocs=True))

    model_section_count = sum(
        1 for section in sections if section.enabled and section.key not in DETERMINISTIC_SECTION_KEYS
    )
    max_batches = max(1, active.report_max_model_calls - model_section_count)
    evidence_capacity = max_batches * batch_capacity
    selected_count = 0
    selected_tokens = 0
    planned: list[PlannedReportSource] = []
    context_omitted = 0
    source_cap = min(active.report_source_token_cap, batch_capacity)

    for record in records:
        citation_key = f"S{len(planned) + 1}"
        evidence, _truncated = truncate_to_token_estimate(
            _build_evidence_text(record, citation_key=citation_key),
            max_tokens=source_cap,
        )
        token_count = estimate_tokens(evidence)
        reason: str | None = None
        if record.id in excluded_ids:
            reason = "excluded_by_user"
        elif selected_count >= active.report_max_sources:
            reason = "source_limit"
        elif selected_tokens + token_count > evidence_capacity:
            reason = "context_budget"
            context_omitted += 1
        else:
            selected_count += 1
            selected_tokens += token_count
        planned.append(
            PlannedReportSource(
                record=record,
                citation_key=citation_key,
                evidence_text=evidence,
                estimated_tokens=token_count,
                included=reason is None,
                exclusion_reason=reason,
            )
        )

    included_evidence = [source.evidence_text for source in planned if source.included]
    batch_plan = plan_evidence_batches(
        included_evidence,
        budget=budget,
        fixed_prompt_tokens=fixed_prompt_tokens,
    )
    omitted = max(0, counts.total - selected_count)
    warnings: list[str] = []
    if counts.total > len(records):
        warnings.append(
            f"Only the highest-ranked {len(records):,} candidates were inspected; {counts.total - len(records):,} were outside the planning window."
        )
    if selected_count >= active.report_max_sources and counts.total > selected_count:
        warnings.append(f"The configured source limit selected {selected_count:,} of {counts.total:,} matching articles.")
    if context_omitted:
        warnings.append(
            f"The context and model-call guardrails omitted {context_omitted:,} candidate articles after the evidence budget was filled."
        )
    if any("truncated by context guardrail" in source.evidence_text for source in planned if source.included):
        warnings.append("Long source text is represented by bounded excerpts; titles, metadata, summaries, and citations remain intact.")
    if not selected_count and counts.total:
        warnings.append("No articles fit the current exclusions and context budget.")

    return ReportSourcePlan(
        sources=tuple(planned),
        total_matches=counts.total,
        articles_with_text=counts.with_article_text,
        items_with_iocs=counts.with_iocs,
        budget=budget,
        fixed_prompt_tokens=fixed_prompt_tokens,
        batch_count=batch_plan.batch_count,
        estimated_model_calls=batch_plan.batch_count + model_section_count,
        estimated_source_tokens=selected_tokens,
        omitted_source_count=omitted,
        warnings=tuple(warnings),
        metrics=_build_metrics([source.record for source in planned if source.included]),
    )


def report_preview_from_plan(plan: ReportSourcePlan, *, preview_limit: int) -> ReportPreviewResponse:
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


def _estimate_fixed_prompt_tokens(
    prompt: ReportPromptConfig,
    sections: list[ReportSectionConfig],
) -> int:
    serialized = json.dumps(
        {
            "audience": prompt.audience,
            "objective": prompt.objective,
            "tone": prompt.tone,
            "detail_level": prompt.detail_level,
            "custom_instructions": prompt.custom_instructions,
            "focus_topics": prompt.focus_topics,
            "excluded_topics": prompt.excluded_topics,
            "sections": [section.model_dump() for section in sections if section.enabled],
        },
        sort_keys=True,
    )
    return max(256, estimate_tokens(serialized) + 180)


def _build_evidence_text(record: ExportRecord, *, citation_key: str) -> str:
    date_value = record.published_at or record.first_seen_at
    classification = record.classification.primary_category if record.classification else "unclassified"
    ai_summary = record.ai.summary if record.ai and record.ai.summary else None
    article_text = record.article.text if record.article and record.article.text else None
    iocs = ", ".join(f"{ioc.type}:{ioc.value}" for ioc in record.iocs[:30])
    parts = [
        f"[{citation_key}] {record.title}",
        f"Feed: {record.feed_name}",
        f"Date: {date_value.isoformat()}",
        f"Classification: {classification}",
        f"Tags: {', '.join(tag.name for tag in record.tags) or 'none'}",
        f"AI relevance: {record.ai.relevance_label if record.ai else 'not scored'}"
        + (f" ({record.ai.relevance_score:.2f})" if record.ai and record.ai.relevance_score is not None else ""),
        f"Source URL: {record.url}",
    ]
    if ai_summary:
        parts.append(f"Existing grounded summary: {ai_summary}")
    if record.summary:
        parts.append(f"Publisher summary: {record.summary}")
    if article_text:
        parts.append(f"Extracted article text: {article_text}")
    if iocs:
        parts.append(f"Extracted observables: {iocs}")
    return "\n".join(parts)


def _build_metrics(records: list[ExportRecord]) -> dict:
    feeds = Counter(record.feed_name for record in records)
    classifications = Counter(
        record.classification.primary_category if record.classification else "unclassified" for record in records
    )
    relevance = Counter(record.ai.relevance_label if record.ai and record.ai.relevance_label else "not_scored" for record in records)
    tags = Counter(tag.name for record in records for tag in record.tags)
    ioc_types = Counter(ioc.type for record in records for ioc in record.iocs)
    return {
        "article_count": len(records),
        "articles_with_extracted_text": sum(bool(record.article and record.article.text) for record in records),
        "articles_with_iocs": sum(bool(record.iocs) for record in records),
        "ioc_count": sum(len(record.iocs) for record in records),
        "feeds": dict(feeds.most_common()),
        "classifications": dict(classifications.most_common()),
        "relevance_labels": dict(relevance.most_common()),
        "top_tags": dict(tags.most_common(20)),
        "ioc_types": dict(ioc_types.most_common()),
    }
