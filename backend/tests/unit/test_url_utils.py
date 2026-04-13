from app.services.url_utils import is_fetchable_url, normalize_feed_url, normalize_url, redact_feed_url


def test_normalize_url_removes_tracking_and_sorts_query():
    url = "HTTPS://Example.com:443/path/?utm_source=a&b=2&a=1#frag"
    normalized = normalize_url(url)
    assert normalized == "https://example.com/path?a=1&b=2"


def test_normalize_feed_url_preserves_query_parameters():
    url = "HTTPS://Example.com:443/path/?token=abc123&source=partner#frag"
    normalized = normalize_feed_url(url)
    assert normalized == "https://example.com/path?token=abc123&source=partner"


def test_normalize_feed_url_preserves_userinfo_for_authenticated_feeds():
    url = "https://alice:secret@example.com:443/path/feed.xml?token=abc123#frag"
    normalized = normalize_feed_url(url)
    assert normalized == "https://alice:secret@example.com/path/feed.xml?token=abc123"


def test_redact_feed_url_hides_credentials_and_sensitive_query_values():
    url = "https://alice:secret@example.com:443/path/feed.xml?token=abc123&source=partner&api_key=xyz"
    redacted = redact_feed_url(url)
    assert redacted == "https://example.com/path/feed.xml?token=REDACTED&source=partner&api_key=REDACTED"


def test_redact_feed_url_leaves_non_urls_unchanged():
    assert redact_feed_url("Legacy Feed") == "Legacy Feed"


def test_normalize_url_handles_default_and_empty_path():
    url = "http://Example.com"
    assert normalize_url(url) == "http://example.com/"


def test_normalize_url_returns_empty_for_invalid_port():
    assert normalize_url("https://example.com:99999/path") == ""
    assert normalize_url("https://example.com:notaport/path") == ""


def test_normalize_url_returns_empty_for_relative_or_hostless_url():
    assert normalize_url("/relative/path") == ""
    assert normalize_url("mailto:user@example.com") == ""


def test_is_fetchable_url_allows_public_http_urls():
    assert is_fetchable_url("https://example.com/feed.xml")


def test_is_fetchable_url_blocks_non_http_and_private_hosts_by_default():
    assert not is_fetchable_url("ftp://example.com/file")
    assert not is_fetchable_url("http://127.0.0.1/feed.xml")
    assert not is_fetchable_url("http://localhost/feed.xml")


def test_is_fetchable_url_can_still_allow_private_hosts_when_explicitly_enabled():
    assert is_fetchable_url("http://127.0.0.1/feed.xml", allow_private_network=True)
