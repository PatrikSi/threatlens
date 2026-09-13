from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup
from fastapi import HTTPException
from pypdf import PdfReader

from app.schemas.reports import ReportDetailResponse, ReportSectionResponse, ReportSourceResponse
from app.services import report_markdown, report_pdf
from app.services.report_rendering import render_report_html, render_report_markdown, render_report_pdf


def report_fixture(body: str, *, title: str = "Weekly Δοκιμή — Привет — café") -> ReportDetailResponse:
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    source = ReportSourceResponse(
        citation_key="S1", item_id=uuid.UUID(int=1), included=True, rank=1, exclusion_reason=None,
        title="Source <evidence>", feed_name="Synthetic feed", url="https://example.test/evidence?a=1&b=2",
        classification=None, relevance_score=None, relevance_label=None, published_at=None,
        first_seen_at=now, tags=[], iocs=[], estimated_tokens=50,
    )
    section = ReportSectionResponse(key="analysis", title="Analysis", position=0, status="completed",
                                   body_markdown=body, key_points=[], citations=["S1"], error=None)
    # Exercise the canonical detail schema without involving ORM/DB state.
    return ReportDetailResponse.model_construct(
        title=title, period_start=now, period_end=now, generated_at=now,
        included_source_count=1, source_count=2, model="Synthetic model", coverage={"warnings": ["Partial coverage"]},
        sections=[section], sources=[source],
    )


MARKDOWN = """## Assessment

A **strong** and *emphasized* claim with ~~superseded~~ context [S1]. Unknown [S99].

3. Ordered response
   - Nested evidence [S1]
   - More evidence
4. Final action

> Quoted assessment with [S1].

| Indicator | Assessment |
| --- | --- |
| `example.test` | **Observed** [S1] |
| other.test | [Analyst reference](https://example.test/reference) |

```python
if observed:
    print("[S1] <safe>")
```

[Safe link](https://example.test/path?q=one&next=two)

![Tracking pixel](https://publisher.invalid/pixel.gif)

<script>window.stolen = true</script>

<iframe src="https://publisher.invalid/frame"></iframe>

[Unsafe](javascript:alert%281%29)
"""


def pdf_reader(body: str) -> PdfReader:
    return PdfReader(io.BytesIO(render_report_pdf(report_fixture(body))))


def pdf_text(reader: PdfReader) -> str:
    return "\n".join(page.extract_text() for page in reader.pages)


def test_html_preserves_semantic_markdown_and_known_citations_without_active_content():
    soup = BeautifulSoup(render_report_html(report_fixture(MARKDOWN)), "html.parser")
    assert soup.h1.get_text() == "Weekly Δοκιμή — Привет — café"
    assert soup.h3.get_text() == "Assessment"
    assert soup.select_one("ol[start='3'] > li > ul") is not None
    assert soup.select_one("strong").get_text() == "strong"
    assert soup.select_one("em").get_text() == "emphasized"
    assert soup.select_one("del").get_text() == "superseded"
    assert soup.select_one("blockquote a")['href'] == "#report-source-S1"
    assert [cell.get_text() for cell in soup.select("th[scope='col']")] == ["Indicator", "Assessment"]
    assert soup.select_one("tbody td a")['href'] == "#report-source-S1"
    assert 'print("[S1] <safe>")' in soup.pre.get_text()
    assert soup.pre.find("a") is None
    assert soup.select_one("#report-source-S1").get_text() == "[S1] Source <evidence> — Synthetic feed"
    assert len(soup.select('a[href="#report-source-S1"]')) == 4
    assert "Unknown [S99]" in soup.get_text()
    assert soup.find_all(["script", "iframe", "img", "object", "embed", "form"]) == []
    assert "window.stolen" not in soup.get_text()
    assert "[Image omitted: Tracking pixel]" in soup.get_text()
    assert all(a["href"].startswith(("https://", "#report-source-")) for a in soup.select("a[href]"))
    assert "default-src 'none'" in soup.find("meta", {"http-equiv": "Content-Security-Policy"})["content"]


def test_pdf_preserves_text_styles_unicode_and_safe_link_annotations():
    reader = pdf_reader(MARKDOWN)
    text = pdf_text(reader)
    for value in ["Δοκιμή", "Привет", "café", "Assessment", "Ordered response", "Nested evidence",
                  "Quoted assessment", "Indicator", "example.test", 'print("[S1] <safe>")', "Source <evidence>"]:
        assert value in text
    assert "**strong**" not in text and "## Assessment" not in text and "window.stolen" not in text
    fonts = {str(font.get_object()["/BaseFont"]) for page in reader.pages for font in page["/Resources"]["/Font"].values()}
    assert any("DejaVuSans-Bold" in font for font in fonts)
    assert any("DejaVuSansMono" in font for font in fonts)
    links = [a.get_object() for page in reader.pages for a in page.get("/Annots", [])]
    assert any("/Dest" in link for link in links)
    uris = [str(link["/A"]["/URI"]) for link in links if "/A" in link]
    assert "https://example.test/reference" in uris
    assert "https://example.test/evidence?a=1&b=2" in uris
    assert all(uri.startswith("https://example.test/") for uri in uris)
    assert "/JavaScript" not in reader.trailer["/Root"].get("/Names", {})
    assert all("/XObject" not in page["/Resources"] for page in reader.pages)


def test_pdf_splits_a_single_tall_table_row_and_repeats_header_without_losing_tail():
    long_cell = " ".join(f"evidence-{index}" for index in range(700))
    reader = pdf_reader(f"| Indicator | Assessment |\n| --- | --- |\n| single | {long_cell} TABLE_TAIL |\n")
    assert len(reader.pages) >= 3
    text = pdf_text(reader)
    assert "TABLE_TAIL" in text and "evidence-0" in text and "evidence-699" in text
    assert sum("Indicator" in page.extract_text() for page in reader.pages) >= 3


def test_pdf_wraps_long_code_and_paginates_many_lines_without_losing_tail():
    long_line = "unbroken_" * 300 + "LONG_LINE_TAIL"
    code = "\n".join([long_line, *[f"line_{index} = 'preserve indentation'" for index in range(180)], "CODE_TAIL"])
    reader = pdf_reader(f"```text\n{code}\n```")
    text = pdf_text(reader)
    assert len(reader.pages) >= 3
    assert "LONG_LINE_TAIL" in text.replace("\n", "")
    assert "CODE_TAIL" in text and "line_179" in text


def test_pdf_keeps_wide_table_values_in_labeled_records():
    headings = [f"Header {index}" for index in range(12)]
    body = "|" + "|".join(headings) + "|\n|" + "|".join(["---"] * 12) + "|\n|" + "|".join(f"VALUE_{i}" for i in range(12)) + "|"
    text = pdf_text(pdf_reader(body))
    assert "Wide table displayed as labeled rows." in text
    for i in range(12):
        assert f"Header {i}" in text and f"VALUE_{i}" in text


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "//publisher.invalid/pixel",
                               "https://user:password@example.test/", "https://example.test/\nheader", "https://[invalid/"])
def test_source_links_reject_unsafe_protocols_credentials_and_control_characters(url):
    report = report_fixture("Evidence [S1].")
    report.sources[0].url = url
    soup = BeautifulSoup(render_report_html(report), "html.parser")
    assert soup.select_one("#report-source-S1 a") is None
    reader = PdfReader(io.BytesIO(render_report_pdf(report)))
    links = [a.get_object() for page in reader.pages for a in page.get("/Annots", [])]
    assert all("/A" not in link for link in links)


def test_excluded_unknown_and_code_citations_do_not_create_dangling_pdf_destinations():
    report = report_fixture("Known [S1], unknown [S99], code `[S1]`, [external [S1]](https://example.test/).")
    report.sources[0].included = False
    soup = BeautifulSoup(render_report_html(report), "html.parser")
    assert soup.select('a[href^="#"]') == []
    assert render_report_pdf(report).startswith(b"%PDF")


@pytest.mark.parametrize("render", [render_report_html, render_report_pdf])
def test_structured_exports_reject_oversized_text_but_markdown_remains_available(monkeypatch, render):
    monkeypatch.setattr(report_markdown, "MAX_RENDER_BYTES", 1024)
    report = report_fixture("x" * 1025)
    with pytest.raises(report_markdown.ReportRenderingLimitError, match="text"):
        render(report)
    assert "x" * 1025 in render_report_markdown(report)


def test_structure_and_page_budgets_fail_explicitly(monkeypatch):
    monkeypatch.setattr(report_markdown, "MAX_RENDER_NODES", 10)
    with pytest.raises(report_markdown.ReportRenderingLimitError, match="structure"):
        render_report_html(report_fixture("\n\n".join(["paragraph"] * 20)))
    monkeypatch.setattr(report_markdown, "MAX_RENDER_NODES", 100_000)
    monkeypatch.setattr(report_pdf, "MAX_PDF_PAGES", 1)
    with pytest.raises(report_markdown.ReportRenderingLimitError, match="page"):
        render_report_pdf(report_fixture("\n\n".join(["A substantial paragraph. " * 20] * 100)))


def test_download_route_returns_actionable_413_without_changing_markdown(monkeypatch):
    from app.api.routes import report_route_helpers

    report = report_fixture("x" * 1025)
    monkeypatch.setattr(report_route_helpers, "report_detail_response", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(report_markdown, "MAX_RENDER_BYTES", 1024)
    stored = type("StoredReport", (), {"id": uuid.UUID(int=2)})()
    for format in ("pdf", "html"):
        with pytest.raises(HTTPException) as caught:
            report_route_helpers.render_report_download(None, report=stored, format=format)
        assert caught.value.status_code == 413
        assert "Download Markdown" in caught.value.detail
    response = report_route_helpers.render_report_download(None, report=stored, format="markdown")
    assert response.status_code == 200 and b"x" * 1025 in response.body


def test_pdf_handles_a_table_header_taller_than_one_page():
    header = " ".join(f"header-{i}" for i in range(700))
    reader = pdf_reader(f"| {header} HEADER_TAIL | Status |\n| --- | --- |\n| RECORD_VALUE | reviewed |")
    text = pdf_text(reader)
    assert "HEADER_TAIL" in text and "RECORD_VALUE" in text


def test_pdf_keeps_all_rows_across_table_page_boundaries():
    body = "| Row | Status |\n| --- | --- |\n" + "\n".join(f"| ROW_{i:03} | reviewed [S1] |" for i in range(180))
    reader = pdf_reader(body)
    text = pdf_text(reader)
    assert len(reader.pages) >= 5
    for i in range(180):
        assert f"ROW_{i:03}" in text


def test_pdf_keeps_long_nested_list_content_across_pages():
    nested = " ".join(f"nested-{i}" for i in range(1100))
    reader = pdf_reader(f"1. Parent\n   - {nested} NESTED_TAIL\n2. NEXT_ACTION")
    text = pdf_text(reader)
    assert len(reader.pages) >= 3
    assert "NESTED_TAIL" in text and "NEXT_ACTION" in text


def test_code_line_expansion_counts_toward_structure_budget(monkeypatch):
    monkeypatch.setattr(report_markdown, "MAX_RENDER_NODES", 50)
    with pytest.raises(report_markdown.ReportRenderingLimitError, match="structure"):
        render_report_pdf(report_fixture("```text\n" + "\n" * 100 + "```"))


@pytest.mark.parametrize("status", ["checked", "degraded", "insufficient_evidence"])
def test_export_grounding_notes_describe_structural_checks_without_semantic_overclaim(status):
    report = report_fixture("A sourced claim [S1].")
    report.coverage["grounding"] = {"version": 1, "status": status, "validated_findings": 4,
                                    "cited_claim_blocks": 2, "empty_batches": [1], "semantic_verification": False}
    html_text = BeautifulSoup(render_report_html(report), "html.parser").get_text()
    text = pdf_text(PdfReader(io.BytesIO(render_report_pdf(report))))
    for output in (html_text, text):
        assert "4 findings validated; 2 cited claim blocks checked" in output
        assert "They do not verify whether each claim is true or" in output
        if status == "degraded":
            assert "Source checks are incomplete" in output
        if status == "insufficient_evidence":
            assert "Insufficient source evidence" in output


def test_single_section_limit_rejects_dense_inline_syntax_before_parser_materialization(monkeypatch):
    monkeypatch.setattr(report_markdown, "MAX_SECTION_BYTES", 1024)
    monkeypatch.setattr(report_markdown, "MarkdownIt", lambda *_args, **_kwargs: pytest.fail("oversized input reached parser"))
    with pytest.raises(report_markdown.ReportRenderingLimitError, match="section"):
        render_report_html(report_fixture("*x* " * 300))
