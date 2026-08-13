import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from pymisp import MISPEvent

from app.schemas.exports import ArticleExportOptions
from app.services.export_models import ExportRecord

MISP_IOC_TYPES = {
    "ipv4": "ip-dst",
    "domain": "domain",
    "hash_md5": "md5",
    "hash_sha1": "sha1",
    "hash_sha256": "sha256",
    "cve": "vulnerability",
    "vendor": "target-org",
    "program": "text",
}


def build_misp_export(
    records: Iterable[ExportRecord],
    *,
    options: ArticleExportOptions,
) -> dict[str, Any]:
    events = [
        {"Event": json.loads(build_misp_event(record, options=options).to_json())}
        for record in records
    ]
    return {"response": events}


def build_misp_event(record: ExportRecord, *, options: ArticleExportOptions) -> MISPEvent:
    event = MISPEvent(strict_validation=True)
    event.info = record.title[:256] or "Untitled ThreatLens article"
    event.distribution = options.misp_distribution
    event.threat_level_id = _misp_threat_level(record)
    event.analysis = 0
    event.published = False
    event.date = _utc_date(record.published_at or record.first_seen_at)
    if record.url:
        event.add_attribute(
            "link",
            record.url,
            comment=f"Source article from {record.feed_name}"[:255],
            to_ids=False,
        )

    if options.include_iocs:
        for ioc in record.iocs:
            misp_type = MISP_IOC_TYPES.get(ioc.type)
            if misp_type is None:
                continue
            event.add_attribute(
                misp_type,
                ioc.value,
                comment=f"ThreatLens extraction: {ioc.source_section}"[:255],
                to_ids=ioc.type not in {"vendor", "program"},
            )

    for tag in record.tags:
        if tag.name.strip():
            event.add_tag(tag.name[:255])

    report_content = _build_event_report(record, options=options)
    if report_content:
        event.add_event_report("ThreatLens article context", report_content)
    return event


def _misp_threat_level(record: ExportRecord) -> int:
    if record.ai is None:
        return 4
    return {"high": 1, "medium": 2, "low": 3}.get(record.ai.relevance_label or "", 4)


def _build_event_report(record: ExportRecord, *, options: ArticleExportOptions) -> str:
    parts = [f"# {record.title}", f"Source: {record.url}", f"Feed: {record.feed_name}"]
    if options.include_ai_details and record.ai and record.ai.summary:
        parts.extend(["## AI summary", record.ai.summary])
    if record.summary:
        parts.extend(["## Source summary", record.summary])
    if options.include_article_text and record.article and record.article.text:
        parts.extend(["## Extracted article text", record.article.text])
    if options.include_user_state:
        parts.extend(
            [
                "## User state",
                f"Read: {'yes' if record.state.is_read else 'no'}",
                f"Starred: {'yes' if record.state.is_starred else 'no'}",
            ]
        )
        if options.include_user_notes and record.state.note:
            parts.extend(["### Note", record.state.note])
    return "\n\n".join(parts)


def _utc_date(value: datetime):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()
