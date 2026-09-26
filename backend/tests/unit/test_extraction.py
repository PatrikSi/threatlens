from app.services.extraction import extract_canonical_url, extract_readable_text
from app.services.ioc_extraction import extract_iocs


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


def test_table_and_code_indicators_survive_without_duplicate_prose_or_nested_code():
    prose = "The incident report discusses a campaign and gives supporting indicators below."
    html = f"""<html><body><article><h1>Campaign report</h1><p>{prose}</p>
    <table><tr><th>Type</th><th>Value</th></tr>
    <tr><td>Domain</td><td>table[.]example</td></tr></table>
    <pre><code>hxxps://code[.]example/drop\n203[.]0[.]113[.]7</code></pre>
    </article></body></html>"""
    result = extract_readable_text(html)
    text = result["text"]
    assert text.count(prose) == 1
    assert text.count("table[.]example") == 1
    assert text.count("hxxps://code[.]example/drop") == 1
    values = {(entry.type, entry.value_norm) for entry in extract_iocs(title="", summary=None, article_text=text)}
    assert values >= {("domain", "table.example"), ("url", "https://code.example/drop"), ("ipv4", "203.0.113.7")}


def test_table_only_sources_and_repeated_appendices():
    table = "<table><tr><td>repeated[.]example</td></tr></table>"
    result = extract_readable_text(f"<article>{table}{table}</article>")
    assert result["text"].count("repeated[.]example") == 1


def test_inline_markup_keeps_indicator_spellings_and_explicit_breaks_separate():
    result = extract_readable_text("""<article><table><tr><td>
    <span>one</span><span>[.]</span><span>example</span><br>two[.]example
    </td></tr></table><pre>hxxps://three[.]example/a<br>hxxps://four[.]example/b</pre></article>""")
    values = {(entry.type, entry.value_norm) for entry in extract_iocs(title="", summary=None, article_text=result["text"])}
    assert values >= {("domain", "one.example"), ("domain", "two.example"),
                      ("url", "https://three.example/a"), ("url", "https://four.example/b")}


def test_appendix_limits_are_disclosed_and_do_not_fabricate_partial_indicators(monkeypatch):
    from app.services import article_appendices
    monkeypatch.setattr(article_appendices, "MAX_APPENDIX_BLOCK_CHARS", 20)
    result = extract_readable_text("<article><pre>https://very-long.example/secret</pre></article>")
    assert "truncated" in result["text"]
    assert "https://very-long" not in result["text"]


def test_html_input_limit_is_a_controlled_extraction_error(monkeypatch):
    from app.services import extraction
    monkeypatch.setattr(extraction, "MAX_HTML_CHARS", 10)
    result = extract_readable_text("<article>too large</article>")
    assert result["text"] is None
    assert result["error"] == "article_extraction_size_limit"
