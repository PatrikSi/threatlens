import json

import pytest

from app.services.ai_context_budget import build_context_budget, estimate_tokens
from app.services.report_prompt_budget import (
    build_evidence_messages,
    build_section_message_plan,
    estimate_message_tokens,
    extend_evidence_message_batch_plan,
    fit_evidence_to_stage,
    plan_evidence_message_batches,
)


def _oversized_prompt_context():
    return (
        {
            "audience": "security_team",
            "objective": "Analyze material threats and defensive priorities. " * 100,
            "tone": "analytical",
            "detail_level": "standard",
            "use_company_context": True,
            "custom_instructions": "Prioritize defensible evidence. " * 200,
            "focus_topics": [f"focus-{index}-" + "x" * 80 for index in range(50)],
            "excluded_topics": [f"exclude-{index}-" + "y" * 80 for index in range(50)],
        },
        {
            "global_instructions": "Preserve uncertainty and citations. " * 200,
            "company_context": {
                "company_name": "Example Company",
                "industry": "Critical infrastructure",
                "regions": ["Europe", "North America"],
                "technology_stack": ["identity", "cloud", "edge"] * 20,
                "priority_topics": ["ransomware", "espionage"] * 20,
                "keywords": ["example"] * 20,
                "exclusions": ["consumer fraud"] * 20,
                "profile_text": "Company profile context. " * 300,
            },
        },
    )


def test_exact_evidence_batching_accounts_for_json_serialization():
    budget = build_context_budget(
        context_window_tokens=8192,
        reserved_output_tokens=1200,
        safety_percent=15,
    )
    prompt, context = _oversized_prompt_context()
    evidence = [
        f'[S{index}] Evidence with "quoted content"\n' + ("line\n" * 500)
        for index in range(1, 21)
    ]
    fitted = [
        fit_evidence_to_stage(
            entry,
            source_token_cap=700,
            prompt=prompt,
            generation_context=context,
            budget=budget,
        )[0]
        for entry in evidence
    ]

    plan = plan_evidence_message_batches(
        fitted,
        prompt=prompt,
        generation_context=context,
        budget=budget,
    )

    assert plan.batch_count > 1
    assert plan.context_compacted is True
    assert plan.largest_batch_input_tokens <= budget.usable_input_tokens
    for batch in plan.batches:
        messages, _ = build_evidence_messages(
            prompt=prompt,
            generation_context=context,
            evidence=batch,
            budget=budget,
        )
        assert estimate_message_tokens(messages) <= budget.usable_input_tokens


def test_two_thousand_token_model_retains_room_for_adaptive_evidence():
    budget = build_context_budget(
        context_window_tokens=2048,
        reserved_output_tokens=256,
        safety_percent=5,
    )
    prompt, context = _oversized_prompt_context()

    evidence, truncated = fit_evidence_to_stage(
        "[S1] Local-model evidence\n" + ("technical detail " * 1000),
        source_token_cap=700,
        prompt=prompt,
        generation_context=context,
        budget=budget,
    )
    plan = plan_evidence_message_batches(
        [evidence],
        prompt=prompt,
        generation_context=context,
        budget=budget,
    )

    assert truncated is True
    assert estimate_tokens(evidence) <= 700
    assert plan.batch_count == 1
    assert plan.largest_batch_input_tokens <= budget.usable_input_tokens


def test_incremental_evidence_plan_matches_full_replanning_at_every_step():
    budget = build_context_budget(
        context_window_tokens=4096,
        reserved_output_tokens=700,
        safety_percent=10,
    )
    prompt, context = _oversized_prompt_context()
    evidence = [
        fit_evidence_to_stage(
            f"[S{index}] Evidence\n" + ("technical detail " * (100 + index * 17)),
            source_token_cap=500,
            prompt=prompt,
            generation_context=context,
            budget=budget,
        )[0]
        for index in range(1, 13)
    ]
    incremental = plan_evidence_message_batches(
        [],
        prompt=prompt,
        generation_context=context,
        budget=budget,
    )

    for index, entry in enumerate(evidence, start=1):
        incremental = extend_evidence_message_batch_plan(
            incremental,
            entry,
            prompt=prompt,
            generation_context=context,
            budget=budget,
        )
        full = plan_evidence_message_batches(
            evidence[:index],
            prompt=prompt,
            generation_context=context,
            budget=budget,
        )
        assert incremental == full


def test_section_plan_bounds_context_instructions_metrics_and_findings():
    budget = build_context_budget(
        context_window_tokens=2048,
        reserved_output_tokens=256,
        safety_percent=5,
    )
    prompt, context = _oversized_prompt_context()
    findings = [
        {
            "text": f"Finding {index} " + ("detailed evidence " * 100),
            "citations": [f"S{index}"],
        }
        for index in range(1, 31)
    ]

    plan = build_section_message_plan(
        section={
            "key": "key_developments",
            "title": "Key Developments",
            "instructions": "Emphasize material change. " * 200,
        },
        report={
            "title": "Small local model report",
            "period_start": "2026-08-01T00:00:00+00:00",
            "period_end": "2026-08-08T00:00:00+00:00",
            "prompt": prompt,
            "generation_context": context,
            "metrics": {
                "article_count": 100,
                "feeds": {f"feed-{index}": index for index in range(100)},
                "top_tags": {f"tag-{index}": index for index in range(100)},
            },
        },
        findings=findings,
        budget=budget,
    )

    assert plan.context_compacted is True
    assert plan.included_findings > 0
    assert plan.omitted_findings > 0
    assert estimate_message_tokens(plan.messages) <= budget.usable_input_tokens
    serialized = plan.messages[1]["content"]
    assert "S1" in serialized
    assert "S30" in serialized


@pytest.mark.parametrize("extra_instructions", [0, 6])
def test_section_planner_compacts_optional_metrics_before_losing_evidence_diversity(monkeypatch, extra_instructions):
    from app.services import report_prompt_budget

    monkeypatch.setattr(report_prompt_budget, "SECTION_SYSTEM_PROMPT",
        report_prompt_budget.SECTION_SYSTEM_PROMPT + " Preserve source uncertainty." * extra_instructions)
    budget = build_context_budget(context_window_tokens=2048, reserved_output_tokens=256, safety_percent=5)
    report = {"title": "Source diversity", "metrics": {
        "article_count": 30,
        "feeds": {f"feed-{index}-" + "x" * 50: index for index in range(10)},
        "top_tags": {f"tag-{index}-" + "x" * 50: index for index in range(10)},
    }}
    section = {"key": "analysis", "title": "Analysis"}
    findings = [{
        "text": f"Finding {index} " + "Observed authentication failures. " * 20,
        "citations": [f"S{index}"],
        "evidence_quotes": [{"citation": f"S{index}", "quote": "Observed authentication failures."}],
    } for index in range(1, 31)]
    plan = build_section_message_plan(section=section, report=report, findings=findings, budget=budget)
    payload = json.loads(plan.messages[-1]["content"])
    assert payload["report"]["metrics"] == {"article_count": 30}
    assert plan.context_compacted  # Metric reductions need the same disclosure as textual context.
    assert plan.included_findings >= 2 and plan.omitted_findings > 0
    assert payload["findings"][0]["citations"] == ["S1"]
    assert payload["findings"][1]["citations"] == ["S30"]
    by_citation = {finding["citations"][0]: finding for finding in findings}
    for finding in payload["findings"]:
        assert finding["evidence_quotes"] == by_citation[finding["citations"][0]]["evidence_quotes"]
    assert estimate_message_tokens(plan.messages) <= budget.usable_input_tokens
    # With no findings to fit, retain the optional metrics whenever they fit.
    empty = build_section_message_plan(section=section, report=report, findings=[], budget=budget)
    assert json.loads(empty.messages[-1]["content"])["report"]["metrics"] == report["metrics"]
    assert not empty.context_compacted
