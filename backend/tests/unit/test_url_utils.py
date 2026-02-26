from app.services.url_utils import normalize_url


def test_normalize_url_removes_tracking_and_sorts_query():
    url = "HTTPS://Example.com:443/path/?utm_source=a&b=2&a=1#frag"
    normalized = normalize_url(url)
    assert normalized == "https://example.com/path?a=1&b=2"


def test_normalize_url_handles_default_and_empty_path():
    url = "http://Example.com"
    assert normalize_url(url) == "http://example.com/"
