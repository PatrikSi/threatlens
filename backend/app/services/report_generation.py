from __future__ import annotations

import json
import logging
import re
import uuid
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
    estimate_tokens,
    plan_evidence_batches,
)
from app.services.ai_integration import FEATURE_REPORT, request_ai_json_with_usage
from app.services.ai_ops import record_ai_task_event
from app.services.ai_provider_client import AIIntegrationError
from app.services.report_sources import DETERMINISTIC_SECTION_KEYS


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
) -> ReportGenerationResult:
    report = db.scalar(select(Report).where(Report.id == report_id).with_for_update())
    if report is None:
        raise ReportGenerationError("Report no longer exists.", code="report_not_found")
    if report.status == "ready":
        return ReportGenerationResult(report.id, report.status, report.model_calls, report.prompt_tokens or 0, report.completion_tokens or 0, report.total_tokens or 0)
    if report.status == "running" and report.started_at and report.started_at < datetime.now(timezone.utc):
        # The task run is the ownership record. A retry intentionally resumes by replacing section output.
        logger.info("report_generation_resuming report_id=%s stage=%s", report.id, report.generation_stage)

    active = load_active_ai_settings(db)
    _validate_reporting_available(active)
    budget = build_context_budget(
        context_window_tokens=active.report_context_window_tokens,
        reserved_output_tokens=active.report_reserved_output_tokens,
        safety_percent=active.report_context_safety_percent,
    )
    sources = list(
        db.scalars(
            select(ReportSourceItem)
            .where(ReportSourceItem.report_id == report.id, ReportSourceItem.included.is_(True))
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
        raise ReportGenerationError("The report has no included source evidence.", code="no_sources")
    if not sections:
        raise ReportGenerationError("The report has no enabled sections.", code="no_sections")

    report.status = "running"
    report.generation_stage = "evidence_synthesis"
    report.started_at = report.started_at or datetime.now(timezone.utc)
    report.error_code = None
    report.error = None
    db.add(report)
    _record_stage(db, task_run_id, report, "evidence_synthesis")
    db.commit()

    counters = _UsageCounters()
    try:
        findings = _synthesize_evidence_batches(
            db,
            active=active,
            report=report,
            sources=sources,
            budget=budget,
            task_run_id=task_run_id,
            counters=counters,
        )
        report = db.get(Report, report_id)
        if report is None:
            raise ReportGenerationError("Report was deleted while generation was running.", code="report_deleted")
        report.generation_stage = "section_generation"
        db.add(report)
        _record_stage(db, task_run_id, report, "section_generation")
        db.commit()

        ordered_sections = _generation_order(sections)
        for section in ordered_sections:
            if counters.model_calls >= active.report_max_model_calls and section.section_key not in DETERMINISTIC_SECTION_KEYS:
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
            )

        report = db.get(Report, report_id)
        if report is None:
            raise ReportGenerationError("Report was deleted while generation was running.", code="report_deleted")
        _finalize_ready_report(db, report=report, counters=counters)
        _record_stage(db, task_run_id, report, "ready")
        db.commit()
        return ReportGenerationResult(
            report.id,
            report.status,
            counters.model_calls,
            counters.prompt_tokens,
            counters.completion_tokens,
            counters.total_tokens,
        )
    except (AIContextBudgetError, AIIntegrationError, ReportGenerationError) as exc:
        db.rollback()
        current = db.get(Report, report_id)
        if current is not None:
            current.status = "error"
            current.generation_stage = "failed"
            current.error_code = getattr(exc, "code", None) or (
                "context_budget" if isinstance(exc, AIContextBudgetError) else "provider_error"
            )
            current.error = str(exc)[:4000]
            current.model_calls = counters.model_calls
            current.prompt_tokens = counters.prompt_tokens or None
            current.completion_tokens = counters.completion_tokens or None
            current.total_tokens = counters.total_tokens or None
            db.add(current)
            _record_stage(db, task_run_id, current, "failed", message=str(exc))
            db.commit()
        raise


@dataclass
class _UsageCounters:
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, completion) -> None:
        self.model_calls += 1
        self.prompt_tokens += completion.prompt_tokens or completion.prompt_char_count // 3 or 0
        self.completion_tokens += completion.completion_tokens or completion.response_char_count // 3 or 0
        self.total_tokens += completion.total_tokens or (
            (completion.prompt_tokens or completion.prompt_char_count // 3 or 0)
            + (completion.completion_tokens or completion.response_char_count // 3 or 0)
        )


def _validate_reporting_available(active: ActiveAISettings) -> None:
    if not active.ai_enabled:
        raise ReportGenerationError("AI features are disabled by the server administrator.", code="ai_disabled")
    if not active.ai_configured:
        raise ReportGenerationError("AI provider settings are incomplete.", code="ai_not_configured")
    if not active.reporting_enabled:
        raise ReportGenerationError("AI reporting is disabled in AI settings.", code="reporting_disabled")


def _synthesize_evidence_batches(
    db: Session,
    *,
    active: ActiveAISettings,
    report: Report,
    sources: list[ReportSourceItem],
    budget,
    task_run_id: uuid.UUID | None,
    counters: _UsageCounters,
) -> list[dict]:
    system_prompt = (
        "You are a threat-intelligence evidence analyst. Use only the supplied source excerpts. "
        "Return JSON with a findings array. Each finding must contain text and citations, where citations is an array "
        "of supplied S-number identifiers. Preserve uncertainty and never invent attribution, exploitation, impact, or observables. "
        "Combine duplicate developments and keep each finding concise."
    )
    fixed_tokens = estimate_tokens(system_prompt) + 180
    batch_plan = plan_evidence_batches(
        [source.evidence_text for source in sources],
        budget=budget,
        fixed_prompt_tokens=fixed_tokens,
    )
    findings: list[dict] = []
    known_citations = {source.citation_key for source in sources}
    for index, batch in enumerate(batch_plan.batches, start=1):
        if counters.model_calls >= active.report_max_model_calls:
            raise ReportGenerationError("Evidence synthesis reached the configured model-call limit.", code="model_call_limit")
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "report_objective": (report.prompt_config_json or {}).get("objective"),
                        "batch": index,
                        "evidence": list(batch),
                    },
                    ensure_ascii=True,
                ),
            },
        ]
        _assert_messages_fit(messages, budget=budget)
        completion = request_ai_json_with_usage(
            db,
            active,
            feature_type=FEATURE_REPORT,
            messages=messages,
            report_id=report.id,
            task_run_id=task_run_id,
            max_completion_tokens=min(active.report_reserved_output_tokens, 800),
        )
        counters.add(completion)
        findings.extend(_normalize_findings(completion.payload.get("findings"), known_citations=known_citations))
        _record_provider_progress(db, task_run_id, report, counters, stage=f"evidence_batch_{index}")
        db.commit()
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
) -> None:
    section = db.get(ReportSection, section.id)
    if section is None:
        raise ReportGenerationError("A report section was deleted during generation.", code="section_deleted")
    if section.section_key in DETERMINISTIC_SECTION_KEYS:
        body, key_points, citations = _deterministic_section(report, section, sources)
    else:
        section.status = "running"
        db.add(section)
        db.commit()
        known_citations = {source.citation_key for source in sources}
        system_prompt = (
            "You are writing one section of a sourced threat-intelligence report. Use only the supplied deterministic metrics "
            "and evidence findings. Return JSON with body_markdown, key_points, and citations. Every material factual claim must "
            "cite one or more supplied S-number sources in square brackets. Do not invent facts, recommendations, or attribution. "
            "State uncertainty plainly and omit claims not supported by evidence."
        )
        compact_findings = _fit_findings_to_budget(
            findings,
            budget_tokens=max(256, budget.usable_input_tokens - estimate_tokens(system_prompt) - 350),
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "section": {"key": section.section_key, "title": section.title},
                        "report": {
                            "title": report.title,
                            "period_start": report.period_start.isoformat(),
                            "period_end": report.period_end.isoformat(),
                            "prompt": report.prompt_config_json,
                            "metrics": report.metrics_json,
                        },
                        "findings": compact_findings,
                    },
                    ensure_ascii=True,
                ),
            },
        ]
        _assert_messages_fit(messages, budget=budget)
        completion = request_ai_json_with_usage(
            db,
            active,
            feature_type=FEATURE_REPORT,
            messages=messages,
            report_id=report.id,
            task_run_id=task_run_id,
            max_completion_tokens=active.report_reserved_output_tokens,
        )
        counters.add(completion)
        body = str(completion.payload.get("body_markdown") or "").strip()
        if not body:
            raise ReportGenerationError(
                f"The AI provider returned an empty {section.title} section.", code="invalid_provider_output"
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
    _record_provider_progress(db, task_run_id, report, counters, stage=report.generation_stage)
    db.commit()


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
                entries.append(f"- `{ioc.get('type', 'observable')}` `{ioc.get('value', '')}` [{source.citation_key}]")
                citations.append(source.citation_key)
        body = "\n".join(entries[:500]) or "No extracted observables were present in the selected source snapshot."
        return body, [], list(dict.fromkeys(citations))
    lines = [
        f"- [{source.citation_key}] [{source.title_snapshot}]({source.url_snapshot}) - {source.feed_name_snapshot}"
        for source in included
    ]
    return "\n".join(lines), [], [source.citation_key for source in included]


def _generation_order(sections: list[ReportSection]) -> list[ReportSection]:
    return sorted(sections, key=lambda section: (section.section_key == "executive_summary", section.position))


def _normalize_findings(value: object, *, known_citations: set[str]) -> list[dict]:
    if not isinstance(value, list):
        return []
    findings: list[dict] = []
    for entry in value[:100]:
        if isinstance(entry, str):
            text = entry.strip()
            citations = _valid_citations(None, body=text, known_citations=known_citations)
        elif isinstance(entry, dict):
            text = str(entry.get("text") or entry.get("finding") or "").strip()
            citations = _valid_citations(entry.get("citations"), body=text, known_citations=known_citations)
        else:
            continue
        if text and citations:
            findings.append({"text": _remove_unknown_inline_citations(text, known_citations), "citations": citations})
    return findings


def _fit_findings_to_budget(findings: list[dict], *, budget_tokens: int) -> list[dict]:
    result: list[dict] = []
    used = 0
    for finding in findings:
        token_count = estimate_tokens(json.dumps(finding, ensure_ascii=True))
        if result and used + token_count > budget_tokens:
            break
        if token_count <= budget_tokens:
            result.append(finding)
            used += token_count
    return result


def _assert_messages_fit(messages: list[dict[str, str]], *, budget) -> None:
    token_count = sum(estimate_tokens(message.get("content")) for message in messages)
    if token_count > budget.usable_input_tokens:
        raise AIContextBudgetError(
            f"A report stage is estimated at {token_count:,} input tokens, above the usable "
            f"{budget.usable_input_tokens:,}-token budget. Reduce custom instructions or the source token cap."
        )


def _valid_citations(value: object, *, body: str, known_citations: set[str]) -> list[str]:
    explicit = _string_list(value, limit=100)
    inline = CITATION_PATTERN.findall(body)
    return list(dict.fromkeys(citation for citation in [*explicit, *inline] if citation in known_citations))


def _remove_unknown_inline_citations(body: str, known_citations: set[str]) -> str:
    return CITATION_PATTERN.sub(lambda match: match.group(0) if match.group(1) in known_citations else "", body)


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [text for entry in value[:limit] if (text := str(entry).strip())]


def _finalize_ready_report(db: Session, *, report: Report, counters: _UsageCounters) -> None:
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
