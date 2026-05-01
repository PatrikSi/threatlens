import uuid

import pytest

from app.models.article import Article
from app.models.item import Item
from app.services.article_preview import (
    ARTICLE_PREVIEW_CSP,
    resolve_article_preview_url,
    sanitize_article_preview_html,
)


def _item() -> Item:
    return Item(
        id=uuid.uuid4(),
        feed_id=uuid.uuid4(),
        source_guid="item-1",
        url="https://example.com/item",
        canonical_url="https://example.com/canonical",
        title="Item",
        summary="summary",
        dedupe_key="item-1",
        content_hash="a" * 64,
    )


def test_sanitize_article_preview_html_removes_active_content_and_injects_base():
    html = """
    <html>
      <head>
        <base href="https://evil.example/">
        <meta http-equiv="Content-Security-Policy" content="default-src *">
      </head>
      <body onload="steal()">
        <script>alert("x")</script>
        <iframe src="https://tracker.example"></iframe>
        <a href="/article" onclick="steal()">Read more</a>
        <a href="javascript:alert(1)">Bad link</a>
        <img src="/image.png" onerror="steal()">
      </body>
    </html>
    """

    sanitized = sanitize_article_preview_html(html, final_url="https://publisher.example/news/story")

    assert '<base href="https://publisher.example/news/story"/>' in sanitized
    assert "Content-Security-Policy" not in sanitized
    assert "<script" not in sanitized
    assert "<iframe" not in sanitized
    assert "onload" not in sanitized
    assert "onclick" not in sanitized
    assert "onerror" not in sanitized
    assert 'href="https://publisher.example/article"' in sanitized
    assert "javascript:" not in sanitized


def test_article_preview_csp_keeps_scripts_forms_and_nested_frames_disabled():
    assert "script-src 'none'" in ARTICLE_PREVIEW_CSP
    assert "form-action 'none'" in ARTICLE_PREVIEW_CSP
    assert "frame-src 'none'" in ARTICLE_PREVIEW_CSP
    assert "sandbox" in ARTICLE_PREVIEW_CSP
    assert "allow-scripts" not in ARTICLE_PREVIEW_CSP


@pytest.mark.parametrize(
    ("article", "expected"),
    [
        (
            Article(final_url="https://publisher.example/final", http_status=200, content_type="text/html", error=None),
            "https://publisher.example/final",
        ),
        (
            Article(
                final_url="https://publisher.example/final",
                http_status=200,
                content_type="text/html; charset=utf-8",
                error="no_extractor_succeeded",
            ),
            "https://publisher.example/final",
        ),
        (
            Article(final_url="https://publisher.example/final", http_status=200, content_type="application/pdf", error=None),
            "https://example.com/canonical",
        ),
        (
            Article(final_url="https://publisher.example/final", http_status=403, content_type="text/html", error="http_status:403"),
            "https://example.com/canonical",
        ),
        (None, "https://example.com/canonical"),
    ],
)
def test_resolve_article_preview_url_prefers_successful_article_final_url(article, expected):
    assert resolve_article_preview_url(_item(), article) == expected
