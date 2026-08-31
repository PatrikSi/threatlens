from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.item import Item
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
    DataAccessSourceInput,
    copy_data_access_envelope_lineage,
    get_data_access_envelope_sources,
    merge_data_access_envelope_sources,
)


def _locked_item_sources(
    db: Session,
    *,
    item_ids: Sequence[uuid.UUID],
    policy_revision: int,
    target_id: uuid.UUID,
    captured_at: datetime | None,
) -> tuple[DataAccessSourceInput, ...]:
    rows = db.execute(
        select(Item.id, Item.feed_id, Feed.handling_label_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.id.in_(item_ids))
        .order_by(Item.id)
        .with_for_update(read=True, of=(Item, Feed))
    ).all()
    return tuple(
        DataAccessSourceInput(
            source_type="item",
            source_id=str(item_id),
            source_version=f"ai-telemetry:{target_id}:item:{item_id}",
            source_feed_id=feed_id,
            handling_label_id=handling_label_id,
            captured_policy_revision=policy_revision,
            captured_at=captured_at,
        )
        for item_id, feed_id, handling_label_id in rows
    )


def _locked_feed_sources(
    db: Session,
    *,
    feed_ids: Sequence[uuid.UUID],
    policy_revision: int,
    target_id: uuid.UUID,
    captured_at: datetime | None,
) -> tuple[DataAccessSourceInput, ...]:
    rows = db.execute(
        select(Feed.id, Feed.handling_label_id)
        .where(Feed.id.in_(feed_ids))
        .order_by(Feed.id)
        .with_for_update(read=True)
    ).all()
    return tuple(
        DataAccessSourceInput(
            source_type="feed",
            source_id=str(feed_id),
            source_version=f"ai-telemetry:{target_id}:feed:{feed_id}",
            source_feed_id=feed_id,
            handling_label_id=handling_label_id,
            captured_policy_revision=policy_revision,
            captured_at=captured_at,
        )
        for feed_id, handling_label_id in rows
    )


def _copy_resource_lineage_if_present(
    db: Session,
    *,
    source_resource_type: str,
    source_resource_id: uuid.UUID | None,
    target_resource_type: str,
    target_resource_id: uuid.UUID,
) -> bool:
    if source_resource_id is None:
        return False
    sources = get_data_access_envelope_sources(
        db,
        resource_type=source_resource_type,
        resource_id=source_resource_id,
    )
    if not sources:
        return False
    copy_data_access_envelope_lineage(
        db,
        source_resource_type=source_resource_type,
        source_resource_id=source_resource_id,
        target_resource_type=target_resource_type,
        target_resource_id=target_resource_id,
        operation="merge",
    )
    return True


def _copy_child_run_lineage(db: Session, *, parent_run_id: uuid.UUID) -> None:
    child_ids = list(
        db.scalars(
            select(AITaskRun.id)
            .where(
                AITaskRun.parent_run_id == parent_run_id,
                AITaskRun.data_access_lineage_complete.is_(True),
            )
            .order_by(AITaskRun.id)
            .with_for_update(read=True)
        ).all()
    )
    for child_id in child_ids:
        _copy_resource_lineage_if_present(
            db,
            source_resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
            source_resource_id=child_id,
            target_resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
            target_resource_id=parent_run_id,
        )


def _ensure_quarantined_if_empty(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    policy_revision: int,
    captured_at: datetime | None,
) -> None:
    if get_data_access_envelope_sources(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
    ):
        return
    _merge_quarantine_source(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        policy_revision=policy_revision,
        captured_at=captured_at,
    )


def _merge_quarantine_source(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    policy_revision: int,
    captured_at: datetime | None,
) -> None:
    merge_data_access_envelope_sources(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        sources=(
            DataAccessSourceInput(
                source_type="unresolved",
                source_id=str(resource_id),
                source_version=f"ai-telemetry:{resource_id}:unresolved",
                handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
                captured_policy_revision=policy_revision,
                captured_at=captured_at,
            ),
        ),
    )


def _unique_uuids(values: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    return tuple(sorted({value for value in values if value is not None}, key=str))
