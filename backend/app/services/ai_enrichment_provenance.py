"""Successful result provenance, independent of the latest enrichment attempt.

Reuse is conservative: historical results without proof remain readable, but
downstream synthesis uses primary evidence until a new successful generation.
SQL comparisons never cast untrusted JSON strings into numeric/date values.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import BigInteger, String, and_, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.services.ai_config import ActiveAISettings


STALE_ENRICHMENT_WARNING = (
    "Some AI enrichment was stale or its source version could not be verified; "
    "current publisher text was used instead."
)


def _epoch_microseconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    value = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def enrichment_result_provenance(
    *,
    active: ActiveAISettings,
    item: Item,
    article: Article,
    classification: ItemClassification | None,
    feed_name: str,
    tag_names: list[str],
    source_hash: str,
    generated_at: datetime,
) -> dict:
    tag_input = "".join(f"{len(name)}:{name}" for name in sorted(tag_names))
    return {
        "version": 1,
        "source_hash": source_hash,
        "source_version": int(item.classification_required_version),
        "article_id": str(article.id),
        "article_retrieved_at_us": _epoch_microseconds(article.retrieved_at),
        "classification_hash": classification.source_hash if classification else None,
        "classification_rules_version": classification.rules_version
        if classification
        else None,
        "feed_name": feed_name,
        "item_url": item.canonical_url or item.url,
        "published_at_us": _epoch_microseconds(item.published_at),
        "tag_fingerprint": hashlib.sha256(tag_input.encode("utf-8")).hexdigest(),
        "generated_at": generated_at.isoformat(),
        "provider_id": str(active.provider_id) if active.provider_id else None,
        "provider_version": active.provider_version,
        "model": active.model,
    }


def _sql_microseconds(column):
    return cast(cast(func.extract("epoch", column) * 1_000_000, BigInteger), String)


def refresh_verified_provenance(
    db: Session,
    *,
    enrichment: ItemAIEnrichment,
    provenance: dict,
) -> None:
    """Refresh identical evidence only if the loaded successful row still owns it.

    The caller fences worker ownership first and holds that lock until commit.
    Source changes after the snapshot continue to fail the SQL reuse predicate.
    """
    proof = enrichment.result_provenance_json
    if not isinstance(proof, dict) or proof.get("version") != 1:
        return
    db.execute(
        update(ItemAIEnrichment)
        .where(
            ItemAIEnrichment.item_id == enrichment.item_id,
            ItemAIEnrichment.status == "ready",
            ItemAIEnrichment.source_hash == enrichment.source_hash,
            ItemAIEnrichment.updated_at == enrichment.updated_at,
        )
        .values(result_provenance_json=provenance)
        .execution_options(synchronize_session=False)
    )
    db.expire(enrichment, ["result_provenance_json", "updated_at"])


def current_enrichment_predicate() -> ColumnElement[bool]:
    """Require the joined Item/Article/Feed/classification to match saved inputs."""
    proof = ItemAIEnrichment.result_provenance_json
    tags = (
        select(
            func.encode(
                func.sha256(
                    func.convert_to(
                        func.coalesce(
                            func.string_agg(
                                func.concat(func.char_length(Tag.name), ":", Tag.name),
                                aggregate_order_by(literal(""), Tag.name.collate("C")),
                            ),
                            "",
                        ),
                        "UTF8",
                    )
                ),
                "hex",
            )
        )
        .select_from(ItemTag)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(ItemTag.item_id == Item.id)
        .correlate(Item)
        .scalar_subquery()
    )
    return and_(
        ItemAIEnrichment.status == "ready",
        proof["version"].as_string() == "1",
        proof["source_hash"].as_string() == ItemAIEnrichment.source_hash,
        proof["source_version"].as_string()
        == cast(Item.classification_required_version, String),
        proof["article_id"].as_string() == cast(Article.id, String),
        proof["article_retrieved_at_us"].as_string()
        == _sql_microseconds(Article.retrieved_at),
        proof["classification_hash"]
        .as_string()
        .is_not_distinct_from(ItemClassification.source_hash),
        proof["classification_rules_version"]
        .as_string()
        .is_not_distinct_from(ItemClassification.rules_version),
        proof["feed_name"].as_string() == Feed.name,
        proof["item_url"].as_string()
        == func.coalesce(func.nullif(Item.canonical_url, ""), Item.url),
        proof["published_at_us"]
        .as_string()
        .is_not_distinct_from(_sql_microseconds(Item.published_at)),
        proof["tag_fingerprint"].as_string() == tags,
    )
