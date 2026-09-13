"""Grounding decisions must follow visible Markdown and actual source links."""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.schemas.reports import ReportDetailResponse, ReportSectionResponse, ReportSourceResponse
from app.services.report_grounding import ReportGroundingError, validate_section
from app.services.report_rendering import render_report_html, render_report_pdf


def _report(body: str) -> ReportDetailResponse:
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    return ReportDetailResponse.model_construct(
        title="Synthetic grounding check", period_start=now, period_end=now, generated_at=now,
        included_source_count=1, source_count=1, model="Synthetic model", coverage={},
        sections=[ReportSectionResponse.model_construct(
            key="analysis", title="Analysis", body_markdown=body,
        )],
        sources=[ReportSourceResponse.model_construct(
            citation_key="S1", included=True, title="Synthetic evidence", feed_name="Fixture",
            url="https://example.test/source",
        )],
    )


def _artifacts(body: str):
    report = _report(body)
    soup = BeautifulSoup(render_report_html(report), "html.parser")
    pdf = PdfReader(io.BytesIO(render_report_pdf(report)))
    text = " ".join(" ".join(page.extract_text() for page in pdf.pages).split())
    source_links = [
        annotation.get_object()
        for page in pdf.pages for annotation in page.get("/Annots", [])
        if "/Dest" in annotation.get_object()
    ]
    return soup, text, source_links


def _validate(body: str):
    return validate_section({"body_markdown": body, "citations": ["S1"]}, known_citations={"S1"})


@pytest.mark.parametrize("body", [
    "Visible assertion. <!-- Hidden evidence [S1] -->",
    "Visible assertion.\n\n<div>Hidden evidence [S1]</div>",
    "Visible assertion.\n\n<script>Hidden evidence [S1]</script>",
])
def test_hidden_html_cannot_supply_a_visible_claim_citation(body):
    soup, pdf_text, source_links = _artifacts(body)
    assert "Visible assertion." in soup.get_text() and "Visible assertion." in pdf_text
    assert "Hidden evidence" not in soup.get_text() and "Hidden evidence" not in pdf_text
    assert soup.select('a[href="#report-source-S1"]') == [] and source_links == []
    with pytest.raises(ReportGroundingError, match="valid source citation"):
        _validate(body)


@pytest.mark.parametrize("body", [
    "`Uncited textual assertion.`\n\nSupported claim [S1].",
    "- `Uncited textual assertion.`\n- Supported claim [S1].",
    "| Claim | Count |\n| --- | --- |\n| `Uncited textual assertion.` | 3 |\n| Supported claim [S1]. | 4 |",
])
def test_visible_inline_code_is_narrative_even_with_a_cited_claim_elsewhere(body):
    soup, pdf_text, source_links = _artifacts(body)
    assert soup.code.get_text() == "Uncited textual assertion."
    assert "Uncited textual assertion." in pdf_text
    assert len(soup.select('a[href="#report-source-S1"]')) == len(source_links) == 1
    with pytest.raises(ReportGroundingError, match="valid source citation"):
        _validate(body)


@pytest.mark.parametrize("body", [
    "Visible assertion `[S1]`.",
    "[Visible assertion [S1]](https://example.test/analyst).",
    "Visible assertion [reference](https://example.test/[S1]).",
])
def test_code_or_external_link_markers_are_not_source_links(body):
    soup, pdf_text, source_links = _artifacts(body)
    assert "Visible assertion" in soup.get_text() and "Visible assertion" in pdf_text
    assert soup.select('a[href="#report-source-S1"]') == [] and source_links == []
    with pytest.raises(ReportGroundingError, match="valid source citation"):
        _validate(body)


@pytest.mark.parametrize("unknown", ["[S&#50;]", r"\[S2\]", "[S&#x32;]"])
def test_decoded_unknown_markers_are_rejected_in_otherwise_cited_narrative(unknown):
    body = f"Visible assertion [S1] {unknown}."
    soup, pdf_text, source_links = _artifacts(body)
    assert "[S2]" in soup.get_text() and "[S2]" in pdf_text
    assert len(soup.select('a[href="#report-source-S1"]')) == len(source_links) == 1
    with pytest.raises(ReportGroundingError, match="unsupported inline citation"):
        _validate(body)


@pytest.mark.parametrize(("body", "claim_blocks", "source_count"), [
    (r"Visible assertion \[S1\].", 1, 1),
    ("Visible assertion [S&#49;].", 1, 1),
    ("~~Superseded assertion~~ and current evidence [S1].", 1, 1),
    ("`Visible textual assertion.` [S1]", 1, 1),
    ("Visible assertion `[S1]` with real evidence [S1].", 1, 1),
    ("[Analyst assertion [S1]](https://example.test/analyst) with real evidence [S1].", 1, 1),
    ("1. Supported parent [S1].\n   - Supported child [S1].", 2, 2),
    ("| Claim | Count |\n| --- | --- |\n| `Observed behavior` [S1] | 3 |", 1, 1),
])
def test_valid_visible_citations_agree_with_html_and_pdf_source_links(body, claim_blocks, source_count):
    section = _validate(body)
    assert section.claim_blocks == claim_blocks and section.citations == ["S1"]
    soup, _, source_links = _artifacts(body)
    assert len(soup.select('a[href="#report-source-S1"]')) == source_count
    assert len(source_links) == source_count


@pytest.mark.parametrize("point", [
    "Visible point <!-- [S1] -->",
    "`Visible point [S1]`",
    "Visible point [S1] [S&#50;]",
])
def test_key_points_obey_the_same_visible_citation_contract(point):
    with pytest.raises(ReportGroundingError):
        validate_section(
            {"body_markdown": "Supported narrative [S1].", "citations": ["S1"], "key_points": [point]},
            known_citations={"S1"},
        )
