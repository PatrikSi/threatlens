from app.services.extraction import extract_canonical_url, extract_readable_text


HTML = """
<html>
  <head>
    <title>Example</title>
    <link rel=\"canonical\" href=\"https://example.com/canonical\" />
  </head>
  <body>
    <article>
      <h1>Headline</h1>
      <p>This is a test article with meaningful body text.</p>
    </article>
  </body>
</html>
"""


def test_extract_canonical_url():
    assert extract_canonical_url(HTML) == "https://example.com/canonical"


def test_extract_readable_text_returns_payload():
    result = extract_readable_text(HTML)
    assert result["method"] in {"trafilatura", "readability", "none"}
    assert "word_count" in result
