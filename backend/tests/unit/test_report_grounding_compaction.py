"""Small valid context windows must not trigger section calls without evidence."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.report import Report
from app.models.report_section import ReportSection
from app.services import report_generation
from app.services.ai_context_budget import build_context_budget, estimate_tokens
from app.services.report_grounding import validate_findings
from app.services.report_prompt_budget import (
    build_evidence_messages, build_section_message_plan, estimate_message_tokens, fit_evidence_to_stage,
)


def _fitted_findings():
    budget = build_context_budget(context_window_tokens=2048, reserved_output_tokens=800, safety_percent=5)
    source, _ = fit_evidence_to_stage(
        "[S1] Synthetic source\n" + "Observed service authentication failures. " * 40,
        source_token_cap=700, prompt={}, generation_context={}, budget=budget,
    )
    evidence_messages, _ = build_evidence_messages(
        prompt={}, generation_context={}, evidence=[source], budget=budget,
    )
    # Quote the source exactly, excluding the planner's truncation marker.
    quote = source.split("\n")[1]
    findings = validate_findings([{
        "text": "Observed service authentication failures.", "citations": ["S1"],
        "evidence_quotes": [{"citation": "S1", "quote": quote}],
    }], sources={"S1": source})
    assert len(quote) > 1000
    assert estimate_message_tokens(evidence_messages) <= budget.usable_input_tokens
    assert estimate_tokens(json.dumps({"findings": findings})) <= budget.reserved_output_tokens
    return budget, findings


def test_valid_evidence_response_can_leave_no_complete_finding_in_section_context():
    budget, findings = _fitted_findings()
    plan = build_section_message_plan(
        section={"key": "assessment", "title": "Assessment"},
        report={"title": "Synthetic"}, findings=findings, budget=budget,
    )
    assert plan.included_findings == 0 and plan.omitted_findings == 1
    assert json.loads(plan.messages[1]["content"])["findings"] == []
    assert estimate_message_tokens(plan.messages) <= budget.usable_input_tokens

    larger_budget = build_context_budget(context_window_tokens=4096, reserved_output_tokens=800, safety_percent=5)
    larger_plan = build_section_message_plan(
        section={"key": "assessment", "title": "Assessment"},
        report={"title": "Synthetic"}, findings=findings, budget=larger_budget,
    )
    included = json.loads(larger_plan.messages[1]["content"])["findings"]
    assert larger_plan.included_findings == 1 and larger_plan.omitted_findings == 0
    assert included[0]["evidence_quotes"] == findings[0]["evidence_quotes"]


def test_empty_compacted_section_discloses_limitation_without_a_paid_call(db_session, monkeypatch):
    budget, findings = _fitted_findings()
    now = datetime.now(timezone.utc)
    report = Report(
        title="Synthetic", report_type="custom", status="running", trigger_source="manual",
        generation_stage="section_generation", period_start=now - timedelta(days=1), period_end=now,
        prompt_config_json={}, generation_context_json={}, sections_config_json=[], metrics_json={},
        coverage_json={"grounding": {
            "version": 1, "status": "checked", "validated_findings": 1, "cited_claim_blocks": 0,
            "empty_batches": [], "semantic_verification": False,
        }},
    )
    db_session.add(report)
    db_session.flush()
    section = ReportSection(
        report_id=report.id, section_key="assessment", title="Assessment", position=0, status="pending",
    )
    db_session.add(section)
    db_session.commit()
    monkeypatch.setattr(report_generation, "_request_report_completion", lambda *_args, **_kwargs: pytest.fail(
        "A section request with no supporting findings cannot produce a grounded response."
    ))
    counters = report_generation._UsageCounters(model_calls=1, prompt_tokens=759, completion_tokens=465, total_tokens=1224)
    report_generation._generate_section(
        db_session, active=SimpleNamespace(report_max_model_calls=20), report=report, section=section,
        sources=[], findings=findings, budget=budget, task_run_id=None, counters=counters,
        execution_checkpoint=None, execution_commit=None,
    )
    db_session.expire_all()
    assert section.status == "ready" and section.citations_json == []
    assert "context" in section.body_markdown.lower()
    assert report.model_calls == 1 and report.total_tokens == 1224
    grounding = report.coverage_json["grounding"]
    assert grounding["validated_findings"] == 1 and grounding["cited_claim_blocks"] == 0
    assert grounding["status"] == "degraded" and grounding["degraded_sections"] == ["assessment"]
    assert any("Assessment" in warning and "context" in warning.lower() for warning in report.coverage_json["warnings"])
