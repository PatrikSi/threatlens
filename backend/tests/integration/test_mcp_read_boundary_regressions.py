"""Real stored-data retrieval at the privacy and materialization boundaries."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from sqlalchemy import delete
from app.models.article import Article
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.investigation import (
    Investigation,
    InvestigationEvidence,
    InvestigationMember,
    InvestigationNote,
)
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.schemas.mcp_reads import ArticleEvidenceArguments
from app.services.data_access_envelopes import (
    DataAccessSourceInput,
    put_data_access_envelope_sources,
)
from app.services.mcp_read_contracts import MCPReadError, encode_bound_cursor
from tests.integration.test_mcp_read_service import _call, _context, _item
from tests.integration.test_mcp_team_read_adapters import (
    rich_reads as rich_fixture,
    assessment_setup as assessment_fixture,
)

assessment_setup = assessment_fixture
rich_reads = rich_fixture


def test_search_filters_escape_literals_and_reject_incompatible_indicator_types(
    db_session, seed_users
):
    wanted = _item(db_session, title="Literal 100% coverage")
    _item(db_session, title="Literal 1000 coverage")
    context = _context(db_session, seed_users["analyst"])
    now = datetime.now(timezone.utc)
    result = _call(
        db_session,
        context,
        "search_articles",
        {
            "q": "100%",
            "feed_id": str(wanted.feed_id),
            "since": (now - timedelta(days=1)).isoformat(),
            "until": (now + timedelta(days=1)).isoformat(),
        },
    )
    assert [row["item_id"] for row in result["data"]["articles"]] == [str(wanted.id)]
    with pytest.raises(MCPReadError) as error:
        _call(
            db_session,
            context,
            "search_articles",
            {"indicator_type": "ipv4", "indicator_value": "evil.test"},
        )
    assert error.value.code == "invalid_arguments"
    with pytest.raises(MCPReadError) as error:
        _call(db_session, context, "unregistered_tool", {})
    assert error.value.code == "unknown_tool"
    wrong = replace(
        context, authorization=replace(context.authorization, principal_id=uuid.uuid4())
    )
    with pytest.raises(MCPReadError) as error:
        _call(db_session, wrong, "search_articles", {})
    assert error.value.code == "access_denied"


def test_evidence_cursor_rejects_signed_invalid_positions_and_handles_removed_bodies(
    db_session, seed_users
):
    item = _item(db_session)
    context = _context(db_session, seed_users["analyst"])
    args = ArticleEvidenceArguments(item_id=item.id)
    for position in (
        {},
        {"offset": True},
        {"offset": -1},
        {"offset": 100_000_001},
        {"offset": 1.5},
    ):
        cursor = encode_bound_cursor(context, args, position)
        with pytest.raises(MCPReadError) as error:
            _call(
                db_session,
                context,
                "get_article_evidence",
                {**args.model_dump(mode="json"), "cursor": cursor},
            )
        assert error.value.code == "invalid_cursor"
    db_session.execute(delete(Article).where(Article.item_id == item.id))
    db_session.flush()
    result = _call(
        db_session, context, "get_article_evidence", {"item_id": str(item.id)}
    )
    assert result["next_cursor"] is None
    assert result["data"]["text_length"] == 0
    assert result["data"]["article_text"] is None
    assert result["data"]["content_available"] is False


def test_malformed_saved_extraction_uses_primary_source_with_explicit_disclosure(
    db_session, seed_users
):
    item = _item(db_session)
    db_session.add(
        ItemAIEnrichment(
            item_id=item.id,
            status="ready",
            structured_extraction_json={"malformed": True},
        )
    )
    db_session.flush()
    result = _call(
        db_session,
        _context(db_session, seed_users["analyst"]),
        "get_article_evidence",
        {"item_id": str(item.id)},
    )
    assert result["data"]["article_text"] == "Stored evidence."
    assert (
        result["data"]["structured_extraction_omission_reason"]
        == "invalid_stored_extraction"
    )
    assert result["data"]["primary_source_fallback"] is True


def test_report_bounds_large_summaries_sections_and_retained_source_snapshots(
    db_session, seed_users
):
    now = datetime.now(timezone.utc)
    report = Report(
        title="Bounded report",
        status="ready",
        period_start=now,
        period_end=now,
        summary_text="s" * 5000,
    )
    db_session.add(report)
    db_session.flush()
    for index in range(2):
        db_session.add(
            ReportSection(
                report_id=report.id,
                section_key=f"s{index}",
                title="Section",
                body_markdown="b" * 9000,
                position=index,
            )
        )
        db_session.add(
            ReportSourceItem(
                report_id=report.id,
                citation_key=f"S{index}",
                title_snapshot="t" * 2000,
                feed_name_snapshot="f" * 500,
                url_snapshot="https://example.test/" + "u" * 3000,
                first_seen_at_snapshot=now,
            )
        )
    db_session.flush()
    result = _call(
        db_session,
        _context(db_session, seed_users["analyst"]),
        "get_report",
        {"report_id": str(report.id), "limit": 1},
    )
    assert len(result["data"]["summary"]) == 4000
    assert len(result["data"]["sections"]) == len(result["data"]["sources"]) == 1
    assert len(result["data"]["sources"][0]["title"]) == 1000
    assert {"data.summary", "data.sections", "data.sources"} <= set(
        result["truncation"]["fields"]
    )


def test_investigation_bounds_description_notes_and_evidence_snapshots(
    db_session, seed_users
):
    user = seed_users["analyst"]
    investigation = Investigation(
        title="Bounded investigation", description="d" * 5000, visibility="private"
    )
    db_session.add(investigation)
    db_session.flush()
    db_session.add(
        InvestigationMember(
            investigation_id=investigation.id, user_id=user.id, role="owner"
        )
    )
    for _ in range(2):
        item = _item(db_session)
        db_session.add(
            InvestigationEvidence(
                investigation_id=investigation.id,
                source_type="item",
                source_id=item.id,
                title_snapshot="Evidence",
                description_snapshot="e" * 3000,
                url_snapshot="https://example.test/" + "x" * 3000,
                note="n" * 3000,
            )
        )
        db_session.add(
            InvestigationNote(investigation_id=investigation.id, body="b" * 5000)
        )
    db_session.flush()
    result = _call(
        db_session,
        _context(db_session, user),
        "get_investigation",
        {"investigation_id": str(investigation.id), "limit": 1},
    )
    assert len(result["data"]["description"]) == 4000
    assert len(result["data"]["evidence"]) == len(result["data"]["notes"]) == 1
    assert len(result["data"]["evidence"][0]["note"]) == 2000
    assert {"data.description", "data.evidence", "data.notes"} <= set(
        result["truncation"]["fields"]
    )


def test_report_lineage_limit_precedes_materialization(
    db_session, seed_users, monkeypatch
):
    from app.services import mcp_read_service

    now = datetime.now(timezone.utc)
    report = Report(
        title="Large lineage", status="ready", period_start=now, period_end=now
    )
    db_session.add(report)
    db_session.flush()
    put_data_access_envelope_sources(
        db_session,
        resource_type="report",
        resource_id=report.id,
        sources=[
            DataAccessSourceInput(
                source_type="item",
                source_id=str(uuid.uuid4()),
                source_version="1",
                handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                captured_policy_revision=1,
            )
            for _ in range(2)
        ],
    )
    monkeypatch.setattr(mcp_read_service, "MAX_LINEAGE_SOURCES", 1)
    with pytest.raises(MCPReadError) as error:
        _call(
            db_session,
            _context(db_session, seed_users["analyst"]),
            "get_report",
            {"report_id": str(report.id)},
        )
    assert error.value.code == "resource_too_large"


def test_ready_assessment_has_current_status_and_rejects_oversized_saved_result(
    db_session, rich_reads
):
    args = {"team_id": rich_reads["team"]["id"], "item_id": str(rich_reads["item"].id)}
    current = _call(db_session, rich_reads["context"], "get_team_assessment", args)
    assert current["data"]["assessment"]["status"] == "ready"
    assert current["data"]["assessment"]["stale"] is False
    rich_reads["row"].result_json = {"oversized": "x" * 270000}
    db_session.flush()
    with pytest.raises(MCPReadError) as error:
        _call(db_session, rich_reads["context"], "get_team_assessment", args)
    assert error.value.code == "resource_too_large"
