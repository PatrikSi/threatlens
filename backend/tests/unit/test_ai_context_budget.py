import pytest

from app.services.ai_context_budget import (
    AIContextBudgetError,
    build_context_budget,
    estimate_tokens,
    plan_evidence_batches,
    truncate_to_token_estimate,
)


def test_estimator_uses_conservative_character_and_word_bounds():
    assert estimate_tokens("a" * 320) == 100
    assert estimate_tokens("word " * 100) >= 145
    assert estimate_tokens("") == 0


def test_context_budget_reserves_output_safety_and_protocol_overhead():
    budget = build_context_budget(
        context_window_tokens=8192,
        reserved_output_tokens=1200,
        safety_percent=15,
    )

    assert budget.safety_margin_tokens == 1229
    assert budget.usable_input_tokens == 5379


def test_context_budget_rejects_configuration_that_cannot_fit_a_prompt():
    with pytest.raises(AIContextBudgetError, match="fewer than 512"):
        build_context_budget(
            context_window_tokens=2048,
            reserved_output_tokens=1200,
            safety_percent=15,
        )


def test_truncation_stays_under_estimated_token_cap_and_marks_excerpt():
    clipped, truncated = truncate_to_token_estimate("word " * 1000, max_tokens=100)

    assert truncated is True
    assert "truncated by context guardrail" in clipped
    assert estimate_tokens(clipped) <= 100


def test_evidence_planner_preserves_order_and_splits_at_capacity():
    budget = build_context_budget(
        context_window_tokens=4096,
        reserved_output_tokens=512,
        safety_percent=10,
    )
    evidence = ["a" * 3200, "b" * 3200, "c" * 3200]

    plan = plan_evidence_batches(evidence, budget=budget, fixed_prompt_tokens=300)

    assert plan.batch_count == 2
    assert [entry[0] for batch in plan.batches for entry in batch] == ["a", "b", "c"]


def test_evidence_planner_rejects_oversized_unit():
    budget = build_context_budget(
        context_window_tokens=2048,
        reserved_output_tokens=512,
        safety_percent=10,
    )

    with pytest.raises(AIContextBudgetError, match="evidence unit"):
        plan_evidence_batches(["x" * 20_000], budget=budget, fixed_prompt_tokens=256)
