from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, require_token_scopes
from app.core.token_scopes import SCOPE_READ_TAGS, SCOPE_WRITE_TAGS
from app.db.session import get_db
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.models.tagging_rule import TaggingRule
from app.models.user import User
from app.schemas.tagging import (
    TaggingReapplyRequest,
    TaggingReapplyResponse,
    TaggingRulePreviewItem,
    TaggingRulePreviewRequest,
    TaggingRulePreviewResponse,
    TaggingRuleResponse,
    TaggingRuleWrite,
    TaggingSettingsBundleResponse,
    TaggingSettingsResponse,
    TaggingSettingsUpdate,
)
from app.services.algorithm_tags import evaluate_tagging_rule_match, normalize_tag_name
from app.services.audit import record_audit
from app.services.tagging_config import (
    apply_tagging_settings_update,
    get_or_create_tagging_settings,
    list_tagging_rules,
    tagging_rule_response_from_model,
    tagging_settings_response_from_model,
)
from app.tasks.feed_tasks import reapply_recent_item_tags

router = APIRouter(prefix="/tagging", tags=["tagging"])


@router.get("/settings", response_model=TaggingSettingsBundleResponse)
def get_tagging_settings_bundle(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_TAGS)),
):
    settings = get_or_create_tagging_settings(db)
    rules = list_tagging_rules(db)
    return TaggingSettingsBundleResponse(
        settings=tagging_settings_response_from_model(settings),
        rules=[tagging_rule_response_from_model(rule) for rule in rules],
    )


@router.put("/settings", response_model=TaggingSettingsResponse)
def update_tagging_settings(
    payload: TaggingSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_TAGS)),
):
    settings = get_or_create_tagging_settings(db)
    apply_tagging_settings_update(settings, payload)
    db.add(settings)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="tagging.settings.update",
        resource_type="tagging_settings",
        resource_id=str(settings.id),
        metadata={
            "enabled_categories": payload.enabled_categories,
            "min_auto_tag_confidence": payload.min_auto_tag_confidence,
            "secondary_tag_limit": payload.secondary_tag_limit,
        },
    )
    db.commit()
    db.refresh(settings)
    return tagging_settings_response_from_model(settings)


@router.post("/rules", response_model=TaggingRuleResponse, status_code=status.HTTP_201_CREATED)
def create_tagging_rule(
    payload: TaggingRuleWrite,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_TAGS)),
):
    normalized_tag_name = _validate_rule_payload(db, payload)
    rule = TaggingRule(
        name=payload.name,
        tag_name=normalized_tag_name,
        enabled=payload.enabled,
        match_type=payload.match_type,
        pattern=payload.pattern,
        case_sensitive=payload.case_sensitive,
        applies_to_json=list(payload.applies_to),
        required_categories_json=list(payload.required_categories),
        feed_scope=payload.feed_scope,
        feed_ids_json=[str(feed_id) for feed_id in payload.feed_ids],
        min_classification_confidence=payload.min_classification_confidence,
    )
    db.add(rule)
    _ensure_tag_exists(db, normalized_tag_name)
    db.flush()
    record_audit(
        db,
        actor_user_id=admin.id,
        action="tagging.rule.create",
        resource_type="tagging_rule",
        resource_id=str(rule.id),
        metadata={"name": rule.name, "tag_name": rule.tag_name},
    )
    db.commit()
    db.refresh(rule)
    return tagging_rule_response_from_model(rule)


@router.patch("/rules/{rule_id}", response_model=TaggingRuleResponse)
def update_tagging_rule(
    rule_id: uuid.UUID,
    payload: TaggingRuleWrite,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_TAGS)),
):
    rule = db.scalar(select(TaggingRule).where(TaggingRule.id == rule_id))
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tagging rule not found")

    normalized_tag_name = _validate_rule_payload(db, payload)
    rule.name = payload.name
    rule.tag_name = normalized_tag_name
    rule.enabled = payload.enabled
    rule.match_type = payload.match_type
    rule.pattern = payload.pattern
    rule.case_sensitive = payload.case_sensitive
    rule.applies_to_json = list(payload.applies_to)
    rule.required_categories_json = list(payload.required_categories)
    rule.feed_scope = payload.feed_scope
    rule.feed_ids_json = [str(feed_id) for feed_id in payload.feed_ids]
    rule.min_classification_confidence = payload.min_classification_confidence
    db.add(rule)
    _ensure_tag_exists(db, normalized_tag_name)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="tagging.rule.update",
        resource_type="tagging_rule",
        resource_id=str(rule.id),
        metadata={"name": rule.name, "tag_name": rule.tag_name},
    )
    db.commit()
    db.refresh(rule)
    return tagging_rule_response_from_model(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tagging_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_TAGS)),
):
    rule = db.scalar(select(TaggingRule).where(TaggingRule.id == rule_id))
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tagging rule not found")

    db.delete(rule)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="tagging.rule.delete",
        resource_type="tagging_rule",
        resource_id=str(rule_id),
        metadata={"tag_name": rule.tag_name},
    )
    db.commit()


@router.post("/rules/preview", response_model=TaggingRulePreviewResponse)
def preview_tagging_rule(
    payload: TaggingRulePreviewRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_TAGS)),
):
    _validate_rule_payload(db, payload)
    return _build_rule_preview_response(db, payload)


@router.post("/reapply", response_model=TaggingReapplyResponse)
def queue_tagging_reapply(
    payload: TaggingReapplyRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_TAGS)),
):
    task = reapply_recent_item_tags.delay(payload.days, payload.limit)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="tagging.reapply.queue",
        resource_type="tagging_settings",
        metadata={"days": payload.days, "limit": payload.limit, "task_id": task.id},
    )
    db.commit()
    return TaggingReapplyResponse(task_id=task.id, queued=True)


def _validate_rule_payload(db: Session, payload: TaggingRuleWrite) -> str:
    normalized_tag_name = normalize_tag_name(payload.tag_name)
    if not normalized_tag_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="tag_name is invalid")

    available_feed_ids = set(db.scalars(select(Feed.id)).all())
    invalid_feed_ids = [str(feed_id) for feed_id in payload.feed_ids if feed_id not in available_feed_ids]
    if invalid_feed_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown feed ids: {', '.join(sorted(invalid_feed_ids))}",
        )

    if payload.match_type == "regex":
        try:
            flags = 0 if payload.case_sensitive else re.IGNORECASE
            re.compile(payload.pattern, flags)
        except re.error as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid regex: {exc}") from exc

    return normalized_tag_name


def _ensure_tag_exists(db: Session, tag_name: str) -> None:
    existing = db.scalar(select(Tag).where(Tag.name == tag_name))
    if existing is not None:
        return
    db.add(Tag(name=tag_name))


def _build_rule_preview_response(db: Session, payload: TaggingRulePreviewRequest) -> TaggingRulePreviewResponse:
    rows = db.execute(
        select(
            Item,
            Feed.name.label("feed_name"),
            Article.text.label("article_text"),
            ItemClassification.primary_category.label("primary_category"),
            ItemClassification.secondary_categories.label("secondary_categories"),
            ItemClassification.confidence.label("confidence"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(Article, Article.item_id == Item.id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .order_by(Item.first_seen_at.desc())
    ).all()

    matched: list[tuple[object, list[str]]] = []
    matched_item_ids: list[uuid.UUID] = []
    total = 0
    for row in rows:
        matched_sections = evaluate_tagging_rule_match(
            rule=payload,
            title=row.Item.title,
            summary=row.Item.summary,
            article_text=row.article_text,
            feed_name=row.feed_name,
            feed_id=row.Item.feed_id,
            primary_category=row.primary_category or "",
            secondary_categories=row.secondary_categories or [],
            classification_confidence=row.confidence,
        )
        if not matched_sections:
            continue

        total += 1
        if len(matched) >= payload.limit:
            continue
        matched.append((row, matched_sections))
        matched_item_ids.append(row.Item.id)

    current_tags_by_item = _load_tags_for_items(db, matched_item_ids)
    return TaggingRulePreviewResponse(
        total=total,
        items=[
            TaggingRulePreviewItem(
                id=row.Item.id,
                title=row.Item.title,
                feed_name=row.feed_name,
                classification=row.primary_category,
                first_seen_at=row.Item.first_seen_at,
                current_tags=current_tags_by_item.get(row.Item.id, []),
                matched_sections=matched_sections,
            )
            for row, matched_sections in matched
        ],
    )


def _load_tags_for_items(db: Session, item_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not item_ids:
        return {}

    tags_by_item: dict[uuid.UUID, list[str]] = {item_id: [] for item_id in item_ids}
    rows = db.execute(
        select(ItemTag.item_id, Tag.name)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(ItemTag.item_id.in_(item_ids))
        .order_by(Tag.name.asc())
    ).all()
    for item_id, tag_name in rows:
        tags_by_item[item_id].append(tag_name)
    return tags_by_item
