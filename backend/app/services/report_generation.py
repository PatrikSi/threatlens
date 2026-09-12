from __future__ import annotations

from app.services.ai_provider_protocol import provider_report_context_budget, provider_output_ceiling

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ai_limits import MAX_AI_COMPLETION_TOKENS
from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.services.ai_config import ActiveAISettings, load_active_ai_settings
from app.services.ai_context_budget import (
    AIContextBudget,
    AIContextBudgetError,
)
from app.services.ai_integration import (
    FEATURE_REPORT,
    AITaskRunStoppedError,
    request_ai_json_with_usage,
)
from app.services.ai_ops import get_ai_task_run_stop_reason, record_ai_task_event
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_workflow_dispatch import AIWorkflowDeferred
from app.services.report_availability import (
    ReportingUnavailableError,
    ensure_reporting_available,
)
from app.services.report_sources import DETERMINISTIC_SECTION_KEYS
from app.services.report_execution import ReportGenerationOwnershipError
from app.services.report_evidence_contract import has_current_evidence_contract
from app.services.report_grounding import (
    NO_FINDINGS_BODY, ReportGroundingError, evidence_sources, report_stage_input,
    validate_findings, validate_section,
)
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

    # A rejected historical snapshot keeps its already-recorded usage. Current
    # attempts reconstruct usage from their paid-stage artifacts below.
    counters = _UsageCounters(
        model_calls=report.model_calls,
        prompt_tokens=report.prompt_tokens or 0,
        completion_tokens=report.completion_tokens or 0,
        total_tokens=report.total_tokens or 0,
    )
    try:
        _check_execution(execution_checkpoint)
        _raise_if_task_stopped(db, task_run_id)
        if not has_current_evidence_contract(report.coverage_json):
            raise ReportGenerationError(
                "This report snapshot predates the current source-evidence contract or uses an unknown version. "
                "Its frozen evidence cannot be verified safely. Please create a new report from current sources; "
                "retrying this snapshot will not rebuild its evidence.",
                code="source_snapshot_requires_rebuild",
            )
        counters = _UsageCounters()
        active = load_active_ai_settings(db, feature_type="report", task_run_id=task_run_id)
        ensure_reporting_available(active)
        budget = provider_report_context_budget(active)
        coverage = dict(report.coverage_json or {})
        coverage.pop("grounding", None)
        report.coverage_json = coverage
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
            _raise_if_task_stopped(db, task_run_id)
            if (
                counters.model_calls >= active.report_max_model_calls
                and section.section_key not in DETERMINISTIC_SECTION_KEYS
                and findings
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
        _raise_if_task_stopped(db, task_run_id)
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
    except AIWorkflowDeferred:
        db.rollback()
        _raise_if_task_stopped(db, task_run_id)
        _check_execution(execution_checkpoint)
        current = db.get(Report, report_id)
        if current is not None:
            current.status, current.generation_stage = "queued", "waiting_for_capacity"
            current.model_calls = counters.model_calls
            current.prompt_tokens = counters.prompt_tokens or None
            current.completion_tokens = counters.completion_tokens or None
            current.total_tokens = counters.total_tokens or None
            db.add(current)
            # The worker commits this state together with task deferral and
            # lease release; a crash cannot publish only half the transition.
        raise
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
                ReportGroundingError,
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


def _raise_if_task_stopped(db: Session, task_run_id: uuid.UUID | None) -> None:
    stop_reason = get_ai_task_run_stop_reason(db, run_id=task_run_id)
    if stop_reason == "canceled":
        raise ReportGenerationError("Report generation was canceled.", code="canceled")
    if stop_reason is not None:
        raise ReportGenerationError(
            "Report generation stopped because its task run was already settled. "
            "Review the AI task history before retrying the report.",
            code="task_stopped",
        )


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
    for index, batch in enumerate(batch_plan.batches, start=1):
        _raise_if_task_stopped(db, task_run_id)
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
        completion = _request_report_completion(
            db,
            active=active,
            budget=budget,
            messages=messages,
            report_id=report.id,
            task_run_id=task_run_id,
            provider_operation_scope=f"evidence_batch:{index}",
            max_provider_attempts=active.report_max_model_calls - counters.model_calls,
            execution_checkpoint=execution_checkpoint,
            execution_commit=execution_commit,
        )
        counters.add(completion)
        _check_execution(execution_checkpoint)
        _raise_if_task_stopped(db, task_run_id)
        stage = report_stage_input(messages)
        assert stage is not None
        batch_findings = validate_findings(completion.payload.get("findings"), sources=evidence_sources(stage))
        findings.extend(batch_findings)
        _record_grounding(report, findings=len(batch_findings), empty_batch=index if not batch_findings else None)
        if not batch_findings:
            _append_coverage_warning(report, f"Evidence batch {index} returned no supported findings; its sources do not support narrative conclusions.")
        _record_provider_progress(
            db, task_run_id, report, counters, stage=f"evidence_batch_{index}"
        )
        _commit_execution(db, execution_commit)
    if not findings:
        _append_coverage_warning(report, "Evidence synthesis produced no supported findings. Narrative sections disclose insufficient evidence; source titles were not substituted.")
        _commit_execution(db, execution_commit)
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
    elif not findings:
        body, key_points, citations = NO_FINDINGS_BODY, [], []
    else:
        if section.status != "ready":
            section.status = "running"
            db.add(section)
            _commit_execution(db, execution_commit)
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
        if message_plan.included_findings == 0:
            body = (
                "No supported findings fit this section's context budget. "
                "Increase the model context window or reduce the report output reserve to include the evidence."
            )
            key_points, citations = [], []
            _append_coverage_warning(report, f"Section {section.title} has no narrative findings because its evidence could not fit the context budget.")
            _record_grounding(report, degraded_section=section.section_key)
        else:
            _assert_messages_fit(messages, budget=budget)
            completion = _request_report_completion(
                db,
                active=active,
                budget=budget,
                messages=messages,
                report_id=report.id,
                task_run_id=task_run_id,
                provider_operation_scope=f"section:{section.id}",
                max_provider_attempts=active.report_max_model_calls - counters.model_calls,
                execution_checkpoint=execution_checkpoint,
                execution_commit=execution_commit,
            )
            counters.add(completion)
            _check_execution(execution_checkpoint)
            _raise_if_task_stopped(db, task_run_id)
            stage = report_stage_input(messages)
            assert stage is not None
            known_citations = {citation for finding in stage["findings"] for citation in finding["citations"]}
            grounded = validate_section(completion.payload, known_citations=known_citations)
            body, key_points, citations = grounded.body, grounded.key_points, grounded.citations
            _record_grounding(report, claim_blocks=grounded.claim_blocks)

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


def _assert_messages_fit(messages: list[dict[str, str]], *, budget) -> None:
    token_count = estimate_message_tokens(messages)
    if token_count > budget.usable_input_tokens:
        raise AIContextBudgetError(
            f"A report stage is estimated at {token_count:,} input tokens, above the usable "
            f"{budget.usable_input_tokens:,}-token budget after adaptive compaction. "
            "Reduce the output reserve or increase the model context window."
        )


def _request_report_completion(
    db: Session,
    *,
    active: ActiveAISettings,
    budget: AIContextBudget,
    messages: list[dict[str, str]],
    report_id: uuid.UUID,
    task_run_id: uuid.UUID | None,
    provider_operation_scope: str,
    max_provider_attempts: int,
    execution_checkpoint: Callable[[], None] | None,
    execution_commit: Callable[[], None] | None,
) -> AICompletionResult:
    initial, retry_ceiling = _report_completion_limits(
        active=active, budget=budget, messages=messages,
    )
    try:
        return request_ai_json_with_usage(
            db,
            active,
            feature_type=FEATURE_REPORT,
            messages=messages,
            report_id=report_id,
            task_run_id=task_run_id,
            provider_operation_scope=provider_operation_scope,
            max_completion_tokens=initial,
            max_retry_completion_tokens=retry_ceiling,
            max_provider_attempts=max_provider_attempts,
            execution_checkpoint=execution_checkpoint,
            execution_commit=execution_commit,
        )
    except AIIntegrationError as error:
        if error.retry_hint != "expand_completion_budget":
            raise
        input_tokens = estimate_message_tokens(messages)
        headroom = (
            budget.context_window_tokens - input_tokens
            - budget.safety_margin_tokens - budget.protocol_overhead_tokens
        )
        final_tokens = (error.request_payload or {}).get("max_completion_tokens", (error.request_payload or {}).get("max_tokens"))
        final_allowance = (
            f"{final_tokens:,}" if type(final_tokens) is int else "unavailable"
        )
        if retry_ceiling == headroom:
            limiting_factor = "The remaining context limits output for this call."
        elif retry_ceiling == MAX_AI_COMPLETION_TOKENS:
            limiting_factor = "The application output limit bounds retries for this call."
        else:
            limiting_factor = "The report/provider output settings bound retries for this call."
        diagnostic = (
            f"Report budget: context window {budget.context_window_tokens:,}; "
            f"estimated serialized input {input_tokens:,}; "
            f"safety reserve {budget.safety_margin_tokens:,}; "
            f"protocol reserve {budget.protocol_overhead_tokens:,}; "
            f"remaining output headroom {headroom:,} tokens. "
            f"Initial report allowance {initial:,}; final request allowance {final_allowance}; "
            f"retry ceiling {retry_ceiling:,} tokens; provider attempts {error.attempt_count}. "
            f"{limiting_factor}"
        )
        # Add context only after the request runtime has finished its retries and
        # settled its receipts. Retain the original exception and I/O metadata.
        error.args = (f"{error}\n\n{diagnostic}", *error.args[1:])
        raise


def _report_completion_limits(
    *,
    active: ActiveAISettings,
    budget: AIContextBudget,
    messages: list[dict[str, str]],
) -> tuple[int, int]:
    # The planner reserves this output budget independently of the provider's
    # default for enrichment and briefs. Retries may use remaining context up to
    # the greater of the report budget and that provider default.
    initial = active.report_reserved_output_tokens
    if not 256 <= initial <= MAX_AI_COMPLETION_TOKENS:
        raise AIContextBudgetError(
            f"The report output budget must be between 256 and {MAX_AI_COMPLETION_TOKENS:,} tokens."
        )
    maximum = min(
        max(initial, active.max_completion_tokens),
        provider_output_ceiling(active, messages),
        MAX_AI_COMPLETION_TOKENS,
        budget.context_window_tokens
        - budget.safety_margin_tokens
        - budget.protocol_overhead_tokens
        - estimate_message_tokens(messages),
    )
    if initial > maximum:
        raise AIContextBudgetError(
            "The report output budget does not fit the remaining model context. "
            "Increase the context window or reduce the report output budget."
        )
    return initial, maximum


def _append_coverage_warning(report: Report, warning: str) -> None:
    coverage = dict(report.coverage_json or {})
    warnings = list(coverage.get("warnings") or [])
    if warning not in warnings:
        warnings.append(warning)
    coverage["warnings"] = warnings
    report.coverage_json = coverage


def _record_grounding(
    report: Report, *, findings: int = 0, claim_blocks: int = 0,
    empty_batch: int | None = None,
    degraded_section: str | None = None,
) -> None:
    coverage = dict(report.coverage_json or {})
    grounding = dict(coverage.get("grounding") or {})
    grounding.update({
        "version": 1,
        "validated_findings": int(grounding.get("validated_findings", 0)) + findings,
        "cited_claim_blocks": int(grounding.get("cited_claim_blocks", 0)) + claim_blocks,
        "semantic_verification": False,
    })
    empty_batches = list(grounding.get("empty_batches") or [])
    if empty_batch is not None and empty_batch not in empty_batches:
        empty_batches.append(empty_batch)
    grounding["empty_batches"] = empty_batches
    degraded_sections = list(grounding.get("degraded_sections") or [])
    if degraded_section is not None and degraded_section not in degraded_sections:
        degraded_sections.append(degraded_section)
    grounding["degraded_sections"] = degraded_sections
    grounding["status"] = (
        "insufficient_evidence" if not grounding["validated_findings"]
        else "degraded" if empty_batches or degraded_sections else "checked"
    )
    coverage["grounding"] = grounding
    report.coverage_json = coverage


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
    from app.services.report_publication import publish_automatic_report

    if not report.review_required:
        publish_automatic_report(db, report)
    if report.delivery_requested and report.publication_status == "published":
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
