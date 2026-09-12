"""The same report citation corpus must pass validation and all three renderers."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.schemas.reports import ReportDetailResponse, ReportSectionResponse, ReportSourceResponse
from app.services.report_grounding import ReportGroundingError, validate_section
from app.services.report_rendering import render_report_html, render_report_pdf

CORPUS = json.loads((Path(__file__).resolve().parents[3] / "tests/fixtures/report-citation-corpus.json").read_text())


def report_fixture(body: str) -> ReportDetailResponse:
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    return ReportDetailResponse.model_construct(
        title="Synthetic citation contract", period_start=now, period_end=now, generated_at=now,
        included_source_count=1, source_count=1, model="Synthetic model", coverage={},
        sections=[ReportSectionResponse.model_construct(key="analysis", title="Analysis", body_markdown=body)],
        sources=[ReportSourceResponse.model_construct(citation_key="S1", included=True,
            title="Synthetic evidence", feed_name="Fixture", url="https://example.test/source")],
    )


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case["name"])
def test_report_citation_acceptance_and_export_anchors(case):
    payload = {"body_markdown": case["body"], "citations": ["S1"]}
    if case["accepted"]:
        result = validate_section(payload, known_citations={"S1"})
        assert result.claim_blocks == case["claim_blocks"]
    else:
        with pytest.raises(ReportGroundingError):
            validate_section(payload, known_citations={"S1"})
    report = report_fixture(case["body"])
    html = BeautifulSoup(render_report_html(report), "html.parser")
    pdf = PdfReader(io.BytesIO(render_report_pdf(report)))
    pdf_citations = [annotation.get_object() for page in pdf.pages for annotation in page.get("/Annots", [])
                     if "/Dest" in annotation.get_object()]
    assert len(html.select('a[href="#report-source-S1"]')) == case["source_links"]
    assert len(pdf_citations) == case["source_links"]
