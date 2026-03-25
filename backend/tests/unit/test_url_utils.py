from app.services.url_utils import is_fetchable_url, normalize_url


def test_normalize_url_removes_tracking_and_sorts_query():
    url = "HTTPS://Example.com:443/path/?utm_source=a&b=2&a=1#frag"
    normalized = normalize_url(url)
    assert normalized == "https://example.com/path?a=1&b=2"


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


def test_is_fetchable_url_blocks_non_http_and_allows_private_hosts_by_default():
    assert not is_fetchable_url("ftp://example.com/file")
    assert is_fetchable_url("http://127.0.0.1/feed.xml")
    assert is_fetchable_url("http://localhost/feed.xml")


def test_is_fetchable_url_can_still_block_private_hosts_when_explicitly_disabled():
    assert not is_fetchable_url("http://127.0.0.1/feed.xml", allow_private_network=False)
