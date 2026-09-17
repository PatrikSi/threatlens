from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest
from sqlalchemy import event, select

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.article import Article
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.investigation import (
    Investigation,
    InvestigationMember,
    InvestigationNote,
)
from app.models.iam import IAMGroup, IAMGroupMembership
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.team import Team
from app.models.team_item_assessment import TeamItemAssessment
from app.services.authorization import authorization_context_for_user
from app.services.ai_extraction import build_verified_extraction
from app.services.data_access_policy import data_access_context_for_authorization
from app.services.data_access_envelopes import (
    DataAccessSourceInput,
    put_data_access_envelope_sources,
)
from app.services.export_job_contracts import ExportAuthorizationSnapshot
from app.services.mcp_read_contracts import MCPReadContext, MCPReadError, json_bytes
from app.services.mcp_read_service import call_read_tool
from app.services.secret_storage import encrypt_json
from tests.integration.test_data_policy_read_coverage import _enable_enforcement


def _context(db, user, scopes=None):
    scopes = scopes or [
        "read:mcp",
        "read:items",
        "read:reports",
        "read:investigations",
        "read:teams",
    ]
    _, prefix, digest = generate_api_token()
    token = ApiToken(
        user_id=user.id,
        name="MCP test",
        token_prefix=prefix,
        token_hash=digest,
        scopes=scopes,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(token)
    db.flush()
    authorization = authorization_context_for_user(db, user, credential_scopes=scopes)
    access = data_access_context_for_authorization(db, authorization)
    return MCPReadContext(
        user,
        authorization,
        access,
        ExportAuthorizationSnapshot(
            credential_kind="api_token",
            credential_id=token.id,
            permissions=authorization.permissions,
            enforced=access.enforced,
            allowed_label_ids=access.allowed_label_ids,
        ),
        b"test-signing-key-at-least-32-bytes-long",
    )


def _item(
    db,
    *,
    title="Defensive evidence",
    label=UNRESTRICTED_HANDLING_LABEL_ID,
    text="Stored evidence.",
):
    nonce = uuid.uuid4().hex
    feed = Feed(
        name="Test source",
        url=f"https://example.test/{nonce}.xml",
        handling_label_id=label,
    )
    db.add(feed)
    db.flush()
    item = Item(
        feed_id=feed.id,
        title=title,
        summary="Summary " + text,
        url=f"https://example.test/{nonce}",
        dedupe_key=nonce,
        content_hash=nonce * 2,
    )
    db.add(item)
    db.flush()
    db.add(Article(item_id=item.id, final_url=item.url, http_status=200, text=text))
    db.flush()
    return item


def _call(db, context, tool, arguments, **kwargs):
    return call_read_tool(
        db, context=context, tool_name=tool, arguments=arguments, **kwargs
    )


def test_article_search_and_evidence_enforce_current_feed_labels(
    db_session, seed_users, monkeypatch
):
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    visible = _item(db_session, title="Visible evidence")
    hidden = _item(db_session, title="Hidden evidence", label=restricted.id)
    context = _context(db_session, seed_users["analyst"])
    result = _call(db_session, context, "search_articles", {})
    assert [entry["item_id"] for entry in result["data"]["articles"]] == [
        str(visible.id)
    ]
    errors = []
    for identifier in (hidden.id, uuid.uuid4()):
        with pytest.raises(MCPReadError) as failure:
            _call(
                db_session,
                context,
                "get_article_evidence",
                {"item_id": str(identifier)},
            )
        errors.append((failure.value.code, str(failure.value)))
    assert errors[0] == errors[1]


def test_search_byte_truncation_does_not_skip_rows_on_next_page(db_session, seed_users):
    for _ in range(6):
        _item(db_session, text="🧪" * 4000)
    context = _context(db_session, seed_users["analyst"])
    seen = []
    args = {"limit": 5}
    while True:
        result = _call(
            db_session, context, "search_articles", args, max_response_bytes=4096
        )
        assert len(json_bytes(result)) <= 4096
        seen.extend(entry["item_id"] for entry in result["data"]["articles"])
        if not result["next_cursor"]:
            break
        args["cursor"] = result["next_cursor"]
    assert len(seen) == len(set(seen)) == 6


def test_article_evidence_only_selects_bounded_stored_text_and_never_mutates(
    db_session, seed_users
):
    item = _item(db_session, text="🧪" * 20000)
    context = _context(db_session, seed_users["analyst"])
    statements = []
    connection = db_session.connection()

    def capture(
        _connection, _cursor, statement, _parameters, _execution_context, _executemany
    ):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        result = _call(
            db_session,
            context,
            "get_article_evidence",
            {"item_id": str(item.id), "text_limit": 100},
        )
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert result["data"]["article_text"] == "🧪" * 100
    assert result["truncation"]["truncated"] is True
    assert any("substr(articles.text" in statement for statement in statements)
    assert all(
        statement.lstrip().split()[0].upper() == "SELECT" for statement in statements
    )


def test_private_investigation_requires_membership_and_has_bounded_notes(
    db_session, seed_users
):
    investigation = Investigation(
        title="Private case", visibility="private", description="Case summary"
    )
    db_session.add(investigation)
    db_session.flush()
    db_session.add(
        InvestigationMember(
            investigation_id=investigation.id,
            user_id=seed_users["analyst"].id,
            role="owner",
        )
    )
    for index in range(3):
        db_session.add(
            InvestigationNote(investigation_id=investigation.id, body=f"Note {index}")
        )
    db_session.flush()
    denied = _context(db_session, seed_users["viewer"])
    with pytest.raises(MCPReadError, match="not found"):
        _call(
            db_session,
            denied,
            "get_investigation",
            {"investigation_id": str(investigation.id)},
        )
    allowed = _context(db_session, seed_users["analyst"])
    result = _call(
        db_session,
        allowed,
        "get_investigation",
        {"investigation_id": str(investigation.id), "limit": 1},
    )
    assert len(result["data"]["notes"]) == 1
    assert "data.notes" in result["truncation"]["fields"]
    assert result["canonical_link"].endswith(f"/investigations/{investigation.id}")


def test_report_returns_retained_bounded_sections(db_session, seed_users):
    now = datetime.now(timezone.utc)
    report = Report(
        title="Stored report",
        status="ready",
        period_start=now,
        period_end=now,
        summary_text="Stored summary",
    )
    db_session.add(report)
    db_session.flush()
    db_session.add(
        ReportSection(
            report_id=report.id,
            section_key="summary",
            title="Summary",
            body_markdown="x" * 12000,
        )
    )
    db_session.flush()
    context = _context(db_session, seed_users["analyst"])
    result = _call(db_session, context, "get_report", {"report_id": str(report.id)})
    assert len(result["data"]["sections"][0]["body_markdown"]) == 8000
    assert result["truncation"]["truncated"] is True
    assert result["data"]["status"] == "ready"


def test_credential_revocation_and_missing_domain_scope_fail_closed(
    db_session, seed_users
):
    context = _context(db_session, seed_users["analyst"], scopes=["read:mcp"])
    with pytest.raises(MCPReadError, match="does not permit"):
        _call(db_session, context, "search_articles", {})
    context = _context(db_session, seed_users["analyst"])
    token = db_session.get(ApiToken, context.credential_snapshot.credential_id)
    token.revoked_at = datetime.now(timezone.utc)
    db_session.flush()
    with pytest.raises(MCPReadError, match="no longer permits"):
        _call(db_session, context, "search_articles", {})


def test_assessment_requires_team_membership_and_preserves_captured_result_label(
    db_session, seed_users, monkeypatch
):
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    item = _item(db_session)
    group = IAMGroup(key=f"mcp-team-{uuid.uuid4().hex}", name="MCP team")
    db_session.add(group)
    db_session.flush()
    team = Team(
        key=f"mcp-{uuid.uuid4().hex}", name="MCP team", membership_group_id=group.id
    )
    db_session.add(team)
    db_session.add(
        IAMGroupMembership(group_id=group.id, user_id=seed_users["analyst"].id)
    )
    db_session.flush()
    row = TeamItemAssessment(
        team_id=team.id,
        item_id=item.id,
        principal_id=seed_users["analyst"].id,
        context_version=0,
        source_version=1,
        result_context_version=0,
        result_source_version=1,
        authorization_encrypted={},
        result_json={
            "relevance_score": 0.4,
            "relevance_reasons": ["Stored assessment"],
            "information_gaps": [],
            "hunts": [],
        },
        result_source_encrypted=encrypt_json(
            [[str(item.id), str(item.feed_id), str(restricted.id)]]
        ),
    )
    db_session.add(row)
    db_session.flush()
    args = {"item_id": str(item.id), "team_id": str(team.id)}
    for role in ("viewer", "analyst"):
        context = _context(db_session, seed_users[role])
        with pytest.raises(MCPReadError, match="not found"):
            _call(db_session, context, "get_team_assessment", args)
    row.result_source_encrypted = encrypt_json(
        [[str(item.id), str(item.feed_id), str(UNRESTRICTED_HANDLING_LABEL_ID)]]
    )
    db_session.flush()
    context = _context(db_session, seed_users["analyst"])
    result = _call(db_session, context, "get_team_assessment", args)
    assert result["data"]["assessment"]["result"]["relevance_score"] == 0.4
    assert result["freshness"]["stale"] is True


def test_report_captured_label_remains_an_export_boundary(
    db_session, seed_users, monkeypatch
):
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    now = datetime.now(timezone.utc)
    report = Report(
        title="Restricted retained report",
        status="ready",
        period_start=now,
        period_end=now,
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
                handling_label_id=restricted.id,
                captured_policy_revision=2,
            )
        ],
    )
    context = _context(db_session, seed_users["analyst"])
    with pytest.raises(MCPReadError, match="not found"):
        _call(db_session, context, "get_report", {"report_id": str(report.id)})


def _stored_extraction(db, item):
    article = db.scalar(select(Article).where(Article.item_id == item.id))
    quote = "ExampleSuite received a defensive update."
    source = {
        "title": item.title,
        "summary": item.summary,
        "article_text": article.text,
    }
    extraction = build_verified_extraction(
        {
            "entities": [
                {
                    "id": "e1",
                    "kind": "product",
                    "name": "ExampleSuite",
                    "description": "A product identified in the source.",
                    "assertion": "reported",
                    "evidence": [{"source": "article_text", "quote": quote}],
                    "versions": [],
                }
            ],
            "relationships": [],
            "information_gaps": [],
        },
        messages=[
            {
                "role": "user",
                "content": json.dumps({"task": "item_enrichment", "item": source}),
            }
        ],
        article_id=article.id,
        article_retrieved_at=article.retrieved_at,
        source_version=item.classification_required_version,
        source_hash="a" * 64,
        article_text_length=len(article.text),
    )
    enrichment = ItemAIEnrichment(
        item_id=item.id,
        status="ready",
        source_hash="a" * 64,
        structured_extraction_json=extraction,
        generated_at=datetime.now(timezone.utc),
    )
    db.add(enrichment)
    db.flush()
    return enrichment


def test_article_evidence_includes_verified_shared_extraction_with_bounded_primary_text(
    db_session, seed_users
):
    item = _item(db_session, text="ExampleSuite received a defensive update.")
    _stored_extraction(db_session, item)
    context = _context(db_session, seed_users["analyst"])
    result = _call(
        db_session,
        context,
        "get_article_evidence",
        {"item_id": str(item.id), "text_limit": 8},
    )
    data = result["data"]
    assert len(data["article_text"]) == 8
    assert data["structured_extraction"]["entities"][0]["name"] == "ExampleSuite"
    evidence = data["structured_extraction"]["entities"][0]["evidence"][0]
    assert evidence["quote"] == "ExampleSuite received a defensive update."
    assert evidence["start"] == 0 and evidence["end"] == len(evidence["quote"])
    assert data["extraction_stale"] is False
    assert data["primary_source_fallback"] is False


def test_article_extraction_retains_history_but_marks_source_revision_changes_stale(
    db_session, seed_users
):
    item = _item(db_session, text="ExampleSuite received a defensive update.")
    _stored_extraction(db_session, item)
    item.classification_required_version += 1
    db_session.flush()
    context = _context(db_session, seed_users["analyst"])
    result = _call(
        db_session, context, "get_article_evidence", {"item_id": str(item.id)}
    )
    assert result["data"]["structured_extraction"] is not None
    assert result["data"]["extraction_stale"] is True
    assert result["data"]["primary_source_fallback"] is True
    assert result["freshness"]["extraction_stale"] is True


def test_oversized_stored_extraction_is_omitted_before_materialization(
    db_session, seed_users, monkeypatch
):
    from app.services import mcp_read_service

    item = _item(db_session)
    db_session.add(
        ItemAIEnrichment(
            item_id=item.id,
            status="ready",
            structured_extraction_json={"oversized": "🧪" * 100000},
        )
    )
    db_session.flush()
    context = _context(db_session, seed_users["analyst"])
    monkeypatch.setattr(
        mcp_read_service,
        "item_extraction_response",
        lambda *args, **kwargs: pytest.fail(
            "oversized JSON must not reach the domain parser"
        ),
    )
    result = _call(
        db_session, context, "get_article_evidence", {"item_id": str(item.id)}
    )
    data = result["data"]
    assert data["structured_extraction"] is None
    assert data["structured_extraction_omitted"] is True
    assert (
        data["structured_extraction_omission_reason"] == "stored_extraction_byte_limit"
    )
    assert data["primary_source_fallback"] is True
    assert data["article_text"] == "Stored evidence."
    assert "data.structured_extraction" in result["truncation"]["fields"]
