from __future__ import annotations

import json
from dataclasses import dataclass

from app.services.ai_context_budget import (
    AIContextBudget,
    AIContextBudgetError,
    estimate_tokens,
    truncate_to_token_estimate,
)


EVIDENCE_SYSTEM_PROMPT = (
    "You are a threat-intelligence evidence analyst. Use only the supplied source excerpts. "
    "Return JSON with a findings array. Each finding must contain text and citations, where citations is an array "
    "of supplied S-number identifiers. Preserve uncertainty and never invent attribution, exploitation, impact, or observables. "
    "Combine duplicate developments and keep each finding concise."
)
SECTION_SYSTEM_PROMPT = (
    "You are writing one section of a sourced threat-intelligence report. Use only the supplied deterministic metrics "
    "and evidence findings. Return JSON with body_markdown, key_points, and citations. Every material factual claim must "
    "cite one or more supplied S-number sources in square brackets. Do not invent facts, recommendations, or attribution. "
    "State uncertainty plainly and omit claims not supported by evidence."
)
CONTEXT_COMPACTION_WARNING = (
    "Optional report context was compacted to fit the configured model context window; "
    "the report objective and global instructions were prioritized."
)
FINDINGS_COMPACTION_WARNING = (
    "Section prompts used a context-bounded representative set of synthesized findings."
)


@dataclass(frozen=True)
class BoundedReportContext:
    prompt: dict
    generation_context: dict
    compacted: bool


@dataclass(frozen=True)
class ReportMessageBatchPlan:
    batches: tuple[tuple[str, ...], ...]
    fixed_prompt_tokens: int
    estimated_input_tokens: int
    largest_evidence_tokens: int
    largest_batch_input_tokens: int
    context_compacted: bool

    @property
    def batch_count(self) -> int:
        return len(self.batches)


@dataclass(frozen=True)
class SectionMessagePlan:
    messages: list[dict[str, str]]
    included_findings: int
    omitted_findings: int
    context_compacted: bool


def build_evidence_messages(
    *,
    prompt: dict,
    generation_context: dict,
    evidence: list[str] | tuple[str, ...],
    budget: AIContextBudget,
) -> tuple[list[dict[str, str]], BoundedReportContext]:
    bounded = compact_report_context(
        prompt,
        generation_context,
        max_tokens=_report_context_target(budget),
    )
    messages = [
        {"role": "system", "content": EVIDENCE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _json(
                {
                    "report_prompt": bounded.prompt,
                    "generation_context": bounded.generation_context,
                    "evidence": list(evidence),
                }
            ),
        },
    ]
    return messages, bounded


def plan_evidence_message_batches(
    evidence: list[str],
    *,
    prompt: dict,
    generation_context: dict,
    budget: AIContextBudget,
) -> ReportMessageBatchPlan:
    empty_messages, bounded = build_evidence_messages(
        prompt=prompt,
        generation_context=generation_context,
        evidence=[],
        budget=budget,
    )
    fixed_tokens = estimate_message_tokens(empty_messages)
    if budget.usable_input_tokens - fixed_tokens < 128:
        raise AIContextBudgetError(
            "The required report objective and provider protocol leave too little room for evidence. "
            "Reduce the output reserve or increase the model context window."
        )

    batches: list[tuple[str, ...]] = []
    current: list[str] = []
    total_tokens = 0
    largest = 0
    for entry in evidence:
        candidate = [*current, entry]
        messages, _ = build_evidence_messages(
            prompt=prompt,
            generation_context=generation_context,
            evidence=candidate,
            budget=budget,
        )
        if estimate_message_tokens(messages) <= budget.usable_input_tokens:
            current = candidate
        elif current:
            batches.append(tuple(current))
            current = [entry]
            singleton, _ = build_evidence_messages(
                prompt=prompt,
                generation_context=generation_context,
                evidence=current,
                budget=budget,
            )
            if estimate_message_tokens(singleton) > budget.usable_input_tokens:
                raise _oversized_evidence_error()
        else:
            raise _oversized_evidence_error()
        entry_tokens = estimate_tokens(entry)
        total_tokens += entry_tokens
        largest = max(largest, entry_tokens)
    if current:
        batches.append(tuple(current))
    return ReportMessageBatchPlan(
        batches=tuple(batches),
        fixed_prompt_tokens=fixed_tokens,
        estimated_input_tokens=total_tokens,
        largest_evidence_tokens=largest,
        largest_batch_input_tokens=max(
            (
                estimate_message_tokens(
                    build_evidence_messages(
                        prompt=prompt,
                        generation_context=generation_context,
                        evidence=batch,
                        budget=budget,
                    )[0]
                )
                for batch in batches
            ),
            default=fixed_tokens,
        ),
        context_compacted=bounded.compacted,
    )


def fit_evidence_to_stage(
    evidence: str,
    *,
    source_token_cap: int,
    prompt: dict,
    generation_context: dict,
    budget: AIContextBudget,
) -> tuple[str, bool]:
    clipped, truncated = truncate_to_token_estimate(
        evidence, max_tokens=source_token_cap
    )
    if _single_evidence_fits(
        clipped,
        prompt=prompt,
        generation_context=generation_context,
        budget=budget,
    ):
        return clipped, truncated

    low, high = 1, min(source_token_cap, estimate_tokens(clipped))
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        candidate, _ = truncate_to_token_estimate(evidence, max_tokens=midpoint)
        if candidate and _single_evidence_fits(
            candidate,
            prompt=prompt,
            generation_context=generation_context,
            budget=budget,
        ):
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    if not best:
        raise _oversized_evidence_error()
    return best, True


def build_section_message_plan(
    *,
    section: dict,
    report: dict,
    findings: list[dict],
    budget: AIContextBudget,
) -> SectionMessagePlan:
    bounded = compact_report_context(
        dict(report.get("prompt") or {}),
        dict(report.get("generation_context") or {}),
        max_tokens=_report_context_target(budget),
    )
    bounded_section = _compact_section(section, budget=budget)
    compact_report = {
        "title": report.get("title"),
        "period_start": report.get("period_start"),
        "period_end": report.get("period_end"),
        "prompt": bounded.prompt,
        "generation_context": bounded.generation_context,
        "metrics": _compact_metrics(dict(report.get("metrics") or {})),
    }

    def messages_for(candidate_findings: list[dict]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SECTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _json(
                    {
                        "section": bounded_section,
                        "report": compact_report,
                        "findings": candidate_findings,
                    }
                ),
            },
        ]

    empty_messages = messages_for([])
    if estimate_message_tokens(empty_messages) > budget.usable_input_tokens:
        compact_report["metrics"] = _scalar_metrics(compact_report["metrics"])
        empty_messages = messages_for([])
    if estimate_message_tokens(empty_messages) > budget.usable_input_tokens:
        compact_report["metrics"] = {}
        empty_messages = messages_for([])
    if estimate_message_tokens(empty_messages) > budget.usable_input_tokens:
        bounded = compact_report_context(
            dict(report.get("prompt") or {}),
            dict(report.get("generation_context") or {}),
            max_tokens=max(96, _report_context_target(budget) // 2),
        )
        compact_report["prompt"] = bounded.prompt
        compact_report["generation_context"] = bounded.generation_context
        empty_messages = messages_for([])
    if estimate_message_tokens(empty_messages) > budget.usable_input_tokens:
        bounded_section.pop("instructions", None)
        empty_messages = messages_for([])
    if estimate_message_tokens(empty_messages) > budget.usable_input_tokens:
        raise AIContextBudgetError(
            "The required section instructions do not fit the model context window after compaction. "
            "Reduce the output reserve or increase the model context window."
        )
    selected = _fit_representative_findings(
        findings,
        messages_for=messages_for,
        max_tokens=budget.usable_input_tokens,
    )
    messages = messages_for(selected)
    return SectionMessagePlan(
        messages=messages,
        included_findings=len(selected),
        omitted_findings=max(0, len(findings) - len(selected)),
        context_compacted=(
            bounded.compacted or bounded_section != _clean(dict(section or {}))
        ),
    )


def compact_report_context(
    prompt: dict,
    generation_context: dict,
    *,
    max_tokens: int,
) -> BoundedReportContext:
    original_prompt = _clean(dict(prompt or {}))
    original_context = _clean(dict(generation_context or {}))
    bounded_prompt: dict[str, object] = {}
    bounded_context: dict[str, object] = {}

    _add_text_field(
        bounded_prompt,
        "objective",
        original_prompt.get("objective"),
        field_token_cap=max(48, min(256, max_tokens // 4)),
        prompt=bounded_prompt,
        context=bounded_context,
        max_tokens=max_tokens,
    )
    for key in ("audience", "tone", "detail_level"):
        _add_text_field(
            bounded_prompt,
            key,
            original_prompt.get(key),
            field_token_cap=32,
            prompt=bounded_prompt,
            context=bounded_context,
            max_tokens=max_tokens,
        )
    if "use_company_context" in original_prompt:
        bounded_prompt["use_company_context"] = bool(
            original_prompt["use_company_context"]
        )
    _add_text_field(
        bounded_context,
        "global_instructions",
        original_context.get("global_instructions"),
        field_token_cap=max(32, min(256, max_tokens // 5)),
        prompt=bounded_prompt,
        context=bounded_context,
        max_tokens=max_tokens,
    )
    _add_text_field(
        bounded_prompt,
        "custom_instructions",
        original_prompt.get("custom_instructions"),
        field_token_cap=max(32, min(256, max_tokens // 5)),
        prompt=bounded_prompt,
        context=bounded_context,
        max_tokens=max_tokens,
    )
    for key in ("focus_topics", "excluded_topics"):
        _add_list_field(
            bounded_prompt,
            key,
            original_prompt.get(key),
            prompt=bounded_prompt,
            context=bounded_context,
            max_tokens=max_tokens,
        )

    company = original_context.get("company_context")
    bounded_company: dict[str, object] = {}
    if isinstance(company, dict):
        bounded_context["company_context"] = bounded_company
        for key in ("company_name", "industry"):
            _add_text_field(
                bounded_company,
                key,
                company.get(key),
                field_token_cap=48,
                prompt=bounded_prompt,
                context=bounded_context,
                max_tokens=max_tokens,
            )
        for key in (
            "regions",
            "priority_topics",
            "technology_stack",
            "keywords",
            "exclusions",
        ):
            _add_list_field(
                bounded_company,
                key,
                company.get(key),
                prompt=bounded_prompt,
                context=bounded_context,
                max_tokens=max_tokens,
            )
        _add_text_field(
            bounded_company,
            "profile_text",
            company.get("profile_text"),
            field_token_cap=max(32, min(192, max_tokens // 6)),
            prompt=bounded_prompt,
            context=bounded_context,
            max_tokens=max_tokens,
        )
        if not bounded_company:
            bounded_context.pop("company_context", None)

    compacted = _json(
        {"prompt": bounded_prompt, "generation_context": bounded_context}
    ) != _json(
        {"prompt": original_prompt, "generation_context": original_context}
    )
    return BoundedReportContext(
        prompt=bounded_prompt,
        generation_context=bounded_context,
        compacted=compacted,
    )


def estimate_message_tokens(messages: list[dict[str, str]]) -> int:
    return sum(estimate_tokens(message.get("content")) for message in messages)


def _fit_representative_findings(
    findings: list[dict],
    *,
    messages_for,
    max_tokens: int,
) -> list[dict]:
    if not findings:
        return []
    empty_tokens = estimate_message_tokens(messages_for([]))
    target_count = min(12, len(findings))
    per_finding_cap = max(
        24,
        min(256, (max_tokens - empty_tokens) // target_count - 16),
    )
    selected: list[dict] = []
    for finding in _representative_order(findings):
        normalized = _bounded_finding(finding, max_tokens=per_finding_cap)
        if not normalized:
            continue
        candidate = [*selected, normalized]
        if estimate_message_tokens(messages_for(candidate)) <= max_tokens:
            selected = candidate
            continue
        if selected:
            continue
        normalized = _fit_single_finding(
            finding,
            messages_for=messages_for,
            max_tokens=max_tokens,
        )
        if normalized:
            selected.append(normalized)
    return selected


def _fit_single_finding(finding: dict, *, messages_for, max_tokens: int) -> dict | None:
    low, high = 1, min(256, estimate_tokens(str(finding.get("text") or "")))
    best: dict | None = None
    while low <= high:
        midpoint = (low + high) // 2
        candidate = _bounded_finding(finding, max_tokens=midpoint)
        if candidate and estimate_message_tokens(messages_for([candidate])) <= max_tokens:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _bounded_finding(finding: dict, *, max_tokens: int) -> dict | None:
    text = str(finding.get("text") or "").strip()
    if not text:
        return None
    clipped, _ = truncate_to_token_estimate(text, max_tokens=max_tokens)
    if not clipped:
        return None
    return {
        "text": clipped,
        "citations": [
            str(value) for value in list(finding.get("citations") or [])[:20]
        ],
    }


def _representative_order(findings: list[dict]) -> list[dict]:
    if len(findings) <= 2:
        return list(findings)
    left, right = 0, len(findings) - 1
    result: list[dict] = []
    while left <= right:
        result.append(findings[left])
        if right != left:
            result.append(findings[right])
        left += 1
        right -= 1
    return result


def _compact_metrics(metrics: dict) -> dict:
    result: dict[str, object] = {}
    for key in ("article_count", "articles_with_extracted_text", "articles_with_iocs", "ioc_count"):
        if key in metrics:
            result[key] = metrics[key]
    for key in ("feeds", "classifications", "relevance_labels", "top_tags", "ioc_types"):
        values = metrics.get(key)
        if not isinstance(values, dict):
            continue
        entries = list(values.items())
        result[key] = dict(entries[:10])
        if len(entries) > 10:
            result[f"{key}_other_count"] = sum(
                value for _, value in entries[10:] if isinstance(value, (int, float))
            )
    return result


def _scalar_metrics(metrics: dict) -> dict:
    return {
        key: value
        for key, value in metrics.items()
        if key
        in {
            "article_count",
            "articles_with_extracted_text",
            "articles_with_iocs",
            "ioc_count",
        }
    }


def _compact_section(section: dict, *, budget: AIContextBudget) -> dict:
    result = {
        "key": str(section.get("key") or "")[:64],
        "title": str(section.get("title") or "")[:255],
    }
    instructions, _ = truncate_to_token_estimate(
        str(section.get("instructions") or ""),
        max_tokens=max(32, min(192, budget.usable_input_tokens // 8)),
    )
    if instructions:
        result["instructions"] = instructions
    return result


def _add_text_field(
    target: dict,
    key: str,
    value: object,
    *,
    field_token_cap: int,
    prompt: dict,
    context: dict,
    max_tokens: int,
) -> None:
    text = str(value or "").strip()
    if not text:
        return
    clipped, _ = truncate_to_token_estimate(text, max_tokens=field_token_cap)
    if not clipped:
        return
    target[key] = clipped
    if _context_tokens(prompt, context) <= max_tokens:
        return
    target.pop(key, None)


def _add_list_field(
    target: dict,
    key: str,
    value: object,
    *,
    prompt: dict,
    context: dict,
    max_tokens: int,
) -> None:
    if not isinstance(value, list):
        return
    selected: list[str] = []
    for entry in value[:20]:
        clipped, _ = truncate_to_token_estimate(str(entry), max_tokens=32)
        if not clipped:
            continue
        selected.append(clipped)
        target[key] = list(selected)
        if _context_tokens(prompt, context) > max_tokens:
            selected.pop()
            break
    if selected:
        target[key] = selected
    else:
        target.pop(key, None)


def _clean(value: dict) -> dict:
    result: dict[str, object] = {}
    for key, entry in value.items():
        if isinstance(entry, dict):
            entry = _clean(entry)
        if entry not in (None, "", [], {}):
            result[key] = entry
    return result


def _single_evidence_fits(
    evidence: str,
    *,
    prompt: dict,
    generation_context: dict,
    budget: AIContextBudget,
) -> bool:
    messages, _ = build_evidence_messages(
        prompt=prompt,
        generation_context=generation_context,
        evidence=[evidence],
        budget=budget,
    )
    return estimate_message_tokens(messages) <= budget.usable_input_tokens


def _context_tokens(prompt: dict, context: dict) -> int:
    return estimate_tokens(_json({"prompt": prompt, "generation_context": context}))


def _report_context_target(budget: AIContextBudget) -> int:
    return min(1600, max(192, budget.usable_input_tokens // 3))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _oversized_evidence_error() -> AIContextBudgetError:
    return AIContextBudgetError(
        "A report evidence unit does not fit the model context window after adaptive truncation. "
        "Reduce the output reserve or increase the context window."
    )
