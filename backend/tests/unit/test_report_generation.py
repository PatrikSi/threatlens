import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.services import report_generation


def test_unexpected_generation_error_moves_report_to_terminal_state(db_session, monkeypatch):
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
        ),
    )
    monkeypatch.setattr(
        report_generation,
        "_synthesize_evidence_batches",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sensitive provider detail")),
    )

    with pytest.raises(RuntimeError, match="sensitive provider detail"):
        report_generation.generate_report(db_session, report_id=report.id, task_run_id=None)

    db_session.expire_all()
    failed = db_session.get(Report, report.id)
    assert failed is not None
    assert failed.status == "error"
    assert failed.generation_stage == "failed"
    assert failed.error_code == "internal_error"
    assert failed.error == "Report generation failed unexpectedly. Review the AI worker logs and retry the report."
