from __future__ import annotations

import math
from dataclasses import dataclass


PROTOCOL_OVERHEAD_TOKENS = 384
MIN_USABLE_INPUT_TOKENS = 512


class AIContextBudgetError(ValueError):
    pass


@dataclass(frozen=True)
class AIContextBudget:
    context_window_tokens: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    protocol_overhead_tokens: int
    usable_input_tokens: int


@dataclass(frozen=True)
class EvidenceBatchPlan:
    batches: tuple[tuple[str, ...], ...]
    estimated_input_tokens: int
    largest_evidence_tokens: int

    @property
    def batch_count(self) -> int:
        return len(self.batches)


def estimate_tokens(text: str | None) -> int:
    """Conservative dependency-free estimate for unknown OpenAI-compatible tokenizers."""
    if not text:
        return 0
    value = str(text)
    character_estimate = math.ceil(len(value) / 3.2)
    word_estimate = math.ceil(len(value.split()) * 1.45)
    return max(1, character_estimate, word_estimate)


def build_context_budget(
    *,
    context_window_tokens: int,
    reserved_output_tokens: int,
    safety_percent: int,
    protocol_overhead_tokens: int = PROTOCOL_OVERHEAD_TOKENS,
) -> AIContextBudget:
    if context_window_tokens < 2048:
        raise AIContextBudgetError("The configured report context window must be at least 2,048 tokens.")
    if not 5 <= safety_percent <= 40:
        raise AIContextBudgetError("The report context safety margin must be between 5% and 40%.")
    if reserved_output_tokens < 256:
        raise AIContextBudgetError("The report output reserve must be at least 256 tokens.")
    safety_tokens = math.ceil(context_window_tokens * safety_percent / 100)
    usable = context_window_tokens - reserved_output_tokens - safety_tokens - protocol_overhead_tokens
    if usable < MIN_USABLE_INPUT_TOKENS:
        raise AIContextBudgetError(
            "The configured context window leaves fewer than 512 usable input tokens. "
            "Increase the context window or reduce the output reserve."
        )
    return AIContextBudget(
        context_window_tokens=context_window_tokens,
        reserved_output_tokens=reserved_output_tokens,
        safety_margin_tokens=safety_tokens,
        protocol_overhead_tokens=protocol_overhead_tokens,
        usable_input_tokens=usable,
    )


def truncate_to_token_estimate(text: str | None, *, max_tokens: int) -> tuple[str, bool]:
    value = str(text or "").strip()
    if not value or estimate_tokens(value) <= max_tokens:
        return value, False
    if max_tokens <= 0:
        return "", True
    low, high = 0, len(value)
    while low < high:
        midpoint = (low + high + 1) // 2
        if estimate_tokens(value[:midpoint]) <= max_tokens:
            low = midpoint
        else:
            high = midpoint - 1
    clipped = value[:low].rsplit(" ", 1)[0].strip() or value[:low].strip()
    return f"{clipped}\n[Source excerpt truncated by context guardrail]", True


def plan_evidence_batches(
    evidence: list[str],
    *,
    budget: AIContextBudget,
    fixed_prompt_tokens: int,
) -> EvidenceBatchPlan:
    batch_capacity = budget.usable_input_tokens - max(0, fixed_prompt_tokens)
    if batch_capacity < 256:
        raise AIContextBudgetError(
            "The report instructions consume the usable context budget. Shorten the instructions or increase the context window."
        )

    batches: list[tuple[str, ...]] = []
    current: list[str] = []
    current_tokens = 0
    total_tokens = 0
    largest = 0
    for entry in evidence:
        token_count = estimate_tokens(entry)
        if token_count > batch_capacity:
            raise AIContextBudgetError(
                "A report evidence unit exceeds the usable context budget after source truncation. "
                "Reduce the per-source token cap or increase the context window."
            )
        if current and current_tokens + token_count > batch_capacity:
            batches.append(tuple(current))
            current = []
            current_tokens = 0
        current.append(entry)
        current_tokens += token_count
        total_tokens += token_count
        largest = max(largest, token_count)
    if current:
        batches.append(tuple(current))
    return EvidenceBatchPlan(tuple(batches), total_tokens, largest)
