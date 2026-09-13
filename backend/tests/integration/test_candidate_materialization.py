from datetime import datetime, timedelta, timezone
import tracemalloc
from types import SimpleNamespace
import uuid

from sqlalchemy import event

from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.report import Report
from app.services.data_access_policy import DataAccessContext
from app.services import investigation_evidence_candidates as candidates


def test_report_previews_bound_materialization_and_search_full_documents(db_session):
    now = datetime.now(timezone.utc)
    summary = "\u2003\n" + "S" * 65536 + " searchable-tail \u3000"
    reports = [
        Report(
            title="\u2003Candidate report\u3000", report_type="custom", status="ready",
            period_start=now - timedelta(days=1), period_end=now, created_at=now,
            summary_text=summary, generation_context_json={"unused": "X" * 262144},
        )
        for _ in range(50)
    ]
    db_session.add_all(reports)
    db_session.flush()
    db_session.expunge_all()
    loaded_reports = []

    def observe_report_load(target, _context):
        loaded_reports.append(target.id)

    access = DataAccessContext(
        mode="off", policy_revision=1, coverage_version=1,
        principal_type="user", principal_id=uuid.uuid4(), principal_eligible=True,
        allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
    )
    event.listen(Report, "load", observe_report_load)
    tracemalloc.start()
    try:
        result, total, truncated = candidates._load_report_candidates(
            db_session, investigation_id=uuid.uuid4(), user=SimpleNamespace(role="admin"),
            data_access=access, analysis=candidates.analyze_candidate_query("searchable-tail"),
            since=now - timedelta(days=1), until=now + timedelta(seconds=1), limit=50,
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        event.remove(Report, "load", observe_report_load)

    assert len(result) == total == 50
    assert not truncated
    assert not loaded_reports
    assert peak < 4 * 1024 * 1024, f"Preview materialized {peak} bytes"
    assert all(row.candidate.title == "Candidate report" for row in result)
    assert all(row.candidate.description == "S" * 597 + "..." for row in result)
