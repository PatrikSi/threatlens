from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tag import ItemTag, Tag
from app.services.classification import CLASSIFICATION_CATEGORIES

TAGGING_RULES_VERSION = "tagging_v2"
ALGORITHM_TAG_NAMES = {name.lower() for name in CLASSIFICATION_CATEGORIES}
AUTO_TAG_SOURCES = {"rule", "ioc", "ml"}
MIN_AUTO_TAG_CONFIDENCE = 0.45

VALID_TAG_CHARS_RE = re.compile(r"[^a-z0-9:_-]+")


@dataclass(frozen=True)
class TagCandidate:
    name: str
    source: str
    confidence: float
    rules_version: str = TAGGING_RULES_VERSION


def normalize_algorithm_tag_names(primary_category: str, secondary_categories: list[str] | None) -> list[str]:
    desired: set[str] = set()
    for raw in [primary_category, *(secondary_categories or [])]:
        value = normalize_tag_name(raw)
        if not value:
            continue
        if value in ALGORITHM_TAG_NAMES:
            desired.add(value)
    return sorted(desired)


def sync_item_algorithm_tags(
    db: Session,
    *,
    item_id: uuid.UUID,
    primary_category: str,
    secondary_categories: list[str] | None,
    classification_confidence: float | None = None,
    ioc_values_by_type: dict[str, list[str]] | None = None,
    title: str = "",
    summary: str | None = None,
    article_text: str | None = None,
    feed_name: str | None = None,
    feed_url: str | None = None,
    feedback_adjustments: dict[str, float] | None = None,
    min_auto_tag_confidence: float = MIN_AUTO_TAG_CONFIDENCE,
) -> list[str]:
    candidates = build_tag_candidates(
        primary_category=primary_category,
        secondary_categories=secondary_categories,
        classification_confidence=classification_confidence,
        ioc_values_by_type=ioc_values_by_type,
        title=title,
        summary=summary,
        article_text=article_text,
        feed_name=feed_name,
        feed_url=feed_url,
        feedback_adjustments=feedback_adjustments,
    )
    desired = [candidate for candidate in candidates if candidate.confidence >= min_auto_tag_confidence]
    desired_by_name = {candidate.name: candidate for candidate in desired}
    desired_names = sorted(desired_by_name.keys())

    existing_auto_links = db.execute(
        select(ItemTag, Tag.name)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(
            and_(
                ItemTag.item_id == item_id,
                ItemTag.source.in_(sorted(AUTO_TAG_SOURCES)),
            )
        )
    ).all()

    stale_tag_ids = [
        item_tag.tag_id
        for item_tag, tag_name in existing_auto_links
        if tag_name not in desired_by_name
    ]
    if stale_tag_ids:
        db.query(ItemTag).filter(
            and_(
                ItemTag.item_id == item_id,
                ItemTag.tag_id.in_(stale_tag_ids),
                ItemTag.source.in_(sorted(AUTO_TAG_SOURCES)),
            )
        ).delete(synchronize_session=False)

    if not desired:
        return []

    existing_tags = db.scalars(select(Tag).where(Tag.name.in_(desired_names))).all()
    tags_by_name: dict[str, Tag] = {tag.name: tag for tag in existing_tags}
    for name in desired_names:
        if name in tags_by_name:
            continue
        tags_by_name[name] = _get_or_create_tag(db, name)

    for candidate in desired:
        tag = tags_by_name[candidate.name]
        link = db.scalar(
            select(ItemTag).where(
                and_(
                    ItemTag.item_id == item_id,
                    ItemTag.tag_id == tag.id,
                )
            )
        )
        if link is None:
            db.add(
                ItemTag(
                    item_id=item_id,
                    tag_id=tag.id,
                    source=candidate.source,
                    confidence=round(candidate.confidence, 3),
                    rules_version=candidate.rules_version,
                )
            )
            continue

        # Manual labels remain analyst-owned and are not overwritten by auto tagging.
        if link.source == "manual":
            continue

        link.source = candidate.source
        link.confidence = round(candidate.confidence, 3)
        link.rules_version = candidate.rules_version
        db.add(link)

    return desired_names


def build_tag_candidates(
    *,
    primary_category: str,
    secondary_categories: list[str] | None,
    classification_confidence: float | None,
    ioc_values_by_type: dict[str, list[str]] | None,
    title: str,
    summary: str | None,
    article_text: str | None,
    feed_name: str | None,
    feed_url: str | None,
    feedback_adjustments: dict[str, float] | None,
) -> list[TagCandidate]:
    feedback_adjustments = feedback_adjustments or {}
    candidates: dict[str, TagCandidate] = {}

    def add_candidate(raw_name: str, base_confidence: float):
        name = normalize_tag_name(raw_name)
        if not name or name not in ALGORITHM_TAG_NAMES:
            return
        confidence = _clamp(base_confidence + feedback_adjustments.get(name, 0.0), 0.05, 0.995)
        existing = candidates.get(name)
        if existing is None or confidence > existing.confidence:
            candidates[name] = TagCandidate(
                name=name,
                source="rule",
                confidence=confidence,
            )

    normalized_primary = normalize_tag_name(primary_category)
    if normalized_primary:
        primary_confidence = max(0.55, float(classification_confidence or 0.55))
        add_candidate(normalized_primary, primary_confidence)

    for category in secondary_categories or []:
        normalized = normalize_tag_name(category)
        if not normalized or normalized == normalized_primary:
            continue
        secondary_confidence = max(0.45, float(classification_confidence or 0.5) * 0.78)
        add_candidate(normalized, secondary_confidence)

    return sorted(candidates.values(), key=lambda candidate: (-candidate.confidence, candidate.name))


def normalize_tag_name(raw: str | None) -> str:
    value = (raw or "").strip().lower().replace(" ", "_")
    if not value:
        return ""
    value = VALID_TAG_CHARS_RE.sub("_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:64]


def _clamp(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _get_or_create_tag(db: Session, tag_name: str) -> Tag:
    tag = db.scalar(select(Tag).where(Tag.name == tag_name))
    if tag is not None:
        return tag

    candidate = Tag(name=tag_name)
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
        return candidate
    except IntegrityError:
        tag = db.scalar(select(Tag).where(Tag.name == tag_name))
        if tag is None:
            raise
        return tag
