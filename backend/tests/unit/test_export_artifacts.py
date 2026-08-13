import csv
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from stix2 import parse

from app.schemas.exports import ArticleExportFilters, ArticleExportOptions
from app.services.export_artifacts import ExportSizeLimitError, generate_export_artifact, remove_export_artifact
from app.services.export_models import (
    ExportAIInsight,
    ExportArticleContent,
    ExportClassification,
    ExportIOC,
    ExportRecord,
    ExportTag,
    ExportUserState,
)


def _record() -> ExportRecord:
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    return ExportRecord(
        id=uuid.uuid4(),
        feed_id=uuid.uuid4(),
        feed_name="Security feed",
        source_guid="source-1",
        url="https://example.com/article",
        canonical_url="https://example.com/article",
        title="=WEBSERVICE formula",
        summary="Source summary",
        published_at=now,
        first_seen_at=now,
        status="content_fetched",
        classification=ExportClassification(
            primary_category="vulnerability",
            secondary_categories=["research"],
            confidence=0.9,
            scores={"vulnerability": 0.9},
            matched_terms={"vulnerability": ["title:cve"]},
            rules_version="v1",
            classified_at=now,
        ),
        ai=ExportAIInsight(
            status="completed",
            summary="AI summary",
            relevance_score=0.94,
            relevance_label="high",
            relevance_reasons=["Matches tracked vulnerability"],
            provider="test",
            model="test-model",
            generated_at=now,
            error=None,
        ),
        article=ExportArticleContent(
            final_url="https://example.com/article",
            retrieved_at=now,
            http_status=200,
            content_type="text/html",
            title="Extracted title",
            text="Readable article body with Žluťoučký text.",
            extraction_method="trafilatura",
            language="en",
            word_count=7,
            error=None,
        ),
        state=ExportUserState(is_read=True, is_starred=True, note="Investigate", updated_at=now),
        tags=[ExportTag(id=uuid.uuid4(), name="vulnerability", source="rule", confidence=0.9, rules_version="v1")],
        iocs=[
            ExportIOC(
                id=uuid.uuid4(),
                type="ipv4",
                value="203.0.113.7",
                source_section="article",
                occurrences=2,
                confidence=1.0,
                first_seen_at=now,
                last_seen_at=now,
            ),
            ExportIOC(
                id=uuid.uuid4(),
                type="cve",
                value="CVE-2026-1234",
                source_section="title",
                occurrences=1,
                confidence=1.0,
                first_seen_at=now,
                last_seen_at=now,
            ),
            ExportIOC(
                id=uuid.uuid4(),
                type="program",
                value="PowerShell",
                source_section="article",
                occurrences=1,
                confidence=0.9,
                first_seen_at=now,
                last_seen_at=now,
            ),
        ],
    )


@pytest.mark.parametrize("export_format", ["csv", "jsonl", "threat_bundle", "stix", "misp", "pdf_bundle"])
def test_generate_export_artifact_formats(export_format: str):
    record = _record()
    artifact = generate_export_artifact(
        iter([record]),
        item_count=1,
        export_format=export_format,
        filters=ArticleExportFilters(),
        options=ArticleExportOptions(include_user_state=True, include_user_notes=True),
        max_uncompressed_bytes=10_000_000,
    )
    try:
        assert artifact.item_count == 1
        assert artifact.file_size > 0
        if export_format == "csv":
            rows = list(csv.DictReader(io.StringIO(artifact.path.read_text(encoding="utf-8-sig"))))
            assert rows[0]["title"].startswith("'=WEBSERVICE")
            assert rows[0]["note"] == "Investigate"
        elif export_format == "jsonl":
            payload = json.loads(artifact.path.read_text(encoding="utf-8"))
            assert payload["article"]["text"].startswith("Readable article")
            assert payload["iocs"][0]["type"] == "ipv4"
            assert payload["user_state"]["note"] == "Investigate"
        elif export_format == "threat_bundle":
            with zipfile.ZipFile(artifact.path) as archive:
                assert set(archive.namelist()) == {"manifest.json", "articles.jsonl", "articles.csv", "iocs.csv"}
                assert json.loads(archive.read("manifest.json"))["article_count"] == 1
        elif export_format == "stix":
            bundle = parse(artifact.path.read_text(encoding="utf-8"), allow_custom=False)
            object_types = {entry.type for entry in bundle.objects}
            assert {"identity", "indicator", "software", "vulnerability", "report"} <= object_types
        elif export_format == "misp":
            payload = json.loads(artifact.path.read_text(encoding="utf-8"))
            event = payload["response"][0]["Event"]
            assert event["published"] is False
            assert event["distribution"] == "0"
            assert {entry["type"] for entry in event["Attribute"]} >= {"link", "ip-dst", "vulnerability"}
        else:
            with zipfile.ZipFile(artifact.path) as archive:
                pdf_name = next(name for name in archive.namelist() if name.endswith(".pdf"))
                assert archive.read(pdf_name).startswith(b"%PDF-")
                assert json.loads(archive.read("manifest.json"))["article_count"] == 1
    finally:
        remove_export_artifact(artifact.path)


def test_export_size_limit_removes_partial_artifact():
    with pytest.raises(ExportSizeLimitError, match="size limit"):
        generate_export_artifact(
            iter([_record()]),
            item_count=1,
            export_format="jsonl",
            filters=ArticleExportFilters(),
            options=ArticleExportOptions(),
            max_uncompressed_bytes=100,
        )


def test_export_options_require_state_before_notes():
    with pytest.raises(ValueError, match="include_user_notes requires include_user_state"):
        ArticleExportOptions(include_user_state=False, include_user_notes=True)


def test_export_filters_reject_reversed_ranges():
    with pytest.raises(ValueError, match="ai_score_min"):
        ArticleExportFilters(ai_score_min=0.8, ai_score_max=0.2)
