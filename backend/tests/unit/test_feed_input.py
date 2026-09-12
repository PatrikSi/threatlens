import pytest

from app.services.feed_input import FeedInputError, clean_feed_text, safe_feed_cache_header


@pytest.mark.parametrize("value", ["\u00ff", "\x00", "\ud800", "line\r\nbreak", "\x7f", "x" * 8_193, "", None])
def test_unsupported_cache_validators_are_optional(value):
    assert safe_feed_cache_header(value) is None


@pytest.mark.parametrize("value", ['W/"revision-123"', "Mon, 24 Aug 2026 12:00:00 GMT"])
def test_ordinary_cache_validators_are_preserved(value):
    assert safe_feed_cache_header(value) == value


def test_evidence_unicode_is_preserved_and_unsupported_codepoints_rejected():
    assert clean_feed_text("  \u00c5ngstr\u00f6m \U0001f6e1  ") == "\u00c5ngstr\u00f6m \U0001f6e1"
    for invalid in ("a\x00b", "a\ud800b", {"unexpected": "shape"}):
        with pytest.raises(FeedInputError):
            clean_feed_text(invalid)
