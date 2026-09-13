from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_data_access_context, require_permissions
from app.core.config import get_settings
from app.core.logging_config import verbose_logging_enabled
from app.core.token_scopes import SCOPE_READ_TAGGING, SCOPE_WRITE_TAGGING
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
from app.services.algorithm_tags import eligible_tagging_rule_fields, evaluate_tagging_rule_match, normalize_tag_name
from app.services.bounded_regex import ERROR_MESSAGES, MAX_REGEX_TEXT_CHARS, validate_regex
from app.services.audit import record_audit
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyUnavailable,
    handling_label_access_predicate,
)
from app.services.tagging_config import (
    apply_tagging_settings_update,
    get_or_create_tagging_settings,
    list_tagging_rules,
    tagging_rule_response_from_model,
    tagging_settings_response_from_model,
)
from app.services.tagging_recovery import tagging_recovery_summary
from app.tasks.feed_tasks import reapply_recent_item_tags
from app.tasks.feed_tasks import (
    claim_tagging_reapply_dispatch,
    release_tagging_reapply_dispatch,
    CoordinationUnavailableError,
)

router = APIRouter(prefix="/tagging", tags=["tagging"])
logger = logging.getLogger(__name__)

PREVIEW_TITLE_CHARS = 500
PREVIEW_FEED_NAME_CHARS = 255
PREVIEW_TAG_CHARS = 64
PREVIEW_TAGS_PER_ITEM = 25


@router.get("/settings", response_model=TaggingSettingsBundleResponse)
def get_tagging_settings_bundle(
    db: Session = Depends(get_db),
    admin: User = Depends(require_permissions(SCOPE_READ_TAGGING)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    settings = get_or_create_tagging_settings(db)
    rules = list_tagging_rules(db)
    return TaggingSettingsBundleResponse(
        settings=tagging_settings_response_from_model(settings),
        tagging_recovery=tagging_recovery_summary(db, data_access),
        rules=_visible_tagging_rule_responses(
            db,
            rules=rules,
            data_access=data_access,
        ),
    )


@router.put("/settings", response_model=TaggingSettingsResponse)
def update_tagging_settings(
    payload: TaggingSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_permissions(SCOPE_WRITE_TAGGING)),
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


@router.post(
    "/rules", response_model=TaggingRuleResponse, status_code=status.HTTP_201_CREATED
)
def create_tagging_rule(
    payload: TaggingRuleWrite,
    db: Session = Depends(get_db),
    admin: User = Depends(require_permissions(SCOPE_WRITE_TAGGING)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    normalized_tag_name = _validate_rule_payload(db, payload, data_access)
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
    admin: User = Depends(require_permissions(SCOPE_WRITE_TAGGING)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    rule = _get_visible_tagging_rule(
        db,
        rule_id=rule_id,
        data_access=data_access,
    )
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tagging rule not found"
        )

    normalized_tag_name = _validate_rule_payload(db, payload, data_access)
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
    admin: User = Depends(require_permissions(SCOPE_WRITE_TAGGING)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    rule = _get_visible_tagging_rule(
        db,
        rule_id=rule_id,
        data_access=data_access,
    )
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tagging rule not found"
        )

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
    _admin: User = Depends(require_permissions(SCOPE_READ_TAGGING)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    _validate_rule_payload(db, payload, data_access)
    return _build_rule_preview_response(db, payload, data_access)


@router.post("/reapply", response_model=TaggingReapplyResponse)
def queue_tagging_reapply(
    payload: TaggingReapplyRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_permissions(SCOPE_WRITE_TAGGING)),
):
    try:
        dispatch_token = claim_tagging_reapply_dispatch()
    except CoordinationUnavailableError as exc:
        record_audit(
            db,
            actor_user_id=admin.id,
            action="tagging.reapply.queue",
            resource_type="tagging_settings",
            success=False,
            metadata={
                "days": payload.days,
                "limit": payload.limit,
                "error": "coordination_unavailable",
            },
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tagging reapply coordination is temporarily unavailable. Try again later.",
        ) from exc
    if dispatch_token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A tagging reapply run is already queued or in progress",
        )
    try:
        task = reapply_recent_item_tags.delay(
            payload.days, payload.limit, dispatch_token
        )
    except Exception as exc:
        release_tagging_reapply_dispatch(dispatch_token)
        logger.warning(
            "tagging_reapply_enqueue_failed dispatch_id=%s error_type=%s",
            dispatch_token,
            type(exc).__name__,
            exc_info=verbose_logging_enabled(get_settings()),
        )
        record_audit(
            db,
            actor_user_id=admin.id,
            action="tagging.reapply.queue",
            resource_type="tagging_settings",
            success=False,
            metadata={
                "days": payload.days,
                "limit": payload.limit,
                "error": "task_queue_unavailable",
            },
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task queue is temporarily unavailable. Try again later.",
        ) from exc
    celery_task_id = getattr(task, "id", None)
    task_id = str(celery_task_id or dispatch_token)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="tagging.reapply.queue",
        resource_type="tagging_settings",
        metadata={
            "days": payload.days,
            "limit": payload.limit,
            "task_id": task_id,
            "celery_task_id": str(celery_task_id) if celery_task_id else None,
            "dispatch_token": dispatch_token,
        },
    )
    db.commit()
    return TaggingReapplyResponse(
        task_id=task_id,
        queued=True,
        celery_task_id=str(celery_task_id) if celery_task_id else None,
        dispatch_token=dispatch_token,
    )


def _validate_rule_payload(
    db: Session,
    payload: TaggingRuleWrite,
    data_access: DataAccessContext,
) -> str:
    normalized_tag_name = normalize_tag_name(payload.tag_name)
    if not normalized_tag_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="tag_name is invalid",
        )

    requested_feed_ids = set(payload.feed_ids)
    available_feed_ids = _accessible_feed_ids(
        db,
        feed_ids=requested_feed_ids,
        data_access=data_access,
    )
    if requested_feed_ids - available_feed_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="One or more selected feed ids are unknown or inaccessible",
        )

    if payload.match_type == "regex":
        error = validate_regex(payload.pattern, payload.case_sensitive)
        if error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=ERROR_MESSAGES[error],
            )

    return normalized_tag_name


def _accessible_feed_ids(
    db: Session,
    *,
    feed_ids: set[uuid.UUID],
    data_access: DataAccessContext,
) -> set[uuid.UUID]:
    if not feed_ids:
        return set()
    return set(
        db.scalars(
            select(Feed.id).where(
                Feed.id.in_(feed_ids),
                handling_label_access_predicate(
                    Feed.handling_label_id,
                    data_access,
                ),
            )
        ).all()
    )


def _rule_feed_ids(rule: TaggingRule) -> list[uuid.UUID]:
    try:
        return [uuid.UUID(value) for value in (rule.feed_ids_json or [])]
    except (AttributeError, TypeError, ValueError) as exc:
        raise DataPolicyUnavailable(
            "A tagging rule contains invalid feed references. Repair the rule before serving tagging configuration.",
            context={"tagging_rule_id": str(rule.id)},
        ) from exc


def _visible_tagging_rule_responses(
    db: Session,
    *,
    rules: list[TaggingRule],
    data_access: DataAccessContext,
) -> list[TaggingRuleResponse]:
    parsed_feed_ids = {rule.id: _rule_feed_ids(rule) for rule in rules}
    if not data_access.enforced:
        return [tagging_rule_response_from_model(rule) for rule in rules]

    selected_feed_ids = {
        feed_id
        for rule in rules
        if rule.feed_scope == "selected"
        for feed_id in parsed_feed_ids[rule.id]
    }
    accessible_feed_ids = _accessible_feed_ids(
        db,
        feed_ids=selected_feed_ids,
        data_access=data_access,
    )
    responses: list[TaggingRuleResponse] = []
    for rule in rules:
        response = tagging_rule_response_from_model(rule)
        if rule.feed_scope != "selected":
            responses.append(response)
            continue
        visible_ids = [
            feed_id
            for feed_id in parsed_feed_ids[rule.id]
            if feed_id in accessible_feed_ids
        ]
        if visible_ids:
            responses.append(response.model_copy(update={"feed_ids": visible_ids}))
    return responses


def _get_visible_tagging_rule(
    db: Session,
    *,
    rule_id: uuid.UUID,
    data_access: DataAccessContext,
) -> TaggingRule | None:
    rule = db.scalar(select(TaggingRule).where(TaggingRule.id == rule_id))
    if rule is None or not data_access.enforced or rule.feed_scope != "selected":
        return rule
    feed_ids = set(_rule_feed_ids(rule))
    accessible_feed_ids = _accessible_feed_ids(
        db,
        feed_ids=feed_ids,
        data_access=data_access,
    )
    if not feed_ids or not feed_ids.issubset(accessible_feed_ids):
        return None
    return rule


def _ensure_tag_exists(db: Session, tag_name: str) -> None:
    existing = db.scalar(select(Tag).where(Tag.name == tag_name))
    if existing is not None:
        return
    try:
        with db.begin_nested():
            db.add(Tag(name=tag_name))
            db.flush()
    except IntegrityError:
        if db.scalar(select(Tag.id).where(Tag.name == tag_name)) is None:
            raise


def _build_rule_preview_response(
    db: Session,
    payload: TaggingRulePreviewRequest,
    data_access: DataAccessContext,
) -> TaggingRulePreviewResponse:
    feed_access_filter = handling_label_access_predicate(
        Feed.handling_label_id, data_access
    )
    candidate_count = int(db.scalar(select(func.count(Item.id)).join(Feed).where(feed_access_filter)) or 0)
    selected_fields = set(payload.applies_to)
    query = (
        select(
            Item.id, Item.feed_id,
            func.substr(Item.title, 1, (MAX_REGEX_TEXT_CHARS if "title" in selected_fields
                                       else PREVIEW_TITLE_CHARS) + 1).label("title"),
            Item.first_seen_at,
            (func.substr(Item.summary, 1, MAX_REGEX_TEXT_CHARS + 1)
             if "summary" in selected_fields else literal(None)).label("summary"),
            func.substr(Feed.name, 1, (MAX_REGEX_TEXT_CHARS if "feed_name" in selected_fields
                                      else PREVIEW_FEED_NAME_CHARS) + 1).label("feed_name"),
            (func.substr(Article.text, 1, MAX_REGEX_TEXT_CHARS + 1)
             if "article_text" in selected_fields else literal(None)).label("article_text"),
            ItemClassification.primary_category.label("primary_category"),
            ItemClassification.secondary_categories.label("secondary_categories"),
            ItemClassification.confidence.label("confidence"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(Article, Article.item_id == Item.id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .where(feed_access_filter)
        .order_by(Item.first_seen_at.desc(), Item.id.desc())
        .limit(200)
    )
    matched: list[TaggingRulePreviewItem] = []
    total = scanned = 0
    errors: list[str] = []
    deadline = time.monotonic() + 3.0
    rows = db.execute(query, execution_options={"yield_per": 1})
    try:
        for row in rows:
            if time.monotonic() >= deadline:
                break
            scanned += 1
            eligible_fields = eligible_tagging_rule_fields(
                rule=payload, feed_id=row.feed_id, primary_category=row.primary_category or "",
                secondary_categories=row.secondary_categories or [],
                classification_confidence=row.confidence,
            )
            if not eligible_fields:
                continue
            # Contains previews must not silently match a truncated field either.
            if any(len(getattr(row, field) or "") > MAX_REGEX_TEXT_CHARS for field in eligible_fields):
                errors.append("input_too_large")
                break
            matched_sections = evaluate_tagging_rule_match(
                rule=payload, title=row.title, summary=row.summary,
                article_text=row.article_text, feed_name=row.feed_name,
                feed_id=row.feed_id, primary_category=row.primary_category or "",
                secondary_categories=row.secondary_categories or [],
                classification_confidence=row.confidence, errors=errors,
            )
            if errors:
                break
            if matched_sections:
                total += 1
                if len(matched) < payload.limit:
                    # Retain only bounded response metadata, never publisher bodies.
                    matched.append(TaggingRulePreviewItem(
                        id=row.id, title=_preview_display_text(row.title, PREVIEW_TITLE_CHARS),
                        feed_name=_preview_display_text(row.feed_name, PREVIEW_FEED_NAME_CHARS),
                        classification=row.primary_category, first_seen_at=row.first_seen_at,
                        current_tags=[], matched_sections=matched_sections,
                    ))
    finally:
        rows.close()

    current_tags_by_item, truncated_tag_items = _load_tags_for_items(
        db, [item.id for item in matched], data_access=data_access
    )
    for item in matched:
        item.current_tags = current_tags_by_item.get(item.id, [])
        item.current_tags_truncated = item.id in truncated_tag_items
    return TaggingRulePreviewResponse(
        total=total,
        scanned_items=scanned,
        candidate_items=candidate_count,
        complete=scanned == candidate_count and not errors,
        warnings=[ERROR_MESSAGES[code] for code in dict.fromkeys(errors)],
        items=matched,
    )


def _preview_display_text(value: str, limit: int) -> str:
    return value[:limit] + "…" if len(value) > limit else value


def _load_tags_for_items(
    db: Session,
    item_ids: list[uuid.UUID],
    *,
    data_access: DataAccessContext,
) -> tuple[dict[uuid.UUID, list[str]], set[uuid.UUID]]:
    if not item_ids:
        return {}, set()

    tags_by_item: dict[uuid.UUID, list[str]] = {item_id: [] for item_id in item_ids}
    truncated_items: set[uuid.UUID] = set()
    # Bound both row count and characters in SQL before allocating tag strings.
    # One sentinel row per item discloses omitted tags without fetching them.
    ranked_tags = (
        select(
            ItemTag.item_id,
            func.substr(Tag.name, 1, PREVIEW_TAG_CHARS + 1).label("name"),
            func.row_number().over(partition_by=ItemTag.item_id, order_by=Tag.name).label("position"),
        )
        .join(Tag, Tag.id == ItemTag.tag_id)
        .join(Item, Item.id == ItemTag.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            ItemTag.item_id.in_(item_ids),
            handling_label_access_predicate(Feed.handling_label_id, data_access),
        )
        .subquery()
    )
    rows = db.execute(select(ranked_tags).where(ranked_tags.c.position <= PREVIEW_TAGS_PER_ITEM + 1)
                      .order_by(ranked_tags.c.item_id, ranked_tags.c.position))
    for item_id, tag_name, position in rows:
        if position > PREVIEW_TAGS_PER_ITEM or len(tag_name) > PREVIEW_TAG_CHARS:
            truncated_items.add(item_id)
        if position <= PREVIEW_TAGS_PER_ITEM:
            tags_by_item[item_id].append(_preview_display_text(tag_name, PREVIEW_TAG_CHARS))
    return tags_by_item, truncated_items
