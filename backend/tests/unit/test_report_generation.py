import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.services import report_generation
from app.services.ai_context_budget import build_context_budget
from app.services.report_prompt_budget import build_evidence_messages, estimate_message_tokens


def test_unexpected_generation_error_moves_report_to_terminal_state(
    db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    report = Report(
        id=uuid.uuid4(),
        title="Failure-path report",
        report_type="custom",
        status="queued",
        trigger_source="manual",
        generation_stage="queued",
        period_start=now - timedelta(days=7),
        period_end=now,
        filters_json={},
        prompt_config_json={"objective": "Summarize material threats."},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
        source_count=1,
        included_source_count=1,
        estimated_input_tokens=10,
        context_window_tokens=8192,
        generation_batches=1,
    )
    db_session.add(report)
    db_session.flush()
    db_session.add(
        ReportSourceItem(
            report_id=report.id,
            citation_key="S1",
            included=True,
            rank=1,
            title_snapshot="Source",
            feed_name_snapshot="Feed",
            url_snapshot="https://example.com/source",
            first_seen_at_snapshot=now,
            tags_snapshot_json=[],
            iocs_snapshot_json=[],
            evidence_text="[S1] Source evidence",
            estimated_tokens=10,
        )
    )
    db_session.add(
        ReportSection(
            report_id=report.id,
            section_key="executive_summary",
            title="Executive Summary",
            position=1,
            status="pending",
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        report_generation,
        "load_active_ai_settings",
        lambda _db: SimpleNamespace(
            ai_enabled=True,
            ai_configured=True,
            reporting_enabled=True,
            report_context_window_tokens=8192,
            report_reserved_output_tokens=1200,
            report_context_safety_percent=15,
            report_source_token_cap=700,
            report_max_model_calls=20,
        ),
    )
    monkeypatch.setattr(
        report_generation,
        "_synthesize_evidence_batches",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("sensitive provider detail")
        ),
    )

    with pytest.raises(RuntimeError, match="sensitive provider detail"):
        report_generation.generate_report(
            db_session, report_id=report.id, task_run_id=None
        )

    db_session.expire_all()
    failed = db_session.get(Report, report.id)
    assert failed is not None
    assert failed.status == "error"
    assert failed.generation_stage == "failed"
    assert failed.error_code == "internal_error"
    assert (
        failed.error
        == "Report generation failed unexpectedly. Review the AI worker logs and retry the report."
    )


def test_runtime_plan_degrades_legacy_sources_to_current_context_and_call_limits(
    db_session,
):
    now = datetime.now(timezone.utc)
    report = Report(
        id=uuid.uuid4(),
        title="Legacy oversized report",
        report_type="custom",
        status="queued",
        trigger_source="retry",
        generation_stage="queued",
        period_start=now - timedelta(days=7),
        period_end=now,
        filters_json={},
        prompt_config_json={
            "objective": "Summarize material threats.",
            "custom_instructions": "Preserve evidence and uncertainty. " * 100,
        },
        generation_context_json={
            "company_context": {"profile_text": "Company context. " * 200}
        },
        sections_config_json=[],
        metrics_json={},
        coverage_json={"warnings": []},
        source_count=18,
        included_source_count=18,
        estimated_input_tokens=18 * 700,
        context_window_tokens=4096,
        generation_batches=1,
    )
    db_session.add(report)
    db_session.flush()
    sources = []
    for index in range(1, 19):
        source = ReportSourceItem(
            report_id=report.id,
            citation_key=f"S{index}",
            included=True,
            rank=index,
            title_snapshot=f"Source {index}",
            feed_name_snapshot="Feed",
            url_snapshot=f"https://example.com/source/{index}",
            first_seen_at_snapshot=now,
            tags_snapshot_json=[],
            iocs_snapshot_json=[],
            evidence_text=f"[S{index}] Evidence\n" + ("technical detail\n" * 300),
            estimated_tokens=700,
        )
        db_session.add(source)
        sources.append(source)
    sections = [
        ReportSection(
            report_id=report.id,
            section_key="key_developments",
            title="Key Developments",
            position=1,
            status="pending",
        )
    ]
    db_session.add_all(sections)
    db_session.commit()
    budget = build_context_budget(
        context_window_tokens=4096,
        reserved_output_tokens=512,
        safety_percent=10,
    )
    active = SimpleNamespace(report_source_token_cap=700, report_max_model_calls=3)

    selected, plan = report_generation._prepare_runtime_evidence(
        db_session,
        active=active,
        report=report,
        sources=sources,
        sections=sections,
        budget=budget,
    )

    assert 0 < len(selected) < len(sources)
    assert plan.batch_count <= 2
    assert report.included_source_count == len(selected)
    assert report.excluded_source_count == len(sources) - len(selected)
    assert any(
        "Execution omitted" in warning
        for warning in report.coverage_json["warnings"]
    )
    assert all(
        source.exclusion_reason == "execution_context_budget"
        for source in sources[len(selected) :]
    )
    for batch in plan.batches:
        messages, _ = build_evidence_messages(
            prompt=report.prompt_config_json,
            generation_context=report.generation_context_json,
            evidence=batch,
            budget=budget,
        )
        assert estimate_message_tokens(messages) <= budget.usable_input_tokens
