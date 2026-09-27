"""Real database coverage of richer, still read-only MCP boundaries."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.article import Article
from app.models.ioc import IOC, ItemIOC
from app.services.mcp_read_contracts import MCPReadError, json_bytes
from tests.integration.test_mcp_read_service import _call, _context, _item


def test_evidence_cursor_delivers_all_characters_under_byte_budget(
    db_session, seed_users
):
    source = "界🧪z" * 4500
    item = _item(db_session, text=source)
    context = _context(db_session, seed_users["analyst"])
    args = {"item_id": str(item.id), "text_limit": 3000}
    collected = ""
    for _ in range(200):
        result = _call(
            db_session, context, "get_article_evidence", args, max_response_bytes=4096
        )
        assert len(json_bytes(result)) <= 4096
        assert result["data"]["text_offset"] == len(collected)
        assert result["data"]["article_text"]
        collected += result["data"]["article_text"]
        if not result["next_cursor"]:
            break
        args["cursor"] = result["next_cursor"]
    assert collected == source


def test_evidence_cursor_rejects_new_revision_and_other_credential(
    db_session, seed_users
):
    item = _item(db_session, text="evidence " * 100)
    context = _context(db_session, seed_users["analyst"])
    args = {"item_id": str(item.id), "text_limit": 100}
    page = _call(db_session, context, "get_article_evidence", args)
    args["cursor"] = page["next_cursor"]
    other = _context(db_session, seed_users["analyst"])
    with pytest.raises(MCPReadError, match="invalid or expired"):
        _call(db_session, other, "get_article_evidence", args)
    article = db_session.scalar(select(Article).where(Article.item_id == item.id))
    article.retrieved_at += timedelta(seconds=1)
    db_session.flush()
    with pytest.raises(MCPReadError, match="article changed"):
        _call(db_session, context, "get_article_evidence", args)


def test_exact_indicator_search_refangs_and_never_matches_prefixes(
    db_session, seed_users
):
    yes, no = _item(db_session), _item(db_session)
    for item, value in ((yes, "evil.test"), (no, "not-evil.test")):
        ioc = IOC(type="domain", value_raw=value, value_norm=value)
        db_session.add(ioc)
        db_session.flush()
        db_session.add(ItemIOC(item_id=item.id, ioc_id=ioc.id))
    db_session.flush()
    context = _context(db_session, seed_users["analyst"])
    result = _call(
        db_session,
        context,
        "search_articles",
        {"indicator_type": "domain", "indicator_value": "evil[.]test"},
    )
    assert [r["item_id"] for r in result["data"]["articles"]] == [str(yes.id)]


def test_attack_lookup_uses_installed_catalog(db_session, seed_users):
    context = _context(db_session, seed_users["analyst"])
    result = _call(
        db_session, context, "lookup_attack_technique", {"technique_id": "T1059"}
    )
    assert result["data"]["technique"]["id"] == "T1059"
    with pytest.raises(MCPReadError, match="not present"):
        _call(db_session, context, "lookup_attack_technique", {"technique_id": "T0000"})
