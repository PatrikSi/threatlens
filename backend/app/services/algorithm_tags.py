from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tag import ItemTag, Tag
from app.services.tagging_config import ActiveTaggingSettings, list_enabled_tagging_rules, load_active_tagging_settings
from app.services.classification import CLASSIFICATION_CATEGORIES

TAGGING_RULES_VERSION = "tagging_v2"
ALGORITHM_TAG_NAMES = {name.lower() for name in CLASSIFICATION_CATEGORIES}
AUTO_TAG_SOURCES = {"rule", "ioc", "ml"}
MIN_AUTO_TAG_CONFIDENCE = 0.45

VALID_TAG_CHARS_RE = re.compile(r"[^a-z0-9:_-]+")


class _TaggingRuleLike(Protocol):
    id: uuid.UUID | None
    tag_name: str
    enabled: bool
    match_type: str
    pattern: str
    case_sensitive: bool
    applies_to: list[str] | None
    applies_to_json: list[str] | None
    required_categories: list[str] | None
    required_categories_json: list[str] | None
    feed_scope: str
    feed_ids: list[uuid.UUID] | None
    feed_ids_json: list[str] | None
    min_classification_confidence: float | None


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
    feed_id: uuid.UUID | None = None,
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
    runtime_settings = load_active_tagging_settings(db)
    custom_rules = list_enabled_tagging_rules(db)
    candidates = build_tag_candidates(
        primary_category=primary_category,
        secondary_categories=secondary_categories,
        feed_id=feed_id,
        classification_confidence=classification_confidence,
        ioc_values_by_type=ioc_values_by_type,
        title=title,
        summary=summary,
        article_text=article_text,
        feed_name=feed_name,
        feed_url=feed_url,
        feedback_adjustments=feedback_adjustments,
        active_settings=runtime_settings,
        custom_rules=custom_rules,
    )
    effective_min_confidence = runtime_settings.min_auto_tag_confidence if runtime_settings else min_auto_tag_confidence
    desired = [candidate for candidate in candidates if candidate.confidence >= effective_min_confidence]
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
    feed_id: uuid.UUID | None = None,
    classification_confidence: float | None,
    ioc_values_by_type: dict[str, list[str]] | None,
    title: str,
    summary: str | None,
    article_text: str | None,
    feed_name: str | None,
    feed_url: str | None,
    feedback_adjustments: dict[str, float] | None,
    active_settings: ActiveTaggingSettings | None = None,
    custom_rules: list[_TaggingRuleLike] | None = None,
) -> list[TagCandidate]:
    feedback_adjustments = feedback_adjustments or {}
    candidates: dict[str, TagCandidate] = {}
    enabled_categories = active_settings.enabled_categories if active_settings else ALGORITHM_TAG_NAMES
    secondary_tag_limit = active_settings.secondary_tag_limit if active_settings else 2

    def add_candidate(
        raw_name: str,
        base_confidence: float,
        *,
        allow_any_name: bool = False,
        rules_version: str = TAGGING_RULES_VERSION,
    ):
        name = normalize_tag_name(raw_name)
        if not name:
            return
        if not allow_any_name and name not in enabled_categories:
            return
        confidence = _clamp(base_confidence + feedback_adjustments.get(name, 0.0), 0.05, 0.995)
        existing = candidates.get(name)
        if existing is None or confidence > existing.confidence:
            candidates[name] = TagCandidate(
                name=name,
                source="rule",
                confidence=confidence,
                rules_version=rules_version,
            )

    normalized_primary = normalize_tag_name(primary_category)
    if normalized_primary:
        primary_confidence = max(0.55, float(classification_confidence or 0.55))
        add_candidate(normalized_primary, primary_confidence)

    for category in (secondary_categories or [])[:secondary_tag_limit]:
        normalized = normalize_tag_name(category)
        if not normalized or normalized == normalized_primary:
            continue
        secondary_confidence = max(0.45, float(classification_confidence or 0.5) * 0.78)
        add_candidate(normalized, secondary_confidence)

    for rule in custom_rules or []:
        matched_sections = evaluate_tagging_rule_match(
            rule=rule,
            title=title,
            summary=summary,
            article_text=article_text,
            feed_name=feed_name,
            feed_id=feed_id,
            primary_category=primary_category,
            secondary_categories=secondary_categories,
            classification_confidence=classification_confidence,
        )
        if not matched_sections:
            continue

        rule_id = getattr(rule, "id", None)
        add_candidate(
            rule.tag_name,
            max(0.72, float(classification_confidence or 0.72)),
            allow_any_name=True,
            rules_version=f"custom_rule:{rule_id}" if rule_id else "custom_rule:preview",
        )

    return sorted(candidates.values(), key=lambda candidate: (-candidate.confidence, candidate.name))


def evaluate_tagging_rule_match(
    *,
    rule: _TaggingRuleLike,
    title: str,
    summary: str | None,
    article_text: str | None,
    feed_name: str | None,
    feed_id: uuid.UUID | None,
    primary_category: str,
    secondary_categories: list[str] | None,
    classification_confidence: float | None,
) -> list[str]:
    if not getattr(rule, "enabled", True):
        return []

    feed_scope = getattr(rule, "feed_scope", "all")
    if feed_scope == "selected":
        configured_feed_ids = {
            str(candidate)
            for candidate in (
                getattr(rule, "feed_ids", None)
                or getattr(rule, "feed_ids_json", None)
                or []
            )
        }
        if feed_id is None or str(feed_id) not in configured_feed_ids:
            return []

    minimum_confidence = getattr(rule, "min_classification_confidence", None)
    if minimum_confidence is not None and float(classification_confidence or 0.0) < float(minimum_confidence):
        return []

    required_categories = [
        normalize_tag_name(value)
        for value in (
            getattr(rule, "required_categories", None)
            or getattr(rule, "required_categories_json", None)
            or []
        )
    ]
    available_categories = {
        normalize_tag_name(primary_category),
        *(normalize_tag_name(value) for value in (secondary_categories or [])),
    }
    available_categories.discard("")
    if required_categories and not any(category in available_categories for category in required_categories):
        return []

    applies_to = list(
        getattr(rule, "applies_to", None)
        or getattr(rule, "applies_to_json", None)
        or []
    )
    pattern = getattr(rule, "pattern", "").strip()
    if not applies_to or not pattern:
        return []

    case_sensitive = bool(getattr(rule, "case_sensitive", False))
    match_type = getattr(rule, "match_type", "contains")
    texts = {
        "title": title or "",
        "summary": summary or "",
        "article_text": article_text or "",
        "feed_name": feed_name or "",
    }

    matched_sections: list[str] = []
    if match_type == "contains":
        needle = pattern if case_sensitive else pattern.lower()
        for field_name in applies_to:
            haystack = texts.get(field_name, "")
            comparison = haystack if case_sensitive else haystack.lower()
            if needle and needle in comparison:
                matched_sections.append(field_name)
        return matched_sections

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(pattern, flags)
    except re.error:
        return []

    for field_name in applies_to:
        haystack = texts.get(field_name, "")
        if haystack and compiled.search(haystack):
            matched_sections.append(field_name)

    return matched_sections


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
