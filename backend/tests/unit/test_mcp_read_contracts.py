from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError

from app.schemas.mcp_reads import MCPReadResult, SearchArticlesArguments
from app.services.mcp_read_contracts import (
    MCPReadContext,
    MCPReadError,
    bounded_result,
    decode_cursor,
    encode_cursor,
    json_bytes,
)


def _context():
    return MCPReadContext(
        principal=SimpleNamespace(id=uuid.uuid4()),
        authorization=SimpleNamespace(principal_type="user", policy_revision=1),
        data_access=SimpleNamespace(policy_revision=1),
        credential_snapshot=SimpleNamespace(credential_id=uuid.uuid4()),
        cursor_secret=b"test-cursor-key-with-at-least-32-bytes",
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": True},
        {"limit": "5"},
        {"limit": 51},
        {"q": "x" * 201},
        {"url": "https://example.test/"},
        {"since": "2026-01-01"},
        {"since": 1},
        {"since": "1777777777"},
        {"since": "20260101"},
        {"since": "0001-01-01T00:00:00+14:00"},
        {"until": "9999-12-31T23:59:59-14:00"},
        {"since": "2026-02-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
    ],
)
def test_search_contract_rejects_unbounded_or_ambiguous_arguments(arguments):
    with pytest.raises(ValidationError):
        SearchArticlesArguments.model_validate(arguments)


def test_cursor_is_bound_to_principal_credential_filters_and_policy():
    context = _context()
    now = datetime.now(timezone.utc)
    identifier = uuid.uuid4()
    args = SearchArticlesArguments(q="defensive evidence")
    cursor = encode_cursor(
        context, args, last_seen=now, last_id=identifier, snapshot_at=now
    )
    continued = args.model_copy(update={"cursor": cursor})
    assert decode_cursor(context, continued) == (now, identifier, now)
    for changed in (
        replace(context, principal=SimpleNamespace(id=uuid.uuid4())),
        replace(
            context, credential_snapshot=SimpleNamespace(credential_id=uuid.uuid4())
        ),
        replace(context, data_access=SimpleNamespace(policy_revision=2)),
    ):
        with pytest.raises(MCPReadError, match="invalid or expired"):
            decode_cursor(changed, continued)
    with pytest.raises(MCPReadError):
        decode_cursor(context, continued.model_copy(update={"q": "other"}))
    with pytest.raises(MCPReadError):
        decode_cursor(
            context, continued.model_copy(update={"cursor": cursor[:-8] + "AAAAAAAA"})
        )


def test_cursor_expiry_is_checked():
    context = _context()
    args = SearchArticlesArguments()
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    cursor = encode_cursor(
        context, args, last_seen=old, last_id=uuid.uuid4(), snapshot_at=old
    )
    with pytest.raises(MCPReadError):
        decode_cursor(context, args.model_copy(update={"cursor": cursor}))


def test_iso_timestamps_are_normalized_to_utc_for_cursor_binding():
    context = _context()
    now = datetime.now(timezone.utc)
    args = SearchArticlesArguments(since="2026-09-17T13:30:00+02:00")
    assert args.since.isoformat() == "2026-09-17T11:30:00+00:00"
    cursor = encode_cursor(
        context, args, last_seen=now, last_id=uuid.uuid4(), snapshot_at=now
    )
    equivalent = SearchArticlesArguments(since="2026-09-17T11:30:00Z", cursor=cursor)
    assert decode_cursor(context, equivalent) is not None


def test_cursor_with_future_snapshot_is_rejected():
    context = _context()
    args = SearchArticlesArguments()
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    cursor = encode_cursor(
        context, args, last_seen=future, last_id=uuid.uuid4(), snapshot_at=future
    )
    with pytest.raises(MCPReadError):
        decode_cursor(context, args.model_copy(update={"cursor": cursor}))


@pytest.mark.parametrize("cursor", ["", "%%%", "a", "null", "🎯"])
def test_malformed_cursor_fails_with_a_stable_error(cursor):
    with pytest.raises(MCPReadError) as caught:
        decode_cursor(_context(), SearchArticlesArguments(cursor=cursor))
    assert caught.value.code == "invalid_cursor"


def test_response_cap_counts_utf8_and_preserves_truncation_metadata():
    result = MCPReadResult(
        data={"article_text": '🧪\\"' * 16000},
        provenance={"content_trust": "untrusted_stored_content"},
        freshness={"retrieved_at": datetime.now(timezone.utc).isoformat()},
        canonical_link="/api/v1/items/record",
    )
    bounded = bounded_result(result, max_bytes=4096)
    assert len(json_bytes(bounded)) <= 4096
    assert bounded["truncation"]["truncated"] is True
    assert bounded["truncation"]["fields"] == ["data.article_text"]
    assert bounded["canonical_link"] == result.canonical_link


def test_byte_truncation_keeps_a_search_row_for_resumable_pagination():
    bounded = bounded_result(
        MCPReadResult(
            data={
                "articles": [
                    {"item_id": str(uuid.uuid4()), "summary": "a" * 2000}
                    for _ in range(50)
                ]
            },
            provenance={},
            freshness={},
            canonical_link="/",
        ),
        max_bytes=4096,
    )
    assert 1 <= len(bounded["data"]["articles"]) < 50
    assert len(json_bytes(bounded)) <= 4096


def test_truncation_paths_do_not_crowd_out_minimum_wire_budget():
    result = MCPReadResult(
        data={
            "articles": [
                {"item_id": str(uuid.uuid4()), "summary": "🧪" * 2000}
                for _ in range(50)
            ]
        },
        provenance={},
        freshness={},
        canonical_link="/",
        truncation={
            "truncated": True,
            "fields": [
                f"data.articles.{index}.{field}"
                for index in range(50)
                for field in ("summary", "title", "source_url")
            ],
            "reasons": ["field_or_collection_limit"],
        },
    )
    budget = (16384 - 2048) // 3
    bounded = bounded_result(result, max_bytes=budget)
    assert len(json_bytes(bounded)) <= budget
    assert bounded["truncation"]["fields"] == ["data.articles"]
    assert bounded["data"]["articles"]


def test_wire_pressure_omits_extraction_as_a_unit_preserving_primary_evidence():
    result = MCPReadResult(
        data={
            "article_text": "Primary evidence remains available.",
            "structured_extraction": {
                "entities": [{"quote": "verified quote" * 2000}],
                "relationships": [],
            },
            "structured_extraction_omitted": False,
            "primary_source_fallback": False,
        },
        provenance={},
        freshness={},
        canonical_link="/api/v1/items/test",
    )
    bounded = bounded_result(result, max_bytes=4096)
    assert bounded["data"]["structured_extraction"] is None
    assert bounded["data"]["structured_extraction_omitted"] is True
    assert (
        bounded["data"]["structured_extraction_omission_reason"]
        == "response_byte_limit"
    )
    assert bounded["data"]["primary_source_fallback"] is True
    assert bounded["data"]["article_text"] == "Primary evidence remains available."
    assert bounded["truncation"]["fields"] == ["data.structured_extraction"]
