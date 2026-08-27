import uuid
from datetime import datetime, timedelta, timezone
from types import ModuleType

from sqlalchemy import select

from app.models.article import Article
from app.models.feed import Feed
from app.models.ioc import ItemIOC
from app.models.item import Item
from app.models.item_classification import ItemClassification


def run_classify_item(item_id: str, *, runtime: ModuleType):
    r = runtime
    integration_event_ids: list[uuid.UUID] = []
    alert_evaluation_request_ids: list[uuid.UUID] = []
    with r.db_session() as db:
        parsed_item_id = _parse_uuid(item_id)
        if parsed_item_id is None:
            return {
                "status": "skipped",
                "reason": "invalid_item_id",
                "item_id": item_id,
            }

        item, claim_reason = r._claim_item_article_processing_target(
            db, item_id=parsed_item_id
        )
        if item is None:
            return {
                "status": "skipped",
                "reason": claim_reason or "not_found",
                "item_id": item_id,
            }

        article = db.scalar(select(Article).where(Article.item_id == parsed_item_id))
        article_freshness_token = r._load_article_freshness_token(
            db, item_id=parsed_item_id
        )
        feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
        feed_name = feed.name if feed is not None else ""
        feed_url = feed.url if feed is not None else ""
        active_ai_settings = r.load_active_ai_settings(db)
        ai_skip_reason = _ai_enrichment_skip_reason(
            item, article, active_ai_settings, runtime=r
        )
        queue_ai_enrichment = ai_skip_reason is None

        result = r.classify_item_content(
            title=item.title,
            summary=item.summary,
            article_text=article.text if article else None,
            feed_name=feed_name,
        )
        if r._article_was_refetched(
            db, item_id=parsed_item_id, expected_token=article_freshness_token
        ):
            r.logger.info(
                "classification_stale_article_discarded item_id=%s", parsed_item_id
            )
            return {
                "status": "skipped",
                "reason": r.ARTICLE_REFRESHED_SKIP_REASON,
                "item_id": item_id,
            }

        row = db.scalar(
            select(ItemClassification).where(
                ItemClassification.item_id == parsed_item_id
            )
        )
        up_to_date = (
            row is not None
            and row.source_hash == result.source_hash
            and row.rules_version == result.rules_version
        )
        if row is None:
            row = ItemClassification(item_id=parsed_item_id)
        if not up_to_date:
            _apply_classification_result(row, result)
            db.add(row)

        _sync_classification_tags(
            db, item, article, feed_name, feed_url, row, runtime=r
        )
        if feed is not None:
            evaluation_intent = r.persist_alert_evaluation_intent(
                db,
                item=item,
                classification=row,
            )
            if evaluation_intent.created:
                alert_evaluation_request_ids.append(evaluation_intent.request_id)
        primary_category = row.primary_category
        db.commit()

    return _complete_classification(
        item_id,
        parsed_item_id,
        primary_category,
        feed_name,
        active_ai_settings,
        ai_skip_reason,
        queue_ai_enrichment,
        integration_event_ids,
        alert_evaluation_request_ids,
        up_to_date=up_to_date,
        runtime=r,
    )


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _ai_enrichment_skip_reason(
    item, article, active_ai_settings, *, runtime: ModuleType
) -> str | None:
    if not active_ai_settings.ai_enabled:
        return "ai_disabled"
    if not active_ai_settings.ai_configured:
        return "ai_not_configured"
    if not active_ai_settings.auto_enrich_new_items:
        return "auto_enrich_disabled"
    if article is None:
        return "no_article"
    if not (article.text or "").strip():
        return "no_article_text"
    if not runtime._item_is_recent_auto_ai_enrichment_candidate(item):
        return runtime.AI_AUTO_ENRICH_OUTSIDE_NEW_ITEM_WINDOW_REASON
    return None


def _apply_classification_result(row: ItemClassification, result) -> None:
    row.primary_category = result.primary_category
    row.secondary_categories = result.secondary_categories
    row.confidence = result.confidence
    row.scores_json = result.scores
    row.matched_terms_json = result.matched_terms
    row.source_hash = result.source_hash
    row.rules_version = result.rules_version
    row.classified_at = datetime.now(timezone.utc)


def _sync_classification_tags(
    db,
    item: Item,
    article: Article | None,
    feed_name: str,
    feed_url: str,
    row: ItemClassification,
    *,
    runtime: ModuleType,
) -> None:
    feedback_adjustments = runtime.load_feedback_adjustments(
        db,
        tag_names=[row.primary_category, *(row.secondary_categories or [])],
    )
    runtime.sync_item_algorithm_tags(
        db,
        item_id=item.id,
        primary_category=row.primary_category,
        secondary_categories=row.secondary_categories,
        feed_id=item.feed_id,
        classification_confidence=row.confidence,
        title=item.title,
        summary=item.summary,
        article_text=article.text if article else None,
        feed_name=feed_name,
        feed_url=feed_url,
        feedback_adjustments=feedback_adjustments,
    )


def _complete_classification(
    item_id: str,
    parsed_item_id: uuid.UUID,
    category: str,
    feed_name: str,
    active_ai_settings,
    ai_skip_reason: str | None,
    queue_ai_enrichment: bool,
    integration_event_ids: list[uuid.UUID],
    alert_evaluation_request_ids: list[uuid.UUID],
    *,
    up_to_date: bool,
    runtime: ModuleType,
):
    integration_enqueue_ok = runtime.enqueue_integration_event_routing(
        integration_event_ids
    )
    evaluation_enqueue_ok = runtime.enqueue_alert_evaluation_requests(
        alert_evaluation_request_ids
    )
    notification_enqueue_ok = integration_enqueue_ok and evaluation_enqueue_ok
    if (
        integration_event_ids or alert_evaluation_request_ids
    ) and not notification_enqueue_ok:
        runtime.logger.warning(
            "classification_notification_enqueue_failed item_id=%s event_count=%s evaluation_count=%s",
            parsed_item_id,
            len(integration_event_ids),
            len(alert_evaluation_request_ids),
        )
    ioc_enqueue_ok = runtime._safe_enqueue_item_iocs(parsed_item_id)
    ai_enqueue_ok = _queue_or_record_ai_skip(
        parsed_item_id,
        category,
        feed_name,
        active_ai_settings,
        ai_skip_reason,
        queue_ai_enrichment,
        runtime=runtime,
    )
    return {
        "status": "skipped" if up_to_date else "ok",
        **({"reason": "up_to_date"} if up_to_date else {}),
        "item_id": item_id,
        "category": category,
        "notification_enqueue_failed": bool(
            integration_event_ids or alert_evaluation_request_ids
        )
        and not notification_enqueue_ok,
        "smtp_notification_enqueue_failed": bool(
            integration_event_ids or alert_evaluation_request_ids
        )
        and not notification_enqueue_ok,
        "alert_evaluation_requests": len(alert_evaluation_request_ids),
        "ioc_enqueue_failed": not ioc_enqueue_ok,
        "ai_enqueue_failed": queue_ai_enrichment and not ai_enqueue_ok,
    }


def _queue_or_record_ai_skip(
    item_id: uuid.UUID,
    category: str,
    feed_name: str,
    active_ai_settings,
    ai_skip_reason: str | None,
    queue_ai_enrichment: bool,
    *,
    runtime: ModuleType,
) -> bool:
    if queue_ai_enrichment:
        return runtime._safe_queue_item_ai_enrichment_run(
            item_id=item_id,
            trigger_source=runtime.AI_TRIGGER_AUTO,
            reason=None,
            model=getattr(active_ai_settings, "model", None),
            metadata={"category": category, "feed_name": feed_name, "force": False},
        )
    runtime._record_skipped_item_ai_enrichment_run(
        item_id=item_id,
        trigger_source=runtime.AI_TRIGGER_AUTO,
        reason=ai_skip_reason or "not_eligible",
        model=getattr(active_ai_settings, "model", None),
        metadata={"category": category, "feed_name": feed_name},
    )
    return True


def run_extract_item_iocs(item_id: str, *, runtime: ModuleType):
    r = runtime
    with r.db_session() as db:
        parsed_item_id = _parse_uuid(item_id)
        if parsed_item_id is None:
            return {
                "status": "skipped",
                "reason": "invalid_item_id",
                "item_id": item_id,
            }
        item, claim_reason = r._claim_item_article_processing_target(
            db, item_id=parsed_item_id
        )
        if item is None:
            return {
                "status": "skipped",
                "reason": claim_reason or "not_found",
                "item_id": item_id,
            }

        article = db.scalar(select(Article).where(Article.item_id == parsed_item_id))
        freshness_token = r._load_article_freshness_token(db, item_id=parsed_item_id)
        extracted = r.extract_iocs(
            title=item.title,
            summary=item.summary,
            article_text=article.text if article else None,
        )
        by_key = _aggregate_iocs(extracted)
        if r._article_was_refetched(
            db, item_id=parsed_item_id, expected_token=freshness_token
        ):
            r.logger.info(
                "ioc_extraction_stale_article_discarded item_id=%s", parsed_item_id
            )
            return {
                "status": "skipped",
                "reason": r.ARTICLE_REFRESHED_SKIP_REASON,
                "item_id": item_id,
            }

        linked_ioc_ids, values_by_type = _store_item_iocs(
            db, parsed_item_id, by_key, runtime=r
        )
        _remove_stale_item_iocs(db, parsed_item_id, linked_ioc_ids)
        item.ioc_extraction_state = (
            r.IOC_EXTRACTION_STATE_COMPLETED
            if linked_ioc_ids
            else r.IOC_EXTRACTION_STATE_COMPLETED_EMPTY
        )
        db.add(item)
        _sync_ioc_tags(db, item, article, values_by_type, runtime=r)
        db.commit()
    return {"status": "ok", "item_id": item_id, "ioc_count": len(by_key)}


def _aggregate_iocs(extracted) -> dict[tuple[str, str], dict[str, object]]:
    by_key: dict[tuple[str, str], dict[str, object]] = {}
    for match in extracted:
        key = (match.type, match.value_norm)
        record = by_key.get(key)
        if record is None:
            by_key[key] = {
                "value_raw": match.value_raw,
                "source_sections": {match.source_section},
                "occurrences": 1,
                "confidence": match.confidence,
            }
            continue
        record["source_sections"] = set(record["source_sections"]).union(
            {match.source_section}
        )
        record["occurrences"] = int(record["occurrences"]) + 1
        record["confidence"] = max(float(record["confidence"]), match.confidence)
    return by_key


def _store_item_iocs(db, item_id: uuid.UUID, by_key, *, runtime: ModuleType):
    linked_ioc_ids: set[uuid.UUID] = set()
    values_by_type: dict[str, list[str]] = {}
    now = datetime.now(timezone.utc)
    for (ioc_type, normalized_value), info in by_key.items():
        values_by_type.setdefault(ioc_type, []).append(normalized_value)
        ioc = runtime._get_or_create_ioc(
            db,
            ioc_type=ioc_type,
            ioc_value_norm=normalized_value,
            ioc_value_raw=str(info["value_raw"]),
            now=now,
        )
        linked_ioc_ids.add(ioc.id)
        link = db.scalar(
            select(ItemIOC).where(ItemIOC.item_id == item_id, ItemIOC.ioc_id == ioc.id)
        )
        if link is None:
            link = ItemIOC(item_id=item_id, ioc_id=ioc.id)
        link.source_section = ",".join(sorted(set(info["source_sections"])))
        link.occurrences = int(info["occurrences"])
        link.confidence = float(info["confidence"])
        db.add(link)
    return linked_ioc_ids, values_by_type


def _remove_stale_item_iocs(
    db, item_id: uuid.UUID, linked_ioc_ids: set[uuid.UUID]
) -> None:
    query = db.query(ItemIOC).filter(ItemIOC.item_id == item_id)
    if linked_ioc_ids:
        query = query.filter(ItemIOC.ioc_id.notin_(linked_ioc_ids))
    query.delete(synchronize_session=False)


def _sync_ioc_tags(
    db, item: Item, article: Article | None, values_by_type, *, runtime: ModuleType
) -> None:
    classification = db.scalar(
        select(ItemClassification).where(ItemClassification.item_id == item.id)
    )
    feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
    feedback_hints = [
        classification.primary_category if classification else "",
        *((classification.secondary_categories or []) if classification else []),
    ]
    for ioc_type, values in values_by_type.items():
        feedback_hints.append(f"ioc:{ioc_type}")
        feedback_hints.extend(values[:6])
    feedback_adjustments = runtime.load_feedback_adjustments(
        db, tag_names=feedback_hints
    )
    runtime.sync_item_algorithm_tags(
        db,
        item_id=item.id,
        primary_category=classification.primary_category
        if classification
        else "threat_intelligence_research",
        secondary_categories=classification.secondary_categories
        if classification
        else [],
        feed_id=item.feed_id,
        classification_confidence=classification.confidence if classification else 0.35,
        ioc_values_by_type=values_by_type,
        title=item.title,
        summary=item.summary,
        article_text=article.text if article else None,
        feed_name=feed.name if feed else "",
        feed_url=feed.url if feed else "",
        feedback_adjustments=feedback_adjustments,
    )


def run_reapply_recent_item_tags(
    days: int = 30,
    limit: int = 0,
    dispatch_token: str | None = None,
    *,
    runtime: ModuleType,
):
    r = runtime
    if days <= 0:
        return {"status": "skipped", "reason": "invalid_days", "days": days}
    if limit < 0:
        return {"status": "skipped", "reason": "invalid_limit", "limit": limit}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    processed = 0
    try:
        with r.tagging_reapply_lock(token=dispatch_token) as acquired:
            if not acquired:
                return {
                    "status": "skipped",
                    "reason": "already_running",
                    "days": days,
                    "limit": limit,
                }
            with r.db_session() as db:
                for item_id in db.scalars(_recent_item_query(cutoff, limit)):
                    item_processed = _reapply_item_tags(db, item_id, runtime=r)
                    if item_processed:
                        processed += 1
                    if (
                        item_processed
                        and processed % r.TAGGING_REAPPLY_COMMIT_INTERVAL == 0
                    ):
                        db.commit()
                        db.expire_all()
                db.commit()
    except r.CoordinationUnavailableError:
        return {
            "status": "error",
            "reason": "coordination_unavailable",
            "days": days,
            "limit": limit,
        }
    return {"status": "ok", "days": days, "limit": limit, "processed": processed}


def _recent_item_query(cutoff: datetime, limit: int):
    query = (
        select(Item.id)
        .where(Item.first_seen_at >= cutoff)
        .order_by(Item.first_seen_at.desc())
    )
    return query.limit(limit) if limit else query


def _reapply_item_tags(db, item_id: uuid.UUID, *, runtime: ModuleType) -> bool:
    item = db.scalar(select(Item).where(Item.id == item_id))
    if item is None:
        return False
    article = db.scalar(select(Article).where(Article.item_id == item.id))
    classification = db.scalar(
        select(ItemClassification).where(ItemClassification.item_id == item.id)
    )
    feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
    if feed is None:
        return False
    if classification is None:
        result = runtime.classify_item_content(
            title=item.title,
            summary=item.summary,
            article_text=article.text if article else None,
            feed_name=feed.name,
        )
        classification = ItemClassification(item_id=item.id)
        _apply_classification_result(classification, result)
        db.add(classification)
    _sync_classification_tags(
        db, item, article, feed.name, feed.url, classification, runtime=runtime
    )
    return True
