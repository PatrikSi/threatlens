"""Exercise immutable report evidence across the provenance deployment boundary."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ai_workflow import AIReportStageArtifact
from app.models.report_source_item import ReportSourceItem
from app.schemas.reports import ReportArticleFilters, ReportCreateRequest, ReportPromptConfig, ReportSectionConfig
from app.services import report_generation
from app.services.ai_config import load_active_ai_settings
from app.services.ai_provider_client import AICompletionResult
from app.services.ai_report_stage_artifacts import store_report_stage_completion
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import data_access_context_for_authorization
from app.services.report_evidence_contract import REPORT_EVIDENCE_CONTRACT_VERSION
from app.services.report_sources import build_report_source_plan, report_preview_from_plan
from app.services.report_storage import create_report_from_plan, report_detail_response, reset_report_for_retry
from tests.integration.test_ai_evidence_provenance import _success
from tests.integration.test_ai_feature_output_validation import configured_item as configured_item
from tests.unit.test_ai_report_stage_artifacts import completion, setup_report


def _legacy_report(db, *, coverage=None, body="Existing grounded summary: The affected version is 1.0."):
    report, run = setup_report(db)
    report.status = "queued"
    report.coverage_json = coverage or {}
    report.source_count = report.included_source_count = 1
    source = ReportSourceItem(
        report_id=report.id, citation_key="S1", included=True, rank=1,
        title_snapshot="Synthetic historical bulletin", feed_name_snapshot="Feed",
        url_snapshot="https://example.test/source", first_seen_at_snapshot=datetime.now(timezone.utc),
        evidence_text=f"[S1] Synthetic historical bulletin\n{body}", estimated_tokens=40,
    )
    db.add(source)
    db.commit()
    return report, run, source


def _forbid_generation(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("rejected snapshots must not load providers, replay stages or send requests")

    for name in ("load_active_ai_settings", "_synthesize_evidence_batches", "request_ai_json_with_usage"):
        monkeypatch.setattr(report_generation, name, forbidden)
    monkeypatch.setattr("app.services.ai_report_stage_artifacts.load_report_stage_completion", forbidden)


@pytest.mark.parametrize("version", [None, 0, 2, True, "1", 1.0, {"version": 1}])
def test_unknown_contract_fails_before_provider_or_stage_replay(db_session, monkeypatch, version):
    coverage = {"evidence_contract_version": version} if version is not None else {}
    report, run, source = _legacy_report(db_session, coverage=coverage)
    frozen = source.evidence_text
    _forbid_generation(monkeypatch)

    with pytest.raises(report_generation.ReportGenerationError, match="create a new report from current sources") as caught:
        report_generation.generate_report(db_session, report_id=report.id, task_run_id=run.id)

    db_session.expire_all()
    assert caught.value.code == report.error_code == "source_snapshot_requires_rebuild"
    assert report.status == "error" and report.generation_stage == "failed"
    assert report.coverage_json == coverage
    assert source.evidence_text == frozen


def test_retry_keeps_legacy_snapshot_unverified_without_rewriting_its_evidence(db_session, monkeypatch):
    report, run, source = _legacy_report(db_session)
    report.status = "error"
    source.included = False
    source.exclusion_reason = "execution_context_budget"
    db_session.commit()
    frozen = source.evidence_text
    reset_report_for_retry(db_session, report=report)
    db_session.commit()
    assert source.included is True
    assert "evidence_contract_version" not in report.coverage_json
    _forbid_generation(monkeypatch)

    with pytest.raises(report_generation.ReportGenerationError, match="retrying this snapshot will not rebuild"):
        report_generation.generate_report(db_session, report_id=report.id, task_run_id=run.id)
    assert report.status == "error" and source.evidence_text == frozen


def test_legacy_paid_stages_remain_intact_but_are_never_replayed(db_session, monkeypatch):
    report, run, source = _legacy_report(db_session)
    report.model_calls, report.prompt_tokens, report.completion_tokens, report.total_tokens = 2, 80, 20, 100
    report.coverage_json = {"grounding": {"validated_findings": 1, "status": "checked"}}
    store_report_stage_completion(
        db_session, task_run_id=run.id, report_id=report.id,
        operation_scope="evidence_batch:1", request_fingerprint="a" * 64, completion=completion(),
    )
    db_session.commit()
    artifact = db_session.get(AIReportStageArtifact, (run.id, "evidence_batch:1"))
    original = dict(artifact.completion_json)
    _forbid_generation(monkeypatch)

    with pytest.raises(report_generation.ReportGenerationError):
        report_generation.generate_report(db_session, report_id=report.id, task_run_id=run.id)
    db_session.expire_all()
    assert artifact.completion_json == original
    assert report.model_calls == 2 and report.total_tokens == 100
    assert report.coverage_json["grounding"]["validated_findings"] == 1
    assert "Existing grounded summary" in source.evidence_text


def test_unversioned_primary_only_snapshot_also_requires_a_new_plan(db_session, monkeypatch):
    # Free-form publisher text and truncated legacy labels are not a trusted
    # origin discriminator. Do not infer safety from the absence of an AI label.
    report, run, _source = _legacy_report(db_session, body="Publisher summary: Security updates are available.")
    _forbid_generation(monkeypatch)
    with pytest.raises(report_generation.ReportGenerationError, match="create a new report"):
        report_generation.generate_report(db_session, report_id=report.id, task_run_id=run.id)


def test_completed_legacy_report_stays_readable_without_generation(db_session, monkeypatch):
    report, run, source = _legacy_report(db_session)
    report.status = "ready"
    report.model_calls, report.total_tokens = 2, 100
    db_session.commit()
    _forbid_generation(monkeypatch)
    result = report_generation.generate_report(db_session, report_id=report.id, task_run_id=run.id)
    detail = report_detail_response(db_session, report=report)
    assert result.status == detail.status == "ready" and result.total_tokens == 100
    assert detail.sources[0].title == source.title_snapshot
    assert detail.sources[0].citation_key == "S1"


@pytest.mark.parametrize("stale", [False, True])
def test_current_source_plan_marks_contract_and_persists_only_verified_ai_metadata(
    db_session, configured_item, seed_users, monkeypatch, stale,
):
    item, _settings = configured_item
    enrichment = _success(db_session, item, monkeypatch)
    if stale:
        item.summary = "The affected version is now 2.0."
        item.classification_required_version += 1
        db_session.commit()
    user = seed_users["analyst"]
    active = replace(load_active_ai_settings(db_session, feature_type="report"), reporting_enabled=True)
    filters = ReportArticleFilters(feed_ids=[item.feed_id])
    sections = [ReportSectionConfig(key="sources", title="Sources")]
    plan = build_report_source_plan(
        db_session, user_id=user.id, filters=filters, excluded_item_ids=[],
        prompt=ReportPromptConfig(), sections=sections, active=active,
        data_access=data_access_context_for_authorization(db_session, authorization_context_for_user(db_session, user)),
    )
    preview = report_preview_from_plan(plan, preview_limit=10)
    assert preview.items[0].ai_relevance_score == (None if stale else enrichment.relevance_score)
    assert preview.items[0].ai_relevance_label == (None if stale else enrichment.relevance_label)
    now = datetime.now(timezone.utc)
    report = create_report_from_plan(
        db_session, user_id=user.id, payload=ReportCreateRequest(
            period_start=now - timedelta(days=1), period_end=now,
            filters=filters, sections=sections,
        ), plan=plan, template=None, active=active,
    )
    db_session.commit()
    source = db_session.scalar(select(ReportSourceItem).where(ReportSourceItem.report_id == report.id))
    assert report.coverage_json["evidence_contract_version"] == REPORT_EVIDENCE_CONTRACT_VERSION
    assert ("Prior AI summary" in source.evidence_text) is not stale
    assert source.relevance_score_snapshot == (None if stale else enrichment.relevance_score)
    assert source.relevance_label_snapshot == (None if stale else enrichment.relevance_label)
    frozen = source.evidence_text
    report.status = "error"
    reset_report_for_retry(db_session, report=report)
    db_session.commit()
    assert report.coverage_json["evidence_contract_version"] == REPORT_EVIDENCE_CONTRACT_VERSION
    sent = []

    def synthesize(_db, _active, **kwargs):
        sent.append(kwargs["messages"])
        return AICompletionResult(
            payload={"findings": []}, provider=active.provider_type, model=active.model,
            latency_ms=1, prompt_tokens=100, completion_tokens=10, total_tokens=110,
        )

    monkeypatch.setattr(report_generation, "load_active_ai_settings", lambda *_args, **_kwargs: active)
    monkeypatch.setattr(report_generation, "request_ai_json_with_usage", synthesize)
    result = report_generation.generate_report(db_session, report_id=report.id, task_run_id=None)
    assert result.status == "ready" and len(sent) == 1
    assert source.evidence_text == frozen
