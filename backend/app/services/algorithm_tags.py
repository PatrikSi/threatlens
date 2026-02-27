from __future__ import annotations

import uuid

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tag import ItemTag, Tag
from app.services.classification import CLASSIFICATION_CATEGORIES

ALGORITHM_TAG_NAMES = {name.lower() for name in CLASSIFICATION_CATEGORIES}


def normalize_algorithm_tag_names(primary_category: str, secondary_categories: list[str] | None) -> list[str]:
    desired: set[str] = set()
    for raw in [primary_category, *(secondary_categories or [])]:
        value = (raw or "").strip().lower()
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
) -> list[str]:
    desired_names = normalize_algorithm_tag_names(primary_category, secondary_categories)

    existing_algorithm_links = db.execute(
        select(ItemTag.tag_id, Tag.name)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(
            and_(
                ItemTag.item_id == item_id,
                Tag.name.in_(sorted(ALGORITHM_TAG_NAMES)),
            )
        )
    ).all()

    stale_tag_ids = [tag_id for tag_id, tag_name in existing_algorithm_links if tag_name not in desired_names]
    if stale_tag_ids:
        db.query(ItemTag).filter(ItemTag.item_id == item_id, ItemTag.tag_id.in_(stale_tag_ids)).delete(synchronize_session=False)

    if not desired_names:
        return []

    existing_tags = db.scalars(select(Tag).where(Tag.name.in_(desired_names))).all()
    tags_by_name: dict[str, Tag] = {tag.name: tag for tag in existing_tags}

    for tag_name in desired_names:
        if tag_name in tags_by_name:
            continue
        tag = _get_or_create_tag(db, tag_name)
        tags_by_name[tag_name] = tag

    desired_tag_ids = [tags_by_name[tag_name].id for tag_name in desired_names]
    existing_item_tag_ids = set(
        db.scalars(
            select(ItemTag.tag_id).where(
                and_(
                    ItemTag.item_id == item_id,
                    ItemTag.tag_id.in_(desired_tag_ids),
                )
            )
        ).all()
    )

    for tag_id in desired_tag_ids:
        if tag_id in existing_item_tag_ids:
            continue
        db.add(ItemTag(item_id=item_id, tag_id=tag_id))

    return desired_names


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
