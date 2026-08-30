from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.alert_occurrence import AlertOccurrence
from app.models.data_policy import (
    DataPolicyState,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.integration import IntegrationDelivery, IntegrationEvent
from app.models.investigation import Investigation, InvestigationEvidence
from app.models.ioc import ItemIOC
from app.models.item import Item
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.report import Report
from app.models.report_source_item import ReportSourceItem
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
    DATA_ACCESS_RESOURCE_INVESTIGATION,
    DATA_ACCESS_RESOURCE_REPORT,
    DataAccessEnvelopeSnapshot,
    DataAccessSourceInput,
    copy_data_access_envelope_lineage,
    get_data_access_envelope,
    get_data_access_envelope_sources,
    merge_data_access_envelope_sources,
)
from app.services.data_access_policy import (
    DataPolicyRevisionConflict,
    DataPolicyUnavailable,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_SYSTEM_EVENT_SOURCE_TYPES = frozenset({"digest_window", "system", "test"})
_MAX_DELIVERY_LINEAGE_DEPTH = 32


def ensure_report_data_access_envelope(
    db: Session,
    *,
    report_id: uuid.UUID,
    expected_policy_revision: int | None = None,
) -> DataAccessEnvelopeSnapshot:
    report = db.get(Report, report_id)
    if report is None:
        raise DataPolicyUnavailable(
            "The report disappeared before its data-policy provenance could be recorded.",
            context={"report_id": str(report_id)},
        )
    existing = get_data_access_envelope(
        db, resource_type=DATA_ACCESS_RESOURCE_REPORT, resource_id=report.id
    )
    if existing is not None:
        return existing
    db.flush()
    source_rows = list(
        db.scalars(
            select(ReportSourceItem)
            .where(ReportSourceItem.report_id == report_id)
            .order_by(ReportSourceItem.id)
        ).all()
    )
    revision = _locked_policy_revision(db)
    if expected_policy_revision is not None and revision != expected_policy_revision:
        raise DataPolicyRevisionConflict(
            "Data access policy changed while the report was being prepared. Retry report creation.",
            context={
                "expected_revision": expected_policy_revision,
                "current_revision": revision,
            },
        )
    sources = _item_sources(
        db,
        refs=[
            _ItemSourceRef(
                item_id=row.item_id,
                source_id=str(row.item_id or row.id),
                source_version=str(row.id),
                captured_at=row.created_at,
            )
            for row in source_rows
        ],
        policy_revision=revision,
    )
    if not sources:
        sources = (
            _system_source(
                source_id=str(report.id),
                source_version=f"report:{report.id}:source-free",
                policy_revision=revision,
                captured_at=report.created_at,
            ),
        )
    return merge_data_access_envelope_sources(
        db,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report.id,
        sources=sources,
    )


def ensure_daily_brief_data_access_envelope(
    db: Session,
    *,
    brief_id: uuid.UUID,
    expected_policy_revision: int | None = None,
) -> DataAccessEnvelopeSnapshot:
    brief = db.get(AIDailyBrief, brief_id)
    if brief is None:
        raise DataPolicyUnavailable(
            "The daily brief disappeared before its data-policy provenance could be recorded.",
            context={"daily_brief_id": str(brief_id)},
        )
    db.flush()
    source_rows = list(
        db.scalars(
            select(AIDailyBriefSourceItem)
            .where(AIDailyBriefSourceItem.daily_brief_id == brief_id)
            .order_by(AIDailyBriefSourceItem.id)
        ).all()
    )
    revision = _locked_policy_revision(db)
    _require_expected_policy_revision(
        revision,
        expected_policy_revision=expected_policy_revision,
        operation="daily brief preparation",
    )
    sources = _item_sources(
        db,
        refs=[
            _ItemSourceRef(
                item_id=row.item_id,
                source_id=str(row.item_id or row.id),
                source_version=str(row.id),
                captured_at=row.created_at,
            )
            for row in source_rows
        ],
        policy_revision=revision,
        source_version_from_digest=True,
    )
    existing = get_data_access_envelope(
        db,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
    )
    if existing is not None:
        existing_identities = {
            (
                source.source_type,
                source.source_id,
                source.source_version,
                source.source_parent_id,
            )
            for source in get_data_access_envelope_sources(
                db,
                resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
                resource_id=brief.id,
            )
        }
        sources = tuple(
            source
            for source in sources
            if (
                source.source_type,
                source.source_id,
                source.source_version,
                source.source_parent_id,
            )
            not in existing_identities
        )
        if not sources:
            return existing
    if not sources:
        sources = (
            _system_source(
                source_id=str(brief.id),
                source_version=f"daily-brief:{brief.id}:source-free",
                policy_revision=revision,
                captured_at=brief.created_at,
            ),
        )
    return merge_data_access_envelope_sources(
        db,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
        sources=sources,
    )


def ensure_investigation_data_access_envelope(
    db: Session, *, investigation_id: uuid.UUID
) -> DataAccessEnvelopeSnapshot:
    investigation = db.get(Investigation, investigation_id)
    if investigation is None:
        raise DataPolicyUnavailable(
            "The investigation disappeared before its data-policy provenance could be recorded.",
            context={"investigation_id": str(investigation_id)},
        )
    existing = get_data_access_envelope(
        db,
        resource_type=DATA_ACCESS_RESOURCE_INVESTIGATION,
        resource_id=investigation.id,
    )
    if existing is not None:
        return existing
    revision = _locked_policy_revision(db)
    snapshot = merge_data_access_envelope_sources(
        db,
        resource_type=DATA_ACCESS_RESOURCE_INVESTIGATION,
        resource_id=investigation.id,
        sources=(
            _system_source(
                source_id=str(investigation.id),
                source_version=f"investigation:{investigation.id}:created",
                policy_revision=revision,
                captured_at=investigation.created_at,
            ),
        ),
    )
    # Investigation provenance is monotonic: removing evidence must not
    # declassify snapshots, notes, or activity already derived from it.
    evidence_rows = list(
        db.scalars(
            select(InvestigationEvidence)
            .where(InvestigationEvidence.investigation_id == investigation.id)
            .order_by(InvestigationEvidence.id)
        ).all()
    )
    for evidence in evidence_rows:
        snapshot = _merge_investigation_evidence(
            db,
            investigation=investigation,
            evidence=evidence,
            policy_revision=revision,
        )
    return snapshot


def merge_investigation_evidence_data_access(
    db: Session,
    *,
    evidence: InvestigationEvidence,
    expected_policy_revision: int | None = None,
) -> DataAccessEnvelopeSnapshot:
    investigation = db.get(Investigation, evidence.investigation_id)
    if investigation is None:
        raise DataPolicyUnavailable(
            "The investigation disappeared before evidence provenance could be recorded.",
            context={"investigation_id": str(evidence.investigation_id)},
        )
    revision = _locked_policy_revision(db)
    _require_expected_policy_revision(
        revision,
        expected_policy_revision=expected_policy_revision,
        operation="investigation evidence capture",
    )
    ensure_investigation_data_access_envelope(db, investigation_id=investigation.id)
    return _merge_investigation_evidence(
        db,
        investigation=investigation,
        evidence=evidence,
        policy_revision=revision,
    )


def ensure_alert_occurrence_data_access_envelope(
    db: Session,
    *,
    occurrence_id: uuid.UUID,
    expected_policy_revision: int | None = None,
) -> DataAccessEnvelopeSnapshot:
    occurrence = db.get(AlertOccurrence, occurrence_id)
    if occurrence is None:
        raise DataPolicyUnavailable(
            "The alert occurrence disappeared before its data-policy provenance could be recorded.",
            context={"alert_occurrence_id": str(occurrence_id)},
        )
    existing = get_data_access_envelope(
        db,
        resource_type=DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
        resource_id=occurrence.id,
    )
    if existing is not None:
        return existing
    revision = _locked_policy_revision(db)
    _require_expected_policy_revision(
        revision,
        expected_policy_revision=expected_policy_revision,
        operation="alert occurrence capture",
    )
    sources = _item_sources(
        db,
        refs=(
            _ItemSourceRef(
                item_id=occurrence.item_id,
                source_id=str(occurrence.item_id_snapshot),
                source_version=str(occurrence.id),
                captured_at=occurrence.created_at,
                source_digest=occurrence.item_content_hash,
            ),
        ),
        policy_revision=revision,
    )
    return merge_data_access_envelope_sources(
        db,
        resource_type=DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
        resource_id=occurrence.id,
        sources=sources,
    )


def ensure_integration_event_data_access_envelope(
    db: Session, *, event_id: uuid.UUID
) -> DataAccessEnvelopeSnapshot:
    event = db.get(IntegrationEvent, event_id)
    if event is None:
        raise DataPolicyUnavailable(
            "The integration event disappeared before its data-policy provenance could be recorded.",
            context={"integration_event_id": str(event_id)},
        )
    existing = get_data_access_envelope(
        db,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
        resource_id=event.id,
    )
    if existing is not None:
        return existing
    source_resource = _event_parent_resource(event)
    if source_resource is not None:
        parent_type, parent_id = source_resource
        if _parent_resource_exists(db, parent_type, parent_id):
            _ensure_parent_resource_envelope(db, parent_type, parent_id)
            return copy_data_access_envelope_lineage(
                db,
                source_resource_type=parent_type,
                source_resource_id=parent_id,
                target_resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
                target_resource_id=event.id,
                operation="merge",
            )

    revision = _locked_policy_revision(db)
    sources = _direct_event_sources(db, event=event, policy_revision=revision)
    return merge_data_access_envelope_sources(
        db,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
        resource_id=event.id,
        sources=sources,
    )


def ensure_integration_delivery_data_access_envelope(
    db: Session, *, delivery_id: uuid.UUID
) -> DataAccessEnvelopeSnapshot:
    return _ensure_delivery_envelope(db, delivery_id=delivery_id, seen=frozenset())


class _ItemSourceRef:
    __slots__ = (
        "captured_at",
        "item_id",
        "source_digest",
        "source_id",
        "source_version",
    )

    def __init__(
        self,
        *,
        item_id: uuid.UUID | None,
        source_id: str,
        source_version: str,
        captured_at: datetime | None,
        source_digest: str | None = None,
    ) -> None:
        self.item_id = item_id
        self.source_id = source_id
        self.source_version = source_version
        self.captured_at = captured_at
        self.source_digest = source_digest


def _item_sources(
    db: Session,
    *,
    refs: Sequence[_ItemSourceRef],
    policy_revision: int,
    source_version_from_digest: bool = False,
) -> tuple[DataAccessSourceInput, ...]:
    item_ids = {ref.item_id for ref in refs if ref.item_id is not None}
    rows = db.execute(
        select(
            Item.id,
            Item.feed_id,
            Item.content_hash,
            Feed.handling_label_id,
        )
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.id.in_(item_ids))
        .order_by(Item.id)
        .with_for_update(read=True)
    ).all()
    items = {row.id: row for row in rows}
    sources: list[DataAccessSourceInput] = []
    for ref in refs:
        row = items.get(ref.item_id)
        if row is None:
            sources.append(
                _unresolved_source(
                    source_id=ref.source_id,
                    source_version=ref.source_version,
                    policy_revision=policy_revision,
                    captured_at=ref.captured_at,
                    source_digest=ref.source_digest,
                )
            )
            continue
        sources.append(
            DataAccessSourceInput(
                source_type="item",
                source_id=str(row.id),
                source_version=(
                    # Daily brief audit rows are replaced on force runs.
                    f"content:{row.content_hash}"
                    if source_version_from_digest
                    else ref.source_version
                ),
                source_feed_id=row.feed_id,
                handling_label_id=row.handling_label_id,
                captured_policy_revision=policy_revision,
                source_digest=_valid_digest(ref.source_digest or row.content_hash),
                captured_at=ref.captured_at,
            )
        )
    return tuple(sources)


def _merge_investigation_evidence(
    db: Session,
    *,
    investigation: Investigation,
    evidence: InvestigationEvidence,
    policy_revision: int,
) -> DataAccessEnvelopeSnapshot:
    if evidence.source_type == "item":
        sources = _item_sources(
            db,
            refs=(
                _ItemSourceRef(
                    item_id=evidence.source_id,
                    source_id=str(evidence.source_id),
                    source_version=str(evidence.id),
                    captured_at=evidence.created_at,
                ),
            ),
            policy_revision=policy_revision,
        )
        return merge_data_access_envelope_sources(
            db,
            resource_type=DATA_ACCESS_RESOURCE_INVESTIGATION,
            resource_id=investigation.id,
            sources=sources,
        )
    if evidence.source_type == "ioc":
        item_ids = list(
            db.scalars(
                select(ItemIOC.item_id)
                .where(ItemIOC.ioc_id == evidence.source_id)
                .order_by(ItemIOC.item_id)
            ).all()
        )
        refs = tuple(
            _ItemSourceRef(
                item_id=item_id,
                source_id=str(item_id),
                source_version=str(evidence.id),
                captured_at=evidence.created_at,
            )
            for item_id in item_ids
        )
        sources = _item_sources(db, refs=refs, policy_revision=policy_revision) or (
            _unresolved_source(
                source_id=str(evidence.source_id),
                source_version=str(evidence.id),
                policy_revision=policy_revision,
                captured_at=evidence.created_at,
            ),
        )
        return merge_data_access_envelope_sources(
            db,
            resource_type=DATA_ACCESS_RESOURCE_INVESTIGATION,
            resource_id=investigation.id,
            sources=sources,
        )
    parent_types = {
        "alert_occurrence": DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
        "report": DATA_ACCESS_RESOURCE_REPORT,
    }
    parent_type = parent_types.get(evidence.source_type)
    if parent_type is None:
        return merge_data_access_envelope_sources(
            db,
            resource_type=DATA_ACCESS_RESOURCE_INVESTIGATION,
            resource_id=investigation.id,
            sources=(
                _unresolved_source(
                    source_id=str(evidence.source_id),
                    source_version=str(evidence.id),
                    policy_revision=policy_revision,
                    captured_at=evidence.created_at,
                ),
            ),
        )
    if not _parent_resource_exists(db, parent_type, evidence.source_id):
        return merge_data_access_envelope_sources(
            db,
            resource_type=DATA_ACCESS_RESOURCE_INVESTIGATION,
            resource_id=investigation.id,
            sources=(
                _unresolved_source(
                    source_id=str(evidence.source_id),
                    source_version=str(evidence.id),
                    policy_revision=policy_revision,
                    captured_at=evidence.created_at,
                ),
            ),
        )
    _ensure_parent_resource_envelope(db, parent_type, evidence.source_id)
    return copy_data_access_envelope_lineage(
        db,
        source_resource_type=parent_type,
        source_resource_id=evidence.source_id,
        target_resource_type=DATA_ACCESS_RESOURCE_INVESTIGATION,
        target_resource_id=investigation.id,
        operation="merge",
    )


def _event_parent_resource(
    event: IntegrationEvent,
) -> tuple[str, uuid.UUID] | None:
    parent_types = {
        "ai_daily_brief": DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        "daily_brief": DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        "report": DATA_ACCESS_RESOURCE_REPORT,
    }
    parent_type = parent_types.get(event.source_type)
    parent_id = _uuid_or_none(event.source_id)
    if parent_type is None or parent_id is None:
        return None
    return parent_type, parent_id


def _direct_event_sources(
    db: Session,
    *,
    event: IntegrationEvent,
    policy_revision: int,
) -> tuple[DataAccessSourceInput, ...]:
    version = f"event:{event.id}"
    source_id = event.source_id or str(event.id)
    parsed_source_id = _uuid_or_none(event.source_id)
    if event.source_type == "item" and parsed_source_id is not None:
        return _item_sources(
            db,
            refs=(
                _ItemSourceRef(
                    item_id=parsed_source_id,
                    source_id=source_id,
                    source_version=version,
                    captured_at=event.created_at,
                ),
            ),
            policy_revision=policy_revision,
        )
    if event.source_type == "feed" and parsed_source_id is not None:
        return (
            _feed_source(
                db,
                feed_id=parsed_source_id,
                source_version=version,
                policy_revision=policy_revision,
                captured_at=event.created_at,
            ),
        )
    if (
        event.source_type == "notification_webhook_delivery"
        and parsed_source_id is not None
    ):
        legacy = db.get(NotificationWebhookDelivery, parsed_source_id)
        if legacy is not None and legacy.item_id is not None:
            return _item_sources(
                db,
                refs=(
                    _ItemSourceRef(
                        item_id=legacy.item_id,
                        source_id=str(legacy.item_id),
                        source_version=version,
                        captured_at=event.created_at,
                    ),
                ),
                policy_revision=policy_revision,
            )
        if legacy is not None and legacy.feed_id is not None:
            return (
                _feed_source(
                    db,
                    feed_id=legacy.feed_id,
                    source_version=version,
                    policy_revision=policy_revision,
                    captured_at=event.created_at,
                ),
            )
    if event.source_type in _SYSTEM_EVENT_SOURCE_TYPES:
        return (
            _system_source(
                source_id=source_id,
                source_version=version,
                policy_revision=policy_revision,
                captured_at=event.created_at,
            ),
        )
    return (
        _unresolved_source(
            source_id=source_id,
            source_version=version,
            policy_revision=policy_revision,
            captured_at=event.created_at,
        ),
    )


def _ensure_delivery_envelope(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    seen: frozenset[uuid.UUID],
) -> DataAccessEnvelopeSnapshot:
    if delivery_id in seen or len(seen) >= _MAX_DELIVERY_LINEAGE_DEPTH:
        raise DataPolicyUnavailable(
            "Integration delivery lineage contains a cycle or exceeds the supported replay depth.",
            context={"integration_delivery_id": str(delivery_id)},
        )
    delivery = db.get(IntegrationDelivery, delivery_id)
    if delivery is None:
        raise DataPolicyUnavailable(
            "The integration delivery disappeared before its data-policy provenance could be recorded.",
            context={"integration_delivery_id": str(delivery_id)},
        )
    existing = get_data_access_envelope(
        db,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
        resource_id=delivery.id,
    )
    if (
        existing is not None
        and delivery.source_delivery_id is None
        and delivery.event_id is None
    ):
        return existing
    next_seen = seen | {delivery.id}
    if delivery.source_delivery_id is not None:
        _ensure_delivery_envelope(
            db, delivery_id=delivery.source_delivery_id, seen=next_seen
        )
        return copy_data_access_envelope_lineage(
            db,
            source_resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            source_resource_id=delivery.source_delivery_id,
            target_resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            target_resource_id=delivery.id,
            operation="merge",
        )
    if delivery.event_id is not None:
        ensure_integration_event_data_access_envelope(db, event_id=delivery.event_id)
        return copy_data_access_envelope_lineage(
            db,
            source_resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
            source_resource_id=delivery.event_id,
            target_resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            target_resource_id=delivery.id,
            operation="merge",
        )

    revision = _locked_policy_revision(db)
    legacy_sources = _legacy_delivery_sources(
        db, delivery=delivery, policy_revision=revision
    )
    sources = legacy_sources or (
        (
            _system_source(
                source_id=str(delivery.id),
                source_version=f"delivery:{delivery.id}",
                policy_revision=revision,
                captured_at=delivery.created_at,
            )
            if delivery.delivery_kind == "test"
            else _unresolved_source(
                source_id=str(delivery.id),
                source_version=f"delivery:{delivery.id}",
                policy_revision=revision,
                captured_at=delivery.created_at,
            )
        ),
    )
    return merge_data_access_envelope_sources(
        db,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
        resource_id=delivery.id,
        sources=sources,
    )


def _legacy_delivery_sources(
    db: Session,
    *,
    delivery: IntegrationDelivery,
    policy_revision: int,
) -> tuple[DataAccessSourceInput, ...]:
    payload = delivery.payload_json if isinstance(delivery.payload_json, dict) else {}
    legacy_id = _uuid_or_none(payload.get("legacy_webhook_delivery_id"))
    legacy = db.get(NotificationWebhookDelivery, legacy_id) if legacy_id else None
    if legacy is None:
        return ()
    version = f"delivery:{delivery.id}"
    if legacy.item_id is not None:
        return _item_sources(
            db,
            refs=(
                _ItemSourceRef(
                    item_id=legacy.item_id,
                    source_id=str(legacy.item_id),
                    source_version=version,
                    captured_at=delivery.created_at,
                ),
            ),
            policy_revision=policy_revision,
        )
    if legacy.feed_id is not None:
        return (
            _feed_source(
                db,
                feed_id=legacy.feed_id,
                source_version=version,
                policy_revision=policy_revision,
                captured_at=delivery.created_at,
            ),
        )
    return ()


def _ensure_parent_resource_envelope(
    db: Session, resource_type: str, resource_id: uuid.UUID
) -> DataAccessEnvelopeSnapshot:
    existing = get_data_access_envelope(
        db, resource_type=resource_type, resource_id=resource_id
    )
    if existing is not None:
        return existing
    if resource_type == DATA_ACCESS_RESOURCE_REPORT:
        return ensure_report_data_access_envelope(db, report_id=resource_id)
    if resource_type == DATA_ACCESS_RESOURCE_DAILY_BRIEF:
        return ensure_daily_brief_data_access_envelope(db, brief_id=resource_id)
    if resource_type == DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE:
        return ensure_alert_occurrence_data_access_envelope(
            db, occurrence_id=resource_id
        )
    raise DataPolicyUnavailable(
        "The requested data-policy parent resource type is not supported.",
        context={"resource_type": resource_type, "resource_id": str(resource_id)},
    )


def _parent_resource_exists(
    db: Session, resource_type: str, resource_id: uuid.UUID
) -> bool:
    model_by_type = {
        DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE: AlertOccurrence,
        DATA_ACCESS_RESOURCE_DAILY_BRIEF: AIDailyBrief,
        DATA_ACCESS_RESOURCE_REPORT: Report,
    }
    model = model_by_type.get(resource_type)
    return model is not None and db.get(model, resource_id) is not None


def _feed_source(
    db: Session,
    *,
    feed_id: uuid.UUID,
    source_version: str,
    policy_revision: int,
    captured_at: datetime | None,
) -> DataAccessSourceInput:
    feed = db.scalar(
        select(Feed)
        .where(Feed.id == feed_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if feed is None:
        return _unresolved_source(
            source_id=str(feed_id),
            source_version=source_version,
            policy_revision=policy_revision,
            captured_at=captured_at,
        )
    return DataAccessSourceInput(
        source_type="feed",
        source_id=str(feed.id),
        source_version=source_version,
        source_feed_id=feed.id,
        handling_label_id=feed.handling_label_id,
        captured_policy_revision=policy_revision,
        captured_at=captured_at,
    )


def _system_source(
    *,
    source_id: str,
    source_version: str,
    policy_revision: int,
    captured_at: datetime | None,
) -> DataAccessSourceInput:
    return DataAccessSourceInput(
        source_type="system",
        source_id=source_id,
        source_version=source_version,
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        captured_policy_revision=policy_revision,
        captured_at=captured_at,
    )


def _unresolved_source(
    *,
    source_id: str,
    source_version: str,
    policy_revision: int,
    captured_at: datetime | None,
    source_digest: str | None = None,
) -> DataAccessSourceInput:
    return DataAccessSourceInput(
        source_type="unresolved",
        source_id=source_id,
        source_version=source_version,
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
        captured_policy_revision=policy_revision,
        source_digest=_valid_digest(source_digest),
        captured_at=captured_at,
    )


def _locked_policy_revision(db: Session) -> int:
    revision = db.scalar(
        select(DataPolicyState.revision)
        .where(DataPolicyState.id == 1)
        .with_for_update(read=True)
    )
    if revision is None:
        raise DataPolicyUnavailable(
            "Data policy state is missing. Restore it before creating derived data."
        )
    return int(revision)


def lock_data_policy_revision_for_derivation(db: Session) -> int:
    return _locked_policy_revision(db)


def _require_expected_policy_revision(
    current_revision: int,
    *,
    expected_policy_revision: int | None,
    operation: str,
) -> None:
    if expected_policy_revision is None or current_revision == expected_policy_revision:
        return
    raise DataPolicyRevisionConflict(
        f"Data access policy changed during {operation}. Retry the operation.",
        context={
            "expected_revision": expected_policy_revision,
            "current_revision": current_revision,
        },
    )


def _valid_digest(value: str | None) -> str | None:
    if value is None or _SHA256_PATTERN.fullmatch(value) is None:
        return None
    return value.lower()


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


__all__ = [
    "ensure_alert_occurrence_data_access_envelope",
    "ensure_daily_brief_data_access_envelope",
    "ensure_integration_delivery_data_access_envelope",
    "ensure_integration_event_data_access_envelope",
    "ensure_investigation_data_access_envelope",
    "ensure_report_data_access_envelope",
    "lock_data_policy_revision_for_derivation",
    "merge_investigation_evidence_data_access",
]
