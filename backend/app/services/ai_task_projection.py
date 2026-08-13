from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.ai_task_run import AITaskRun
from app.models.audit_log import AuditLog
from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.schemas.ai import (
    AIAuditEntryResponse,
    AIDailyBriefSourceItemResponse,
    AITaskRunResponse,
)


def list_daily_brief_source_items(
    db: Session,
    *,
    daily_brief_id: uuid.UUID,
    included: bool | None = None,
    limit: int = 200,
) -> list[AIDailyBriefSourceItemResponse] | None:
    brief_exists = db.scalar(
        select(AIDailyBrief.id).where(AIDailyBrief.id == daily_brief_id)
    )
    if brief_exists is None:
        return None
    query = select(AIDailyBriefSourceItem).where(
        AIDailyBriefSourceItem.daily_brief_id == daily_brief_id
    )
    if included is not None:
        query = query.where(AIDailyBriefSourceItem.included.is_(included))
    rows = list(
        db.scalars(
            query.order_by(
                AIDailyBriefSourceItem.included.desc(),
                AIDailyBriefSourceItem.rank.asc(),
            ).limit(limit)
        )
    )
    return [
        AIDailyBriefSourceItemResponse(
            id=row.id,
            daily_brief_id=row.daily_brief_id,
            item_id=row.item_id,
            included=bool(row.included),
            rank=int(row.rank or 0),
            exclusion_reason=row.exclusion_reason,
            title_snapshot=row.title_snapshot,
            feed_name_snapshot=row.feed_name_snapshot,
            url_snapshot=row.url_snapshot,
            classification_snapshot=row.classification_snapshot,
            relevance_score_snapshot=(
                float(row.relevance_score_snapshot)
                if row.relevance_score_snapshot is not None
                else None
            ),
            relevance_label_snapshot=row.relevance_label_snapshot,
            published_at_snapshot=row.published_at_snapshot,
            first_seen_at_snapshot=row.first_seen_at_snapshot,
            created_at=row.created_at,
        )
        for row in rows
    ]


def list_ai_manual_actions(
    db: Session, *, limit: int = 50
) -> list[AIAuditEntryResponse]:
    logs = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action.in_(
                    [
                        "ai.connection.test",
                        "ai.daily_brief.generate",
                        "ai.daily_brief.queue",
                        "ai.daily_brief.backfill.queue",
                        "ai.reprocess.queue",
                        "reports.generate.queue",
                        "reports.generate.retry",
                    ]
                )
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    )
    return _map_audit_entries(db, logs)


def list_ai_prompt_history(
    db: Session, *, limit: int = 50
) -> list[AIAuditEntryResponse]:
    logs = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "ai.settings.update")
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    )
    return _map_audit_entries(db, logs)


def _map_run_responses(db: Session, runs: list[AITaskRun]) -> list[AITaskRunResponse]:
    actor_ids = [run.actor_user_id for run in runs if run.actor_user_id]
    email_map = _load_user_emails(db, actor_ids)
    item_context_map = _load_run_item_context(
        db, [run.item_id for run in runs if run.item_id]
    )
    return [
        AITaskRunResponse(
            id=run.id,
            task_type=run.task_type,
            trigger_source=run.trigger_source,
            status=run.status,
            reason=run.reason,
            celery_task_id=run.celery_task_id,
            worker_name=run.worker_name,
            actor_user_id=run.actor_user_id,
            actor_email=email_map.get(run.actor_user_id),
            item_id=run.item_id,
            item_title=item_context_map.get(run.item_id, {}).get("title")
            if run.item_id
            else None,
            item_url=item_context_map.get(run.item_id, {}).get("url")
            if run.item_id
            else None,
            feed_name=item_context_map.get(run.item_id, {}).get("feed_name")
            if run.item_id
            else None,
            item_first_seen_at=item_context_map.get(run.item_id, {}).get(
                "first_seen_at"
            )
            if run.item_id
            else None,
            item_published_at=item_context_map.get(run.item_id, {}).get("published_at")
            if run.item_id
            else None,
            daily_brief_id=run.daily_brief_id,
            report_id=run.report_id,
            parent_run_id=run.parent_run_id,
            model=run.model,
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            total_tokens=run.total_tokens,
            latency_ms=run.latency_ms,
            duration_ms=run.duration_ms,
            prompt_char_count=run.prompt_char_count,
            response_char_count=run.response_char_count,
            input_text_chars=run.input_text_chars,
            error=run.error,
            metadata=dict(run.metadata_json or {}),
            target_count=run.target_count,
            processed_count=int(run.processed_count or 0),
            success_count=int(run.success_count or 0),
            error_count=int(run.error_count or 0),
            skipped_count=int(run.skipped_count or 0),
            skipped_unchanged_count=int(run.skipped_unchanged_count or 0),
            skipped_ineligible_count=int(run.skipped_ineligible_count or 0),
            queued_at=run.queued_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        for run in runs
    ]


def _load_run_item_context(
    db: Session,
    item_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, Any]]:
    unique_item_ids = list({item_id for item_id in item_ids if item_id})
    if not unique_item_ids:
        return {}
    rows = db.execute(
        select(
            Item.id,
            Item.title,
            Item.url,
            Item.first_seen_at,
            Item.published_at,
            Feed.name.label("feed_name"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.id.in_(unique_item_ids))
    ).all()
    return {
        item_id: {
            "title": title,
            "url": url,
            "first_seen_at": first_seen_at,
            "published_at": published_at,
            "feed_name": feed_name,
        }
        for item_id, title, url, first_seen_at, published_at, feed_name in rows
    }


def _map_audit_entries(db: Session, logs: list[AuditLog]) -> list[AIAuditEntryResponse]:
    actor_ids = [log.actor_user_id for log in logs if log.actor_user_id]
    email_map = _load_user_emails(db, actor_ids)
    return [
        AIAuditEntryResponse(
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_email=email_map.get(log.actor_user_id),
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            success=bool(log.success),
            metadata=dict(log.metadata_json or {}),
            created_at=log.created_at,
        )
        for log in logs
    ]


def _load_user_emails(
    db: Session, actor_ids: list[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    unique_actor_ids = [
        actor_id for actor_id in {value for value in actor_ids if value}
    ]
    if not unique_actor_ids:
        return {}
    rows = db.execute(
        select(User.id, User.email).where(User.id.in_(unique_actor_ids))
    ).all()
    return {user_id: email for user_id, email in rows}
