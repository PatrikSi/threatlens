import csv
import io
from datetime import datetime, timezone
from typing import Any

from app.schemas.exports import ArticleExportFilters, ArticleExportOptions
from app.services.export_models import ExportIOC, ExportRecord, ExportTag

EXPORT_SCHEMA_VERSION = 1
CSV_FIELDS = (
    "id",
    "title",
    "url",
    "canonical_url",
    "feed_id",
    "feed_name",
    "published_at",
    "first_seen_at",
    "status",
    "tags",
    "classification",
    "classification_confidence",
    "ai_relevance_score",
    "ai_relevance_label",
    "summary",
    "source_summary",
    "ai_summary",
    "full_article_text",
    "ioc_count",
    "iocs",
    "is_read",
    "is_starred",
    "note",
)
IOC_CSV_FIELDS = (
    "article_id",
    "article_title",
    "article_url",
    "type",
    "value",
    "source_section",
    "occurrences",
    "confidence",
    "first_seen_at",
    "last_seen_at",
)


def record_to_document(record: ExportRecord, *, options: ArticleExportOptions) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "id": str(record.id),
        "title": record.title,
        "url": record.url,
        "canonical_url": record.canonical_url,
        "source_guid": record.source_guid,
        "published_at": isoformat_or_none(record.published_at),
        "first_seen_at": isoformat_or_none(record.first_seen_at),
        "status": record.status,
        "summary": record.summary,
        "feed": {"id": str(record.feed_id), "name": record.feed_name},
        "classification": _classification_document(record),
        "tags": _tag_documents(record.tags, detailed=options.include_tag_metadata),
    }
    if options.include_ai_details:
        document["ai"] = _ai_document(record)
    if record.article is not None:
        document["article"] = {
            "final_url": record.article.final_url,
            "retrieved_at": isoformat_or_none(record.article.retrieved_at),
            "http_status": record.article.http_status,
            "content_type": record.article.content_type,
            "title": record.article.title,
            "text": record.article.text if options.include_article_text else None,
            "text_included": options.include_article_text and record.article.text is not None,
            "extraction_method": record.article.extraction_method,
            "language": record.article.language,
            "word_count": record.article.word_count,
            "error": record.article.error,
        }
    else:
        document["article"] = None
    if options.include_iocs:
        document["iocs"] = [_ioc_document(ioc) for ioc in record.iocs]
    if options.include_user_state:
        document["user_state"] = {
            "is_read": record.state.is_read,
            "is_starred": record.state.is_starred,
            "note": record.state.note if options.include_user_notes else None,
            "updated_at": isoformat_or_none(record.state.updated_at),
        }
    return document


def build_manifest(
    *,
    item_count: int,
    exported_at: datetime,
    filters: ArticleExportFilters,
    options: ArticleExportOptions,
    files: list[str],
    export_format: str,
) -> dict[str, Any]:
    return {
        "schema": "threatlens-export",
        "schema_version": EXPORT_SCHEMA_VERSION,
        "format": export_format,
        "exported_at": isoformat_or_none(exported_at),
        "article_count": item_count,
        "files": files,
        "filters": filters.model_dump(mode="json"),
        "options": options.model_dump(mode="json"),
    }


def csv_header_bytes(*, ioc_export: bool = False) -> bytes:
    fields = IOC_CSV_FIELDS if ioc_export else CSV_FIELDS
    return _csv_line(dict.fromkeys(fields, ""), fields=fields, write_header=True)


def record_to_csv_bytes(
    record: ExportRecord,
    *,
    options: ArticleExportOptions,
    include_article_text: bool,
) -> bytes:
    ai_summary = record.ai.summary if options.include_ai_details and record.ai else None
    row: dict[str, object] = {
        "id": str(record.id),
        "title": record.title,
        "url": record.url,
        "canonical_url": record.canonical_url,
        "feed_id": str(record.feed_id),
        "feed_name": record.feed_name,
        "published_at": isoformat_or_none(record.published_at),
        "first_seen_at": isoformat_or_none(record.first_seen_at),
        "status": record.status,
        "tags": "; ".join(tag.name for tag in record.tags),
        "classification": record.classification.primary_category if record.classification else None,
        "classification_confidence": record.classification.confidence if record.classification else None,
        "ai_relevance_score": record.ai.relevance_score if options.include_ai_details and record.ai else None,
        "ai_relevance_label": record.ai.relevance_label if options.include_ai_details and record.ai else None,
        "summary": ai_summary or record.summary,
        "source_summary": record.summary,
        "ai_summary": ai_summary,
        "full_article_text": (
            record.article.text if include_article_text and record.article is not None else None
        ),
        "ioc_count": len(record.iocs) if options.include_iocs else 0,
        "iocs": "; ".join(f"{ioc.type}:{ioc.value}" for ioc in record.iocs) if options.include_iocs else "",
        "is_read": record.state.is_read if options.include_user_state else None,
        "is_starred": record.state.is_starred if options.include_user_state else None,
        "note": record.state.note if options.include_user_notes else None,
    }
    return _csv_line(row, fields=CSV_FIELDS)


def ioc_to_csv_bytes(record: ExportRecord, ioc: ExportIOC) -> bytes:
    row: dict[str, object] = {
        "article_id": str(record.id),
        "article_title": record.title,
        "article_url": record.url,
        "type": ioc.type,
        "value": ioc.value,
        "source_section": ioc.source_section,
        "occurrences": ioc.occurrences,
        "confidence": ioc.confidence,
        "first_seen_at": isoformat_or_none(ioc.first_seen_at),
        "last_seen_at": isoformat_or_none(ioc.last_seen_at),
    }
    return _csv_line(row, fields=IOC_CSV_FIELDS)


def isoformat_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _classification_document(record: ExportRecord) -> dict[str, Any] | None:
    if record.classification is None:
        return None
    return {
        "primary_category": record.classification.primary_category,
        "secondary_categories": record.classification.secondary_categories,
        "confidence": record.classification.confidence,
        "scores": record.classification.scores,
        "matched_terms": record.classification.matched_terms,
        "rules_version": record.classification.rules_version,
        "classified_at": isoformat_or_none(record.classification.classified_at),
    }


def _ai_document(record: ExportRecord) -> dict[str, Any] | None:
    if record.ai is None:
        return None
    return {
        "status": record.ai.status,
        "summary": record.ai.summary,
        "relevance_score": record.ai.relevance_score,
        "relevance_label": record.ai.relevance_label,
        "relevance_reasons": record.ai.relevance_reasons,
        "provider": record.ai.provider,
        "model": record.ai.model,
        "generated_at": isoformat_or_none(record.ai.generated_at),
        "error": record.ai.error,
    }


def _tag_documents(tags: list[ExportTag], *, detailed: bool) -> list[dict[str, Any] | str]:
    if not detailed:
        return [tag.name for tag in tags]
    return [
        {
            "id": str(tag.id),
            "name": tag.name,
            "source": tag.source,
            "confidence": tag.confidence,
            "rules_version": tag.rules_version,
        }
        for tag in tags
    ]


def _ioc_document(ioc: ExportIOC) -> dict[str, Any]:
    return {
        "id": str(ioc.id),
        "type": ioc.type,
        "value": ioc.value,
        "source_section": ioc.source_section,
        "occurrences": ioc.occurrences,
        "confidence": ioc.confidence,
        "first_seen_at": isoformat_or_none(ioc.first_seen_at),
        "last_seen_at": isoformat_or_none(ioc.last_seen_at),
    }


def _csv_line(row: dict[str, object], *, fields: tuple[str, ...], write_header: bool = False) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n")
    if write_header:
        writer.writeheader()
    else:
        writer.writerow({field: _sanitize_csv_value(row.get(field)) for field in fields})
    return output.getvalue().encode("utf-8-sig" if write_header else "utf-8")


def _sanitize_csv_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    first_non_whitespace = text.lstrip()[:1]
    if first_non_whitespace in {"=", "+", "-", "@"}:
        return f"'{text}"
    return text
