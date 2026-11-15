"""URL sanitation supplements the preview's unchanged CSP and sandbox."""

from bs4 import BeautifulSoup
import pytest

from app.services.article_preview import sanitize_article_preview_html


PREVIEW_URL = "https://publisher.example/news/story"


def _sanitize(markup):
    return BeautifulSoup(
        sanitize_article_preview_html(markup, final_url=PREVIEW_URL), "html.parser"
    )


@pytest.mark.parametrize(("tag", "attribute"), [("a", "href"), ("img", "src")])
@pytest.mark.parametrize("value", [
    "java&#9;script:alert(1)",
    "java&#10;script:alert(1)",
    "java&#13;script:alert(1)",
    "&#1;javascript:alert(1)",
    "vb&#9;script:msgbox(1)",
    "&#1;vbscript:msgbox(1)",
])
def test_article_preview_removes_browser_normalized_script_urls(tag, attribute, value):
    document = _sanitize(f'<{tag} {attribute}="{value}">')

    assert document.find(tag).get(attribute) is None


@pytest.mark.parametrize(("tag", "attribute"), [("a", "href"), ("img", "src")])
@pytest.mark.parametrize("value", [
    "http://[",
    "https://[not-an-address]/story",
    "//[",
])
def test_article_preview_drops_malformed_urls_without_rejecting_the_document(
    tag, attribute, value
):
    document = _sanitize(f'<p>Publisher article</p><{tag} {attribute}="{value}">')

    assert document.p.get_text() == "Publisher article"
    assert document.find(tag).get(attribute) is None


@pytest.mark.parametrize(("value", "expected"), [
    ("/next?edition=1", "https://publisher.example/next?edition=1"),
    ("//cdn.example.com/story", "https://cdn.example.com/story"),
    ("http://publisher.example/story", "http://publisher.example/story"),
    ("#evidence", PREVIEW_URL + "#evidence"),
    ("/javascript:notes", "https://publisher.example/javascript:notes"),
    ("mailto:editor@publisher.example", "mailto:editor@publisher.example"),
])
def test_article_preview_keeps_existing_safe_link_semantics(value, expected):
    document = _sanitize(f'<a href="{value}">Read more</a>')

    assert document.a["href"] == expected
    assert document.a["target"] == "_blank"
    assert document.a["rel"] == ["noopener", "noreferrer"]


@pytest.mark.parametrize("value", [
    "data:image/png;base64,AA==",
    "https://publisher.example/image.png",
])
def test_article_preview_keeps_image_urls_for_the_existing_resource_policy(value):
    assert _sanitize(f'<img src="{value}">').img["src"] == value


def test_article_preview_keeps_valid_srcset_and_removes_invalid_url_candidate():
    valid = "/image.png 1x, /large.png 2x"

    assert _sanitize(f'<img srcset="{valid}">').img["srcset"] == valid
    assert _sanitize('<img srcset="/image.png 1x, https://[ 2x">').img.get("srcset") is None
