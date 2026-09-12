"""Exercise generation resumption through real receipts, usage and stage artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_usage_event import AIUsageEvent
from app.models.ai_workflow import AIReportStageArtifact
from app.services.report_evidence_contract import REPORT_EVIDENCE_CONTRACT_VERSION
from app.models.report import Report
from app.models.report_generation_lease import ReportGenerationLease
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.services import ai_request_runtime, report_generation
from app.services.ai_persistence import record_usage_event
from app.services.ai_provider_client import AICompletionResult
from app.services.ai_ops import start_ai_task_run
from app.services.ai_report_workflow import defer_report_workflow
from app.services.ai_workflow_dispatch import AIWorkflowDeferred
from app.services.report_execution import claim_report_generation
from tests.unit.test_ai_request_runtime import _active, _run_request, _task_run


@pytest.mark.parametrize("cancel_before_resume", [False, True])
def test_resume_replays_committed_stages_without_duplicate_calls_usage_or_grounding(
    db_session, monkeypatch, cancel_before_resume,
):
    run = _task_run(db_session)
    report = db_session.get(Report, run.report_id)
    report.status = "queued"
    report.coverage_json = {"evidence_contract_version": REPORT_EVIDENCE_CONTRACT_VERSION}
    report.source_count = report.included_source_count = 1
    now = datetime.now(timezone.utc)
    db_session.add(ReportSourceItem(
        report_id=report.id, citation_key="S1", included=True, rank=1,
        title_snapshot="Synthetic evidence", feed_name_snapshot="Fixture",
        url_snapshot="https://example.test/source", first_seen_at_snapshot=now,
        evidence_text="[S1] Analysts observed suspicious authentication activity.", estimated_tokens=20,
    ))
    sections = [ReportSection(
        report_id=report.id, section_key=key, title=title, position=position, status="pending",
    ) for position, (key, title) in enumerate([
        ("assessment", "Assessment"), ("executive_summary", "Executive Summary"),
    ])]
    db_session.add_all(sections)
    db_session.commit()
    claim = claim_report_generation(
        db_session, report_id=report.id, lease_token="initial-owner", lease_seconds=120,
    )
    assert claim.status == "claimed"
    db_session.commit()
    active = _active()
    for name, value in {
        "ai_enabled": True, "ai_configured": True, "reporting_enabled": True,
        "report_context_window_tokens": 8192, "report_reserved_output_tokens": 512,
        "report_context_safety_percent": 15, "report_source_token_cap": 700, "report_max_model_calls": 20,
    }.items():
        setattr(active, name, value)
    monkeypatch.setattr(report_generation, "load_active_ai_settings", lambda *_args, **_kwargs: active)
    paid_stages = []
    capacity_blocked = True

    def provider(_active, *, messages, **_kwargs):
        stage = json.loads(messages[-1]["content"])
        key = "evidence" if "evidence" in stage else stage["section"]["key"]
        paid_stages.append(key)
        payload = {"findings": [{
            "text": "Suspicious authentication activity.", "citations": ["S1"],
            "evidence_quotes": [{"citation": "S1", "quote": "Analysts observed suspicious authentication activity."}],
        }]} if key == "evidence" else {
            "body_markdown": "Suspicious authentication activity was observed. [S1]", "citations": ["S1"],
        }
        return AICompletionResult(payload=payload, provider=active.provider_type, model=active.model,
            latency_ms=1, prompt_tokens=100, completion_tokens=50, total_tokens=150)

    def admission(_db, selected, *, call, messages, call_kwargs, **_kwargs):
        stage = json.loads(messages[-1]["content"])
        if capacity_blocked and stage.get("section", {}).get("key") == "executive_summary":
            raise AIWorkflowDeferred("provider_concurrency_budget", 30)
        return call(selected, **call_kwargs)

    def request(db, selected, **kwargs):
        return _run_request(
            db, active=selected, task_run_id=run.id, report_id=report.id, messages=kwargs["messages"],
            operation_scope=kwargs["provider_operation_scope"], max_provider_attempts=kwargs["max_provider_attempts"],
            call_provider=provider, record_usage=record_usage_event,
        )

    monkeypatch.setattr(ai_request_runtime, "call_with_provider_budget", admission)
    monkeypatch.setattr(report_generation, "request_ai_json_with_usage", request)
    with pytest.raises(AIWorkflowDeferred) as deferred:
        report_generation.generate_report(db_session, report_id=report.id, task_run_id=run.id)
    # The generator leaves this transition pending so the worker can commit the
    # report, logical task and lease release atomically.
    assert defer_report_workflow(
        db_session, report_id=report.id, run_id=run.id,
        lease_token="initial-owner", generation_fence=claim.generation_fence,
        reason=deferred.value.reason, retry_after_seconds=deferred.value.retry_after_seconds,
    ) == {"status": "queued", "reason": "provider_concurrency_budget"}
    db_session.expire_all()
    assert report.status == run.status == "queued" and report.model_calls == 2
    assert run.finished_at is None
    assert db_session.get(ReportGenerationLease, report.id).lease_token is None
    assert sections[0].status == "ready" and sections[1].status == "running"
    assert report.coverage_json["grounding"]["validated_findings"] == 1
    assert report.coverage_json["grounding"]["cited_claim_blocks"] == 1
    assert paid_stages == ["evidence", "assessment"]
    assert len(list(db_session.scalars(select(AIReportStageArtifact)))) == 2
    first_usages = list(db_session.scalars(select(AIUsageEvent).where(AIUsageEvent.report_id == report.id)))
    assert len(first_usages) == 3  # Two received responses and one zero-I/O admission denial.
    capacity_blocked = False
    if cancel_before_resume:
        run.metadata_json = {**run.metadata_json, "cancel_requested_at": now.isoformat()}
        db_session.commit()
        with pytest.raises(report_generation.ReportGenerationError) as error:
            report_generation.generate_report(db_session, report_id=report.id, task_run_id=run.id)
        assert error.value.code == "canceled"
        assert paid_stages == ["evidence", "assessment"]
        assert len(list(db_session.scalars(select(AIUsageEvent).where(AIUsageEvent.report_id == report.id)))) == 3
        return

    start_ai_task_run(db_session, run_id=run.id, celery_task_id=run.celery_task_id)
    resumed_claim = claim_report_generation(
        db_session, report_id=report.id, lease_token="resumed-owner", lease_seconds=120,
    )
    assert resumed_claim.status == "claimed"
    assert resumed_claim.generation_fence > claim.generation_fence
    db_session.commit()
    result = report_generation.generate_report(db_session, report_id=report.id, task_run_id=run.id)
    db_session.expire_all()
    assert result.status == report.status == "ready" and result.model_calls == 3
    assert result.total_tokens == report.total_tokens == 450
    assert paid_stages == ["evidence", "assessment", "executive_summary"]
    assert report.coverage_json["grounding"]["validated_findings"] == 1
    assert report.coverage_json["grounding"]["cited_claim_blocks"] == 2
    assert report.coverage_json["grounding"]["status"] == "checked"
    assert len(list(db_session.scalars(select(AIReportStageArtifact)))) == 3
    usages = list(db_session.scalars(select(AIUsageEvent).where(AIUsageEvent.report_id == report.id)))
    assert len(usages) == 4 and sum(usage.total_tokens or 0 for usage in usages) == 450
    receipts = list(db_session.scalars(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == run.id,
    )))
    assert len(receipts) == 3 and all(receipt.state == "succeeded" for receipt in receipts)
