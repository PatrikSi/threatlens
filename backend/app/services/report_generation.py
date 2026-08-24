from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.services.ai_config import ActiveAISettings, load_active_ai_settings
from app.services.ai_context_budget import (
    AIContextBudgetError,
    build_context_budget,
)
from app.services.ai_integration import (
    FEATURE_REPORT,
    AITaskRunStoppedError,
    request_ai_json_with_usage,
)
from app.services.ai_ops import get_ai_task_run_stop_reason, record_ai_task_event
from app.services.ai_provider_client import AIIntegrationError
from app.services.report_availability import (
    ReportingUnavailableError,
    ensure_reporting_available,
)
from app.services.report_sources import DETERMINISTIC_SECTION_KEYS
from app.services.report_execution import ReportGenerationOwnershipError
from app.services.report_prompt_budget import (
    CONTEXT_COMPACTION_WARNING,
    FINDINGS_COMPACTION_WARNING,
    ReportMessageBatchPlan,
    build_evidence_messages,
    build_section_message_plan,
    estimate_message_tokens,
    extend_evidence_message_batch_plan,
    fit_evidence_to_stage,
    plan_evidence_message_batches,
)


logger = logging.getLogger(__name__)
CITATION_PATTERN = re.compile(r"\[(S\d+)\]")


class ReportGenerationError(RuntimeError):
    def __init__(self, message: str, *, code: str = "generation_failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReportGenerationResult:
    report_id: uuid.UUID
    status: str
    model_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def generate_report(
    db: Session,
    *,
    report_id: uuid.UUID,
    task_run_id: uuid.UUID | None,
    execution_checkpoint: Callable[[], None] | None = None,
    execution_commit: Callable[[], None] | None = None,
) -> ReportGenerationResult:
    report = db.scalar(select(Report).where(Report.id == report_id).with_for_update())
    if report is None:
        raise ReportGenerationError("Report no longer exists.", code="report_not_found")
    if report.status == "ready":
        return ReportGenerationResult(
            report.id,
            report.status,
            report.model_calls,
            report.prompt_tokens or 0,
            report.completion_tokens or 0,
            report.total_tokens or 0,
        )
    if report.status == "running":
        raise ReportGenerationError(
            "Report generation was interrupted before it completed. Retry the report to start a fresh generation attempt.",
            code="generation_interrupted",
        )

    active = load_active_ai_settings(db)
    ensure_reporting_available(active)
    _raise_if_canceled(db, task_run_id)
    budget = build_context_budget(
        context_window_tokens=active.report_context_window_tokens,
        reserved_output_tokens=active.report_reserved_output_tokens,
        safety_percent=active.report_context_safety_percent,
    )
    report.provider = active.provider_type
    report.model = active.model
    report.context_window_tokens = budget.context_window_tokens
    db.add(report)
    sources = list(
        db.scalars(
            select(ReportSourceItem)
            .where(
                ReportSourceItem.report_id == report.id,
                ReportSourceItem.included.is_(True),
            )
            .order_by(ReportSourceItem.rank.asc())
        ).all()
    )
    sections = list(
        db.scalars(
            select(ReportSection)
            .where(ReportSection.report_id == report.id)
            .order_by(ReportSection.position.asc())
        ).all()
    )
    if not sources:
        raise ReportGenerationError(
            "The report has no included source evidence.", code="no_sources"
        )
    if not sections:
        raise ReportGenerationError(
            "The report has no enabled sections.", code="no_sections"
        )
    _commit_execution(db, execution_commit)
    counters = _UsageCounters()
    try:
        sources, evidence_plan = _prepare_runtime_evidence(
            db,
            active=active,
            report=report,
            sources=sources,
            sections=sections,
            budget=budget,
        )
        report.status = "running"
        report.generation_stage = "evidence_synthesis"
        report.started_at = report.started_at or datetime.now(timezone.utc)
        report.error_code = None
        report.error = None
        db.add(report)
        _record_stage(db, task_run_id, report, "evidence_synthesis")
        _check_execution(execution_checkpoint)
        _commit_execution(db, execution_commit)
        _check_execution(execution_checkpoint)

        findings = _synthesize_evidence_batches(
            db,
            active=active,
            report=report,
            sources=sources,
            batch_plan=evidence_plan,
            budget=budget,
            task_run_id=task_run_id,
            counters=counters,
            execution_checkpoint=execution_checkpoint,
            execution_commit=execution_commit,
        )
        report = db.get(Report, report_id)
        if report is None:
            raise ReportGenerationError(
                "Report was deleted while generation was running.",
                code="report_deleted",
            )
        report.generation_stage = "section_generation"
        db.add(report)
        _record_stage(db, task_run_id, report, "section_generation")
        _commit_execution(db, execution_commit)

        ordered_sections = _generation_order(sections)
        for section in ordered_sections:
            _check_execution(execution_checkpoint)
            _raise_if_canceled(db, task_run_id)
            if (
                counters.model_calls >= active.report_max_model_calls
                and section.section_key not in DETERMINISTIC_SECTION_KEYS
            ):
                raise ReportGenerationError(
                    "Report generation reached the configured model-call limit before all sections were complete.",
                    code="model_call_limit",
                )
            _generate_section(
                db,
                active=active,
                report=report,
                section=section,
                sources=sources,
                findings=findings,
                budget=budget,
                task_run_id=task_run_id,
                counters=counters,
                execution_checkpoint=execution_checkpoint,
                execution_commit=execution_commit,
            )

        report = db.get(Report, report_id)
        if report is None:
            raise ReportGenerationError(
                "Report was deleted while generation was running.",
                code="report_deleted",
            )
        _raise_if_canceled(db, task_run_id)
        _check_execution(execution_checkpoint)
        _finalize_ready_report(db, report=report, counters=counters)
        _record_stage(db, task_run_id, report, "ready")
        _commit_execution(db, execution_commit)
        return ReportGenerationResult(
            report.id,
            report.status,
            counters.model_calls,
            counters.prompt_tokens,
            counters.completion_tokens,
            counters.total_tokens,
        )
    except Exception as exc:
        if isinstance(exc, ReportGenerationOwnershipError):
            db.rollback()
            raise
        if isinstance(exc, AIIntegrationError):
            counters.model_calls += exc.attempt_count
        db.rollback()
        expected_error = isinstance(
            exc,
            (
                AIContextBudgetError,
                AIIntegrationError,
                AITaskRunStoppedError,
                ReportGenerationError,
                ReportingUnavailableError,
            ),
        )
        current = db.get(Report, report_id)
        if current is not None:
            canceled = getattr(exc, "code", None) == "canceled"
            current.status = "skipped" if canceled else "error"
            current.generation_stage = "canceled" if canceled else "failed"
            current.error_code = _generation_error_code(exc, expected=expected_error)
            current.error = (
                str(exc)[:4000]
                if expected_error
                else "Report generation failed unexpectedly. Review the AI worker logs and retry the report."
            )
            current.model_calls = counters.model_calls
            current.prompt_tokens = counters.prompt_tokens or None
            current.completion_tokens = counters.completion_tokens or None
            current.total_tokens = counters.total_tokens or None
            db.add(current)
            _record_stage(
                db,
                task_run_id,
                current,
                "canceled" if canceled else "failed",
                message=str(exc),
            )
            _commit_execution(db, execution_commit)
        raise


def _generation_error_code(exc: Exception, *, expected: bool) -> str:
    if not expected:
        return "internal_error"
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    return (
        "context_budget" if isinstance(exc, AIContextBudgetError) else "provider_error"
    )


def _raise_if_canceled(db: Session, task_run_id: uuid.UUID | None) -> None:
    if get_ai_task_run_stop_reason(db, run_id=task_run_id) == "canceled":
        raise ReportGenerationError("Report generation was canceled.", code="canceled")


def _check_execution(checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is not None:
        checkpoint()


def _commit_execution(
    db: Session,
    execution_commit: Callable[[], None] | None,
) -> None:
    if execution_commit is not None:
        execution_commit()
        return
    db.commit()


@dataclass
class _UsageCounters:
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, completion) -> None:
        self.model_calls += completion.attempt_count
        self.prompt_tokens += (
            completion.prompt_tokens or completion.prompt_char_count // 3 or 0
        )
        self.completion_tokens += (
            completion.completion_tokens or completion.response_char_count // 3 or 0
        )
        self.total_tokens += completion.total_tokens or (
            (completion.prompt_tokens or completion.prompt_char_count // 3 or 0)
            + (completion.completion_tokens or completion.response_char_count // 3 or 0)
        )


def _prepare_runtime_evidence(
    db: Session,
    *,
    active: ActiveAISettings,
    report: Report,
    sources: list[ReportSourceItem],
    sections: list[ReportSection],
    budget,
) -> tuple[list[ReportSourceItem], ReportMessageBatchPlan]:
    model_section_count = sum(
        section.section_key not in DETERMINISTIC_SECTION_KEYS for section in sections
    )
    max_batches = active.report_max_model_calls - model_section_count
    if max_batches < 1:
        raise AIContextBudgetError(
            "The enabled report sections use every allowed model call, leaving no call for evidence synthesis. "
            "Disable an AI-generated section or increase the report model-call limit."
        )

    selected: list[ReportSourceItem] = []
    batch_plan = plan_evidence_message_batches(
        [],
        prompt=report.prompt_config_json,
        generation_context=report.generation_context_json,
        budget=budget,
    )
    dropped = 0
    truncated = 0
    for source in sources:
        evidence, was_truncated = fit_evidence_to_stage(
            source.evidence_text,
            source_token_cap=active.report_source_token_cap,
            prompt=report.prompt_config_json,
            generation_context=report.generation_context_json,
            budget=budget,
        )
        candidate_plan = extend_evidence_message_batch_plan(
            batch_plan,
            evidence,
            prompt=report.prompt_config_json,
            generation_context=report.generation_context_json,
            budget=budget,
        )
        if candidate_plan.batch_count > max_batches:
            source.included = False
            source.exclusion_reason = "execution_context_budget"
            db.add(source)
            dropped += 1
            continue
        truncated += int(was_truncated)
        selected.append(source)
        batch_plan = candidate_plan

    if not selected:
        raise AIContextBudgetError(
            "No report source fits the current model context and model-call limits after adaptive truncation. "
            "Reduce the output reserve, disable an AI-generated section, or increase the context window."
        )

    report.included_source_count = len(selected)
    report.excluded_source_count = max(0, report.source_count - len(selected))
    report.estimated_input_tokens = batch_plan.estimated_input_tokens
    report.generation_batches = batch_plan.batch_count
    coverage = dict(report.coverage_json or {})
    coverage["included_sources"] = len(selected)
    coverage["omitted_sources"] = report.excluded_source_count
    coverage["coverage_percent"] = (
        round(100 * len(selected) / report.source_count, 1)
        if report.source_count
        else 100.0
    )
    report.coverage_json = coverage
    if batch_plan.context_compacted:
        _append_coverage_warning(report, CONTEXT_COMPACTION_WARNING)
    if dropped:
        _append_coverage_warning(
            report,
            f"Execution omitted {dropped:,} lower-ranked sources so serialized prompts fit the current model context and call limits.",
        )
    if truncated:
        _append_coverage_warning(
            report,
            "Source excerpts were tightened at execution to fit the current model context window.",
        )
    logger.info(
        "report_context_plan report_id=%s usable_input_tokens=%s fixed_prompt_tokens=%s "
        "peak_input_tokens=%s batches=%s selected_sources=%s dropped_sources=%s context_compacted=%s",
        report.id,
        budget.usable_input_tokens,
        batch_plan.fixed_prompt_tokens,
        batch_plan.largest_batch_input_tokens,
        batch_plan.batch_count,
        len(selected),
        dropped,
        batch_plan.context_compacted,
    )
    db.add(report)
    return selected, batch_plan


def _synthesize_evidence_batches(
    db: Session,
    *,
    active: ActiveAISettings,
    report: Report,
    sources: list[ReportSourceItem],
    batch_plan: ReportMessageBatchPlan,
    budget,
    task_run_id: uuid.UUID | None,
    counters: _UsageCounters,
    execution_checkpoint: Callable[[], None] | None,
    execution_commit: Callable[[], None] | None,
) -> list[dict]:
    findings: list[dict] = []
    known_citations = {source.citation_key for source in sources}
    for index, batch in enumerate(batch_plan.batches, start=1):
        _raise_if_canceled(db, task_run_id)
        if counters.model_calls >= active.report_max_model_calls:
            raise ReportGenerationError(
                "Evidence synthesis reached the configured model-call limit.",
                code="model_call_limit",
            )
        messages, _ = build_evidence_messages(
            prompt=report.prompt_config_json,
            generation_context=report.generation_context_json,
            evidence=batch,
            budget=budget,
        )
        _assert_messages_fit(messages, budget=budget)
        completion_tokens, retry_completion_tokens = _report_completion_limits(
            active=active,
            budget=budget,
            messages=messages,
            stage_cap=800,
        )
        completion = request_ai_json_with_usage(
            db,
            active,
            feature_type=FEATURE_REPORT,
            messages=messages,
            report_id=report.id,
            task_run_id=task_run_id,
            max_completion_tokens=completion_tokens,
            max_retry_completion_tokens=retry_completion_tokens,
            max_provider_attempts=active.report_max_model_calls - counters.model_calls,
            execution_checkpoint=execution_checkpoint,
            execution_commit=execution_commit,
        )
        counters.add(completion)
        _check_execution(execution_checkpoint)
        _raise_if_canceled(db, task_run_id)
        findings.extend(
            _normalize_findings(
                completion.payload.get("findings"), known_citations=known_citations
            )
        )
        _record_provider_progress(
            db, task_run_id, report, counters, stage=f"evidence_batch_{index}"
        )
        _commit_execution(db, execution_commit)
    if not findings:
        findings = [
            {"text": source.title_snapshot, "citations": [source.citation_key]}
            for source in sources[: min(20, len(sources))]
        ]
    return findings


def _generate_section(
    db: Session,
    *,
    active: ActiveAISettings,
    report: Report,
    section: ReportSection,
    sources: list[ReportSourceItem],
    findings: list[dict],
    budget,
    task_run_id: uuid.UUID | None,
    counters: _UsageCounters,
    execution_checkpoint: Callable[[], None] | None,
    execution_commit: Callable[[], None] | None,
) -> None:
    section = db.get(ReportSection, section.id)
    if section is None:
        raise ReportGenerationError(
            "A report section was deleted during generation.", code="section_deleted"
        )
    if section.section_key in DETERMINISTIC_SECTION_KEYS:
        body, key_points, citations = _deterministic_section(report, section, sources)
    else:
        section.status = "running"
        db.add(section)
        _commit_execution(db, execution_commit)
        known_citations = {source.citation_key for source in sources}
        section_config = next(
            (
                entry
                for entry in report.sections_config_json or []
                if entry.get("key") == section.section_key
            ),
            {},
        )
        message_plan = build_section_message_plan(
            section={
                "key": section.section_key,
                "title": section.title,
                "instructions": section_config.get("instructions"),
            },
            report={
                "title": report.title,
                "period_start": report.period_start.isoformat(),
                "period_end": report.period_end.isoformat(),
                "prompt": report.prompt_config_json,
                "generation_context": report.generation_context_json,
                "metrics": report.metrics_json,
            },
            findings=findings,
            budget=budget,
        )
        messages = message_plan.messages
        if message_plan.context_compacted:
            _append_coverage_warning(report, CONTEXT_COMPACTION_WARNING)
        if message_plan.omitted_findings:
            _append_coverage_warning(report, FINDINGS_COMPACTION_WARNING)
        _assert_messages_fit(messages, budget=budget)
        completion_tokens, retry_completion_tokens = _report_completion_limits(
            active=active,
            budget=budget,
            messages=messages,
        )
        completion = request_ai_json_with_usage(
            db,
            active,
            feature_type=FEATURE_REPORT,
            messages=messages,
            report_id=report.id,
            task_run_id=task_run_id,
            max_completion_tokens=completion_tokens,
            max_retry_completion_tokens=retry_completion_tokens,
            max_provider_attempts=active.report_max_model_calls - counters.model_calls,
            execution_checkpoint=execution_checkpoint,
            execution_commit=execution_commit,
        )
        counters.add(completion)
        _check_execution(execution_checkpoint)
        _raise_if_canceled(db, task_run_id)
        body = str(completion.payload.get("body_markdown") or "").strip()
        if not body:
            raise ReportGenerationError(
                f"The AI provider returned an empty {section.title} section.",
                code="invalid_provider_output",
            )
        citations = _valid_citations(
            completion.payload.get("citations"),
            body=body,
            known_citations=known_citations,
        )
        body = _remove_unknown_inline_citations(body, known_citations=known_citations)
        key_points = _string_list(completion.payload.get("key_points"), limit=12)

    section.body_markdown = body
    section.key_points_json = key_points
    section.citations_json = citations
    section.status = "ready"
    section.error = None
    db.add(section)
    report.model_calls = counters.model_calls
    report.prompt_tokens = counters.prompt_tokens or None
    report.completion_tokens = counters.completion_tokens or None
    report.total_tokens = counters.total_tokens or None
    report.generation_stage = f"section:{section.section_key}"
    if section.section_key == "executive_summary":
        report.summary_text = body
    db.add(report)
    _record_provider_progress(
        db, task_run_id, report, counters, stage=report.generation_stage
    )
    _check_execution(execution_checkpoint)
    _commit_execution(db, execution_commit)


def _deterministic_section(
    report: Report,
    section: ReportSection,
    sources: list[ReportSourceItem],
) -> tuple[str, list[str], list[str]]:
    included = [source for source in sources if source.included]
    if section.section_key == "scope_evidence":
        coverage = report.coverage_json or {}
        body = (
            f"This report covers **{report.period_start.date().isoformat()}** through "
            f"**{report.period_end.date().isoformat()}**. It uses {len(included):,} of "
            f"{report.source_count:,} matching articles ({coverage.get('coverage_percent', 0)}% coverage) from "
            f"{len((report.metrics_json or {}).get('feeds', {})):,} feeds. Source text was bounded by the configured "
            "context guardrails before AI processing."
        )
        return body, list(coverage.get("warnings") or []), []
    if section.section_key == "observables":
        entries: list[str] = []
        citations: list[str] = []
        for source in included:
            for ioc in source.iocs_snapshot_json or []:
                entries.append(
                    f"- `{ioc.get('type', 'observable')}` `{ioc.get('value', '')}` [{source.citation_key}]"
                )
                citations.append(source.citation_key)
        body = (
            "\n".join(entries[:500])
            or "No extracted observables were present in the selected source snapshot."
        )
        return body, [], list(dict.fromkeys(citations))
    lines = [
        f"- [{source.citation_key}] [{source.title_snapshot}]({source.url_snapshot}) - {source.feed_name_snapshot}"
        for source in included
    ]
    return "\n".join(lines), [], [source.citation_key for source in included]


def _generation_order(sections: list[ReportSection]) -> list[ReportSection]:
    return sorted(
        sections,
        key=lambda section: (
            section.section_key == "executive_summary",
            section.position,
        ),
    )


def _normalize_findings(value: object, *, known_citations: set[str]) -> list[dict]:
    if not isinstance(value, list):
        return []
    findings: list[dict] = []
    for entry in value[:100]:
        if isinstance(entry, str):
            text = entry.strip()
            citations = _valid_citations(
                None, body=text, known_citations=known_citations
            )
        elif isinstance(entry, dict):
            text = str(entry.get("text") or entry.get("finding") or "").strip()
            citations = _valid_citations(
                entry.get("citations"), body=text, known_citations=known_citations
            )
        else:
            continue
        if text and citations:
            findings.append(
                {
                    "text": _remove_unknown_inline_citations(text, known_citations),
                    "citations": citations,
                }
            )
    return findings


def _assert_messages_fit(messages: list[dict[str, str]], *, budget) -> None:
    token_count = estimate_message_tokens(messages)
    if token_count > budget.usable_input_tokens:
        raise AIContextBudgetError(
            f"A report stage is estimated at {token_count:,} input tokens, above the usable "
            f"{budget.usable_input_tokens:,}-token budget after adaptive compaction. "
            "Reduce the output reserve or increase the model context window."
        )


def _report_completion_limits(
    *,
    active: ActiveAISettings,
    budget,
    messages: list[dict[str, str]],
    stage_cap: int | None = None,
) -> tuple[int, int]:
    initial = min(active.report_reserved_output_tokens, active.max_completion_tokens)
    maximum = min(
        active.max_completion_tokens,
        budget.context_window_tokens
        - budget.safety_margin_tokens
        - budget.protocol_overhead_tokens
        - estimate_message_tokens(messages),
    )
    if stage_cap is not None:
        initial = min(initial, stage_cap)
        maximum = min(maximum, stage_cap)
    return initial, max(initial, maximum)


def _append_coverage_warning(report: Report, warning: str) -> None:
    coverage = dict(report.coverage_json or {})
    warnings = list(coverage.get("warnings") or [])
    if warning not in warnings:
        warnings.append(warning)
    coverage["warnings"] = warnings
    report.coverage_json = coverage


def _valid_citations(
    value: object, *, body: str, known_citations: set[str]
) -> list[str]:
    explicit = _string_list(value, limit=100)
    inline = CITATION_PATTERN.findall(body)
    return list(
        dict.fromkeys(
            citation for citation in [*explicit, *inline] if citation in known_citations
        )
    )


def _remove_unknown_inline_citations(body: str, known_citations: set[str]) -> str:
    return CITATION_PATTERN.sub(
        lambda match: match.group(0) if match.group(1) in known_citations else "", body
    )


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [text for entry in value[:limit] if (text := str(entry).strip())]


def _finalize_ready_report(
    db: Session, *, report: Report, counters: _UsageCounters
) -> None:
    report.status = "ready"
    report.generation_stage = "ready"
    report.generated_at = datetime.now(timezone.utc)
    report.model_calls = counters.model_calls
    report.prompt_tokens = counters.prompt_tokens or None
    report.completion_tokens = counters.completion_tokens or None
    report.total_tokens = counters.total_tokens or None
    report.error_code = None
    report.error = None
    report.citation_count = len(
        db.scalars(
            select(ReportSourceItem.citation_key).where(
                ReportSourceItem.report_id == report.id,
                ReportSourceItem.included.is_(True),
            )
        ).all()
    )
    db.add(report)
    if report.delivery_requested:
        from app.services.report_notifications import emit_report_ready_event

        emit_report_ready_event(db, report=report)


def _record_stage(
    db: Session,
    task_run_id: uuid.UUID | None,
    report: Report,
    stage: str,
    *,
    message: str | None = None,
) -> None:
    if task_run_id is None:
        return
    record_ai_task_event(
        db,
        run_id=task_run_id,
        event_type="report_stage",
        message=message,
        payload={"report_id": str(report.id), "stage": stage},
    )


def _record_provider_progress(
    db: Session,
    task_run_id: uuid.UUID | None,
    report: Report,
    counters: _UsageCounters,
    *,
    stage: str,
) -> None:
    if task_run_id is None:
        return
    record_ai_task_event(
        db,
        run_id=task_run_id,
        event_type="report_progress",
        payload={
            "report_id": str(report.id),
            "stage": stage,
            "model_calls": counters.model_calls,
            "prompt_tokens": counters.prompt_tokens,
            "completion_tokens": counters.completion_tokens,
        },
    )
