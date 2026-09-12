"""Hash exact retained report evidence without loading complete excerpt bodies."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem


def report_revision_hash(db: Session, report: Report) -> str:
    """Include published content and source snapshots, excluding mutable FK links.

    Item retention can clear source.item_id without changing the historical
    evidence. Stable source-row IDs and every evidence snapshot field are pinned.
    PostgreSQL hashes large strings before returning the bounded projection.
    """
    db.flush()
    digest = hashlib.sha256()

    def include(value: object) -> None:
        encoded = json.dumps(
            value, default=str, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    include(
        {
            name: getattr(report, name)
            for name in (
                "id",
                "title",
                "report_type",
                "owner_user_id",
                "period_start",
                "period_end",
                "filters_json",
                "prompt_config_json",
                "sections_config_json",
                "metrics_json",
                "coverage_json",
                "summary_text",
                "source_count",
                "included_source_count",
                "excluded_source_count",
                "citation_count",
                "generated_at",
                "provider",
                "model",
                "delivery_requested",
                "delivery_mode",
            )
        }
    )

    def text_hash(column):
        return func.encode(
            func.sha256(func.convert_to(func.coalesce(column, ""), "UTF8")), "hex"
        )

    sections = db.execute(
        select(
            ReportSection.section_key,
            ReportSection.title,
            ReportSection.position,
            ReportSection.status,
            text_hash(ReportSection.body_markdown),
            ReportSection.key_points_json,
            ReportSection.citations_json,
        )
        .where(ReportSection.report_id == report.id)
        .order_by(ReportSection.position, ReportSection.id)
    )
    for section in sections:
        include(tuple(section))
    sources = db.execute(
        select(
            ReportSourceItem.id,
            ReportSourceItem.citation_key,
            ReportSourceItem.included,
            ReportSourceItem.rank,
            ReportSourceItem.exclusion_reason,
            text_hash(ReportSourceItem.title_snapshot),
            text_hash(ReportSourceItem.feed_name_snapshot),
            text_hash(ReportSourceItem.url_snapshot),
            ReportSourceItem.classification_snapshot,
            ReportSourceItem.relevance_score_snapshot,
            ReportSourceItem.relevance_label_snapshot,
            ReportSourceItem.published_at_snapshot,
            ReportSourceItem.first_seen_at_snapshot,
            ReportSourceItem.tags_snapshot_json,
            ReportSourceItem.iocs_snapshot_json,
            text_hash(ReportSourceItem.evidence_text),
            ReportSourceItem.estimated_tokens,
        )
        .where(ReportSourceItem.report_id == report.id)
        .order_by(ReportSourceItem.rank, ReportSourceItem.id)
    )
    for source in sources:
        include(tuple(source))
    return digest.hexdigest()
