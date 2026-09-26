"""Preserve captured evidence boundaries on hunt and assessment audit metadata."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.item import Item
from app.models.team_item_assessment import TeamItemAssessment
from app.services.export_job_access import ExportJobAccessDenied, load_export_sources
from app.services.team_assessment_access import RequestPrincipal


def assessment_audit_labels(db: Session, row: TeamItemAssessment) -> set[uuid.UUID]:
    labels = set(
        db.scalars(
            select(Feed.handling_label_id)
            .join(Item, Item.feed_id == Feed.id)
            .where(Item.id == row.item_id)
        )
    )
    for snapshot in (row.source_encrypted, row.result_source_encrypted):
        if snapshot is None:
            continue
        try:
            sources = load_export_sources(
                RequestPrincipal(row.principal_id, source_encrypted=snapshot)
            )
            if len(sources) != 1 or sources[0].item_id != row.item_id:
                labels.add(QUARANTINE_HANDLING_LABEL_ID)
            else:
                labels.add(sources[0].captured_label_id)
        except ExportJobAccessDenied:
            labels.add(QUARANTINE_HANDLING_LABEL_ID)
    return labels
