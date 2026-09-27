"""Malformed, expired and oversized evidence continuations fail predictably."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import base64
import hmac
import json
import uuid
import pytest
from app.schemas.mcp_reads import ArticleEvidenceArguments, MCPReadResult
from app.services.mcp_read_contracts import (
    MCPReadError,
    bounded_result,
    decode_bound_cursor,
    encode_bound_cursor,
)
from tests.unit.test_mcp_read_contracts import _context


def _rewrite(context, cursor, **changes):
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    payload = json.loads(raw[:-32])
    payload.update(changes)
    encoded = json.dumps(payload).encode()
    return (
        base64.urlsafe_b64encode(
            encoded + hmac.digest(context.cursor_secret, encoded, "sha256")
        )
        .rstrip(b"=")
        .decode()
    )


def test_evidence_cursor_binds_exact_request_and_has_no_position_without_cursor():
    context = _context()
    args = ArticleEvidenceArguments(item_id=uuid.uuid4(), text_limit=100)
    assert decode_bound_cursor(context, args) is None
    position = {"offset": 100, "revision": "a" * 64}
    cursor = encode_bound_cursor(context, args, position)
    assert (
        decode_bound_cursor(context, args.model_copy(update={"cursor": cursor}))
        == position
    )
    for changed in ({"item_id": uuid.uuid4()}, {"text_limit": 101}):
        with pytest.raises(MCPReadError) as failure:
            decode_bound_cursor(
                context, args.model_copy(update={"cursor": cursor, **changed})
            )
        assert failure.value.code == "invalid_cursor"


@pytest.mark.parametrize(
    "change",
    [
        {"issued": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
        {"issued": "future"},
        {"issued": "2026-09-27T00:00:00"},
        {"issued": None},
        {"position": []},
        {"v": 9},
        {"binding": "different-credential"},
    ],
)
def test_signed_but_obsolete_cursor_shapes_are_rejected(change):
    if change.get("issued") == "future":
        change = {
            "issued": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        }
    context = _context()
    args = ArticleEvidenceArguments(item_id=uuid.uuid4())
    cursor = encode_bound_cursor(context, args, {"offset": 0, "revision": "x"})
    with pytest.raises(MCPReadError) as failure:
        decode_bound_cursor(
            context,
            args.model_copy(update={"cursor": _rewrite(context, cursor, **change)}),
        )
    assert failure.value.code == "invalid_cursor"


@pytest.mark.parametrize("cursor", ["", "%%%", "a", "界", "not-signed"])
def test_malformed_evidence_cursor_is_a_stable_client_error(cursor):
    with pytest.raises(MCPReadError) as failure:
        decode_bound_cursor(
            _context(), ArticleEvidenceArguments(item_id=uuid.uuid4(), cursor=cursor)
        )
    assert failure.value.code == "invalid_cursor"


def test_missing_cursor_key_never_encodes_or_accepts_a_continuation():
    context = _context()
    args = ArticleEvidenceArguments(item_id=uuid.uuid4())
    cursor = encode_bound_cursor(context, args, {"offset": 0})
    weak = replace(context, cursor_secret=b"short")
    with pytest.raises(MCPReadError) as failure:
        encode_bound_cursor(weak, args, {"offset": 0})
    assert failure.value.code == "mcp_unavailable"
    with pytest.raises(MCPReadError) as failure:
        decode_bound_cursor(weak, args.model_copy(update={"cursor": cursor}))
    assert failure.value.code == "invalid_cursor"


def test_unshrinkable_result_metadata_fails_instead_of_exceeding_wire_limit():
    result = MCPReadResult(
        data={},
        provenance={},
        freshness={},
        canonical_link="https://example.test/" + "x" * 3000,
    )
    with pytest.raises(MCPReadError) as failure:
        bounded_result(result, max_bytes=2048)
    assert failure.value.code == "response_too_large"
    for size in (2047, 65537):
        with pytest.raises(ValueError):
            bounded_result(result, max_bytes=size)
