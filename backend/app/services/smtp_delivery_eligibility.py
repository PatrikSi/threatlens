from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alert_occurrence import AlertOccurrence
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.user import User
from app.services.smtp_alert_context import (
    SMTP_ALERT_KEYWORD_CAP as _SMTP_ALERT_KEYWORD_CAP,
    SMTP_ALERT_NAME_CAP as _SMTP_ALERT_NAME_CAP,
    SMTP_LEGACY_ALERT_OCCURRENCE_CAP as _SMTP_LEGACY_ALERT_OCCURRENCE_CAP,
    legacy_smtp_payload_alert_context as _legacy_payload_alert_context,
)
from app.services.notification_webhook_templates import AlertMatchContext
from app.services.smtp_delivery_errors import (
    SMTPDeliveryIneligibleError,
    SMTPDeliverySourceCompatibilityError,
    SMTPDeliverySourceContextError,
    SMTPDeliveryTemporarilyIneligibleError,
)
from app.services.report_event_compatibility import (
    validate_report_ready_delivery_owner,
)
from app.services.smtp_schema_compatibility import (
    ensure_smtp_config_schema_compatible,
    ensure_smtp_delivery_schema_compatible as ensure_smtp_delivery_schema_compatible,
)
from app.services.integration_storage import (
    SMTP_INTEGRATION_TYPE,
    ActiveSMTPSettings,
    SMTPSecretError,
    acquire_smtp_configuration_read_lock,
    build_active_smtp_settings,
    smtp_instance_is_archived,
)
from app.services.smtp_legacy_alert_snapshot import (
    SMTPLegacyAlertSnapshot,
)
from app.services.smtp_delivery_heartbeat import (  # noqa: F401 - compatibility re-export
    persisted_smtp_settings_heartbeat,
)

_DELIVERY_SENDING = "sending"
SMTP_SOURCE_OWNER_IDS_KEY = "_threatlens_source_owner_user_ids"
# Schema 3 is owner-scoped. Legacy global schemas remain supported within the
# same 100-owner bound as their combined alert context.
_SMTP_ALERT_SOURCE_OWNER_CAP_BY_SCHEMA = {1: 100, 2: 100, 3: 1}


def smtp_alert_event_source_owner_ids(
    db: Session,
    *,
    event: IntegrationEvent,
) -> frozenset[uuid.UUID]:
    """Resolve bounded source ownership while routing SMTP alert events."""

    if event.event_type != "alert_match":
        return frozenset()
    payload = _alert_payload(event.payload_json, label="event")
    schema_version = _supported_alert_schema_version(
        event.schema_version, label="event"
    )
    _validate_snapshot_schema_envelope(
        payload,
        schema_version=schema_version,
        label="event",
    )
    if schema_version == 1:
        owner_id = _optional_owner_id(payload, label="event")
        if owner_id is not None:
            return frozenset({owner_id})
        return _legacy_alert_event_source_owner_ids(db, event=event, payload=payload)
    return _snapshot_event_owner_ids(event, payload=payload)


def smtp_legacy_alert_event_snapshot(
    db: Session,
    *,
    event: IntegrationEvent,
    owner_ids: frozenset[uuid.UUID],
) -> SMTPLegacyAlertSnapshot:
    """Load bounded immutable resources and owner contexts for a v1 event."""

    if not owner_ids:
        raise _source_context_error(
            "smtp_source_owner_context_missing",
            "Legacy SMTP alert event has no source owner.",
        )
    payload = _alert_payload(event.payload_json, label="event")
    item_id = _required_owner_context_uuid(payload.get("item_id"), label="item_id")
    rows = list(
        db.scalars(
            select(AlertOccurrence)
            .where(
                AlertOccurrence.integration_event_id == event.id,
                AlertOccurrence.item_id_snapshot == item_id,
                AlertOccurrence.owner_user_id.in_(owner_ids),
            )
            .order_by(AlertOccurrence.owner_user_id.asc(), AlertOccurrence.id.asc())
            .limit(_SMTP_LEGACY_ALERT_OCCURRENCE_CAP + 1)
        ).all()
    )
    if len(rows) > _SMTP_LEGACY_ALERT_OCCURRENCE_CAP:
        raise _source_context_error(
            "smtp_source_owner_context_too_large",
            "Legacy SMTP alert occurrence context exceeds the supported 500-row limit.",
        )
    event_owner_id = _optional_owner_id(payload, label="event")
    if not rows and event_owner_id is not None and owner_ids == {event_owner_id}:
        item, feed = _legacy_resource_snapshot(
            payload,
            item_id=item_id,
            feed_id=_optional_owner_context_uuid(
                payload.get("feed_id"), label="feed_id"
            ),
        )
        return SMTPLegacyAlertSnapshot(
            item,
            feed,
            {event_owner_id: _legacy_payload_alert_context(payload)},
        )
    contexts = _legacy_occurrence_contexts(rows)
    if set(contexts) != set(owner_ids):
        raise _source_context_error(
            "smtp_source_owner_context_missing",
            "Legacy SMTP alert event is missing immutable occurrence context for a source owner.",
        )
    item, feed = _legacy_occurrence_resources(
        rows,
        item_id=item_id,
        feed_id=_optional_owner_context_uuid(payload.get("feed_id"), label="feed_id"),
    )
    return SMTPLegacyAlertSnapshot(item, feed, contexts)


def _legacy_occurrence_contexts(
    rows: list[AlertOccurrence],
) -> dict[uuid.UUID, AlertMatchContext]:
    contexts: dict[uuid.UUID, AlertMatchContext] = {}
    for occurrence in rows:
        existing = contexts.get(occurrence.owner_user_id)
        names = list(existing.names) if existing is not None else []
        categories = list(existing.categories) if existing is not None else []
        keywords = list(existing.matched_keywords) if existing is not None else []
        if len(names) < _SMTP_ALERT_NAME_CAP:
            names.append(occurrence.alert_name_snapshot)
            categories.append(occurrence.alert_category_snapshot)
        raw_keywords = occurrence.matched_keywords
        if not isinstance(raw_keywords, list):
            raise _source_context_error(
                "smtp_source_owner_context_invalid",
                "Legacy SMTP alert occurrence has invalid matched-keyword context.",
            )
        for keyword in raw_keywords[:_SMTP_ALERT_KEYWORD_CAP]:
            if not isinstance(keyword, str):
                raise _source_context_error(
                    "smtp_source_owner_context_invalid",
                    "Legacy SMTP alert occurrence has invalid matched-keyword context.",
                )
            if keyword not in keywords and len(keywords) < _SMTP_ALERT_KEYWORD_CAP:
                keywords.append(keyword)
        contexts[occurrence.owner_user_id] = AlertMatchContext(
            count=(existing.count if existing is not None else 0) + 1,
            primary_name=(
                existing.primary_name
                if existing is not None
                else occurrence.alert_name_snapshot
            ),
            names=names,
            categories=categories,
            matched_keywords=keywords,
        )
    return contexts


def _legacy_occurrence_resources(
    rows: list[AlertOccurrence],
    *,
    item_id: uuid.UUID,
    feed_id: uuid.UUID | None,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    resources: tuple[SimpleNamespace, SimpleNamespace] | None = None
    for occurrence in rows:
        candidate = _legacy_resource_snapshot(
            occurrence.source_snapshot_json,
            item_id=item_id,
            feed_id=feed_id,
        )
        if resources is None:
            resources = candidate
        elif resources != candidate:
            raise _source_context_error(
                "smtp_source_snapshot_mismatch",
                "Legacy SMTP alert occurrences contain inconsistent immutable item or feed snapshots.",
            )
    if resources is None:
        raise _source_context_error(
            "smtp_source_snapshot_missing",
            "Legacy SMTP alert event has no immutable item and feed snapshot. Replay the alert from a current ThreatLens event.",
        )
    return resources


def _legacy_resource_snapshot(
    value: object,
    *,
    item_id: uuid.UUID,
    feed_id: uuid.UUID | None,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    if not isinstance(value, dict):
        raise _source_context_error(
            "smtp_source_snapshot_missing",
            "Legacy SMTP alert event has no immutable item and feed snapshot. Replay the alert from a current ThreatLens event.",
        )
    item_snapshot = value.get("item")
    feed_snapshot = value.get("feed")
    if not isinstance(item_snapshot, dict) or not isinstance(feed_snapshot, dict):
        raise _source_context_error(
            "smtp_source_snapshot_missing",
            "Legacy SMTP alert event has no immutable item and feed snapshot. Replay the alert from a current ThreatLens event.",
        )
    snapshot_item_id = _required_owner_context_uuid(
        item_snapshot.get("id"), label="source_snapshot.item.id"
    )
    snapshot_feed_id = _required_owner_context_uuid(
        feed_snapshot.get("id"), label="source_snapshot.feed.id"
    )
    embedded_feed_id = _optional_owner_context_uuid(
        item_snapshot.get("feed_id"), label="source_snapshot.item.feed_id"
    )
    if snapshot_item_id != item_id or (
        feed_id is not None and snapshot_feed_id != feed_id
    ):
        raise _source_context_error(
            "smtp_source_snapshot_mismatch",
            "Legacy SMTP alert item or feed snapshot does not match its source event.",
        )
    if embedded_feed_id is not None and embedded_feed_id != snapshot_feed_id:
        raise _source_context_error(
            "smtp_source_snapshot_mismatch",
            "Legacy SMTP alert item snapshot references a different feed.",
        )
    item = SimpleNamespace(
        id=snapshot_item_id,
        feed_id=snapshot_feed_id,
        title=_required_snapshot_text(
            item_snapshot.get("title"), label="item.title", limit=512
        ),
        summary=_optional_snapshot_text(
            item_snapshot.get("summary"), label="item.summary", limit=2_000
        ),
        url=_required_snapshot_text(
            item_snapshot.get("url"), label="item.url", limit=2_048
        ),
        canonical_url=_optional_snapshot_text(
            item_snapshot.get("canonical_url"),
            label="item.canonical_url",
            limit=2_048,
        ),
        published_at=_optional_snapshot_datetime(
            item_snapshot.get("published_at"), label="item.published_at"
        ),
        first_seen_at=_optional_snapshot_datetime(
            item_snapshot.get("first_seen_at"), label="item.first_seen_at"
        ),
        status=_required_snapshot_text(
            item_snapshot.get("status"), label="item.status", limit=32
        ),
    )
    feed = SimpleNamespace(
        id=snapshot_feed_id,
        name=_required_snapshot_text(
            feed_snapshot.get("name"), label="feed.name", limit=255
        ),
        url=_required_snapshot_text(
            feed_snapshot.get("url"), label="feed.url", limit=2_048
        ),
        site_url=None,
        error_count=0,
        last_error=None,
        last_fetch_at=None,
        last_success_at=None,
    )
    return item, feed


def _required_snapshot_text(value: object, *, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise _source_context_error(
            "smtp_source_snapshot_invalid",
            f"Legacy SMTP alert snapshot has invalid {label}.",
        )
    return value


def _optional_snapshot_text(
    value: object,
    *,
    label: str,
    limit: int,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise _source_context_error(
            "smtp_source_snapshot_invalid",
            f"Legacy SMTP alert snapshot has invalid {label}.",
        )
    return value


def _optional_snapshot_datetime(value: object, *, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise _source_context_error(
            "smtp_source_snapshot_invalid",
            f"Legacy SMTP alert snapshot has invalid {label}.",
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _source_context_error(
            "smtp_source_snapshot_invalid",
            f"Legacy SMTP alert snapshot has invalid {label}.",
        ) from exc
    if parsed.tzinfo is None:
        raise _source_context_error(
            "smtp_source_snapshot_invalid",
            f"Legacy SMTP alert snapshot has invalid {label}.",
        )
    return parsed.astimezone(timezone.utc)


def smtp_legacy_alert_delivery_snapshot(
    db: Session,
    *,
    delivery: IntegrationDelivery,
) -> SMTPLegacyAlertSnapshot:
    payload = _alert_payload(delivery.payload_json, label="delivery")
    event = _load_delivery_event(db, delivery=delivery)
    fallback_schema_version = event.schema_version if event is not None else 1
    schema_version = _supported_alert_schema_version(
        payload.get("schema_version", fallback_schema_version),
        label="delivery",
    )
    if delivery.event_type != "alert_match" or schema_version != 1:
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            "SMTP delivery is not a legacy schema-1 alert delivery.",
        )
    owner_ids = _persisted_smtp_delivery_source_owner_ids(delivery=delivery)
    if event is not None:
        return smtp_legacy_alert_event_snapshot(
            db,
            event=event,
            owner_ids=owner_ids,
        )
    item_id = _required_owner_context_uuid(payload.get("item_id"), label="item_id")
    item, feed = _legacy_resource_snapshot(
        payload,
        item_id=item_id,
        feed_id=_optional_owner_context_uuid(payload.get("feed_id"), label="feed_id"),
    )
    context = _legacy_payload_alert_context(payload)
    return SMTPLegacyAlertSnapshot(
        item,
        feed,
        {owner_id: context for owner_id in owner_ids},
    )


def persist_smtp_delivery_source_owner_context(
    db: Session,
    *,
    delivery: IntegrationDelivery,
) -> frozenset[uuid.UUID]:
    """Validate and backfill source ownership for an already-claimed delivery."""

    owner_ids = _smtp_delivery_source_owner_ids(db, delivery=delivery)
    _validate_personal_delivery_source_owner(delivery, owner_ids=owner_ids)
    if delivery.event_type != "alert_match":
        return owner_ids
    payload = _alert_payload(delivery.payload_json, label="delivery")
    if SMTP_SOURCE_OWNER_IDS_KEY not in payload:
        payload[SMTP_SOURCE_OWNER_IDS_KEY] = [
            str(owner_id) for owner_id in sorted(owner_ids, key=str)
        ]
        delivery.payload_json = payload
        db.add(delivery)
        db.flush()
    return owner_ids


def lock_smtp_delivery_external_io_eligibility(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
    expected_settings: ActiveSMTPSettings,
) -> None:
    """Fence SMTP configuration and lease changes at the side-effect boundary.

    The shared advisory lock remains transaction-scoped. Configuration writers
    take its exclusive counterpart, so callers must commit or roll back only
    after the next SMTP operation has completed.
    """

    acquire_smtp_configuration_read_lock(db)
    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        raise SMTPDeliveryIneligibleError(
            "smtp_delivery_missing", "SMTP delivery no longer exists."
        )
    if (
        delivery.connector_type != SMTP_INTEGRATION_TYPE
        or delivery.state != _DELIVERY_SENDING
        or int(delivery.attempt_count or 0) != int(expected_attempt_number)
    ):
        raise RuntimeError("SMTP delivery lease is no longer owned by this worker")

    instance = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == delivery.integration_id)
        .execution_options(populate_existing=True)
    )
    if instance is None or instance.integration_type != SMTP_INTEGRATION_TYPE:
        raise SMTPDeliveryIneligibleError(
            "smtp_integration_missing", "SMTP integration no longer exists."
        )
    if not instance.enabled or smtp_instance_is_archived(instance):
        raise SMTPDeliveryIneligibleError(
            "smtp_integration_disabled", "SMTP integration is disabled."
        )

    subscription = db.scalar(
        select(IntegrationSubscription)
        .where(IntegrationSubscription.id == delivery.subscription_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        subscription is None
        or subscription.integration_id != instance.id
        or subscription.event_type != delivery.event_type
        or not subscription.enabled
    ):
        raise SMTPDeliveryIneligibleError(
            "smtp_subscription_disabled",
            "SMTP event subscription is disabled or no longer exists.",
        )

    credential_source = _lock_credential_source(db, instance=instance)
    ensure_smtp_config_schema_compatible(
        instance=instance,
        credential_source=credential_source,
    )
    try:
        current_settings = build_active_smtp_settings(
            instance,
            credential_source=credential_source,
        )
    except SMTPSecretError as exc:
        raise SMTPDeliveryIneligibleError(
            "smtp_configuration_invalid",
            "SMTP credentials changed and the current configuration is invalid.",
        ) from exc
    if current_settings != expected_settings:
        raise SMTPDeliveryIneligibleError(
            "smtp_configuration_changed",
            "SMTP configuration changed after this delivery was claimed.",
        )

    report_owner_id = (
        _report_ready_delivery_owner_id(db, delivery=delivery)
        if delivery.event_type == "report_ready"
        else None
    )
    owner_matches_instance = delivery.owner_user_id == instance.owner_user_id
    global_report_delivery = (
        instance.owner_user_id is None
        and report_owner_id is not None
        and delivery.owner_user_id == report_owner_id
    )
    if not owner_matches_instance and not global_report_delivery:
        raise SMTPDeliveryIneligibleError(
            "smtp_owner_mismatch",
            "SMTP delivery owner no longer matches its integration owner.",
        )
    source_owner_ids = _persisted_smtp_delivery_source_owner_ids(delivery=delivery)
    owner_ids = set(source_owner_ids)
    if instance.owner_user_id is not None:
        owner_ids.add(instance.owner_user_id)
    if report_owner_id is not None:
        owner_ids.add(report_owner_id)
    owners = {
        owner.id: owner
        for owner in db.scalars(
            select(User)
            .where(User.id.in_(owner_ids))
            .order_by(User.id.asc())
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        ).all()
    }
    integration_owner = (
        owners.get(instance.owner_user_id)
        if instance.owner_user_id is not None
        else None
    )
    if instance.owner_user_id is not None and (
        integration_owner is None
        or not integration_owner.is_active
        or not integration_owner.is_approved
    ):
        error_type = (
            SMTPDeliveryTemporarilyIneligibleError
            if report_owner_id is not None
            else SMTPDeliveryIneligibleError
        )
        raise error_type(
            "smtp_owner_not_eligible",
            "SMTP owner is no longer active and approved for outbound delivery.",
        )
    if any(
        owner_id not in owners
        or not owners[owner_id].is_active
        or not owners[owner_id].is_approved
        for owner_id in source_owner_ids
    ):
        raise SMTPDeliveryIneligibleError(
            "smtp_source_owner_not_eligible",
            "An alert source owner is no longer active and approved for outbound delivery.",
        )
    if report_owner_id is not None:
        report_owner = owners.get(report_owner_id)
        if (
            report_owner is None
            or not report_owner.is_active
            or not report_owner.is_approved
        ):
            raise SMTPDeliveryTemporarilyIneligibleError(
                "smtp_report_owner_not_eligible",
                "Report owner is temporarily inactive or unapproved for outbound delivery.",
            )


def _report_ready_delivery_owner_id(
    db: Session,
    *,
    delivery: IntegrationDelivery,
) -> uuid.UUID:
    if delivery.event_id is None:
        raise SMTPDeliveryIneligibleError(
            "smtp_report_event_missing",
            "SMTP report delivery is missing its source event.",
        )
    event = db.scalar(
        select(IntegrationEvent)
        .where(IntegrationEvent.id == delivery.event_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if event is None or event.event_type != "report_ready":
        raise SMTPDeliveryIneligibleError(
            "smtp_report_event_missing",
            "SMTP report delivery source event no longer exists.",
        )
    try:
        return validate_report_ready_delivery_owner(
            db,
            event=event,
            delivery_payload=delivery.payload_json,
            delivery_owner_user_id=delivery.owner_user_id,
            require_eligible=False,
        )
    except ValueError as exc:
        raise SMTPDeliveryIneligibleError(
            "smtp_report_owner_context_invalid",
            "SMTP report delivery has invalid owner context.",
        ) from exc


def _smtp_delivery_source_owner_ids(
    db: Session,
    *,
    delivery: IntegrationDelivery,
) -> frozenset[uuid.UUID]:
    if delivery.event_type != "alert_match":
        return frozenset()

    payload = _alert_payload(delivery.payload_json, label="delivery")
    event = _load_delivery_event(db, delivery=delivery)
    fallback_schema_version = event.schema_version if event is not None else 1
    schema_version = _supported_alert_schema_version(
        payload.get("schema_version", fallback_schema_version),
        label="delivery",
    )
    _validate_snapshot_schema_envelope(
        payload,
        schema_version=schema_version,
        label="delivery",
    )
    if SMTP_SOURCE_OWNER_IDS_KEY in payload:
        owner_ids = _owner_id_list(
            payload.get(SMTP_SOURCE_OWNER_IDS_KEY),
            schema_version=schema_version,
            label="delivery",
        )
        _validate_delivery_owner_evidence(
            db,
            delivery_payload=payload,
            delivery_schema_version=schema_version,
            owner_ids=owner_ids,
            event=event,
        )
        return owner_ids

    payload_owner_id = _optional_owner_id(payload, label="delivery")
    if event is not None:
        event_payload = _alert_payload(event.payload_json, label="event")
        event_schema_version = _supported_alert_schema_version(
            event.schema_version, label="event"
        )
        if event_schema_version >= 2:
            event_owner_ids = _snapshot_event_owner_ids(event, payload=event_payload)
            if payload_owner_id is not None:
                if payload_owner_id not in event_owner_ids:
                    raise _source_context_error(
                        "smtp_source_owner_context_mismatch",
                        "SMTP alert delivery owner does not match its source event.",
                    )
                return frozenset({payload_owner_id})
            return event_owner_ids

        event_owner_id = _optional_owner_id(event_payload, label="event")
        if payload_owner_id is not None and event_owner_id is not None:
            if payload_owner_id != event_owner_id:
                raise _source_context_error(
                    "smtp_source_owner_context_mismatch",
                    "SMTP alert delivery owner does not match its legacy source event.",
                )
        resolved_owner_id = payload_owner_id or event_owner_id
        if resolved_owner_id is not None:
            return frozenset({resolved_owner_id})
        return _legacy_alert_event_source_owner_ids(
            db,
            event=event,
            payload=event_payload or payload,
        )

    if payload_owner_id is not None:
        return frozenset({payload_owner_id})
    if schema_version >= 2 and "alert_matches" in payload:
        return _snapshot_payload_owner_ids(
            payload,
            schema_version=schema_version,
            label="delivery",
        )
    raise _source_context_error(
        "smtp_source_owner_context_missing",
        "Legacy SMTP alert delivery has no immutable source-owner evidence. Replay the alert from a current ThreatLens event.",
    )


def _persisted_smtp_delivery_source_owner_ids(
    *,
    delivery: IntegrationDelivery,
) -> frozenset[uuid.UUID]:
    if delivery.event_type != "alert_match":
        return frozenset()
    payload = _alert_payload(delivery.payload_json, label="delivery")
    schema_version = _supported_alert_schema_version(
        payload.get("schema_version", 1), label="delivery"
    )
    _validate_snapshot_schema_envelope(
        payload,
        schema_version=schema_version,
        label="delivery",
    )
    if SMTP_SOURCE_OWNER_IDS_KEY not in payload:
        raise _source_context_error(
            "smtp_source_owner_context_missing",
            "SMTP alert delivery is missing its persisted source-owner evidence.",
        )
    owner_ids = _owner_id_list(
        payload[SMTP_SOURCE_OWNER_IDS_KEY],
        schema_version=schema_version,
        label="delivery",
    )
    payload_owner_id = _optional_owner_id(payload, label="delivery")
    if schema_version >= 3:
        if payload_owner_id is None or owner_ids != {payload_owner_id}:
            raise _source_context_error(
                "smtp_source_owner_context_mismatch",
                "SMTP schema-3 alert delivery has inconsistent source ownership.",
            )
    elif payload_owner_id is not None and owner_ids != {payload_owner_id}:
        raise _source_context_error(
            "smtp_source_owner_context_mismatch",
            "SMTP alert delivery owner is absent from its source-owner context.",
        )
    if "alert_matches" in payload:
        snapshot_owner_ids = _snapshot_payload_owner_ids(
            payload,
            schema_version=schema_version,
            label="delivery",
        )
        if owner_ids != snapshot_owner_ids:
            raise _source_context_error(
                "smtp_source_owner_context_mismatch",
                "SMTP alert delivery source owners do not match its persisted snapshot.",
            )
    _validate_personal_delivery_source_owner(delivery, owner_ids=owner_ids)
    return owner_ids


def _validate_personal_delivery_source_owner(
    delivery: IntegrationDelivery,
    *,
    owner_ids: frozenset[uuid.UUID],
) -> None:
    if delivery.event_type != "alert_match" or delivery.owner_user_id is None:
        return
    if owner_ids != {delivery.owner_user_id}:
        raise _source_context_error(
            "smtp_source_owner_context_mismatch",
            "Personal SMTP alert delivery source ownership does not match its integration owner.",
        )


def _load_delivery_event(
    db: Session, *, delivery: IntegrationDelivery
) -> IntegrationEvent | None:
    if delivery.event_id is None:
        return None
    event = db.scalar(
        select(IntegrationEvent)
        .where(IntegrationEvent.id == delivery.event_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if event is None:
        raise _source_context_error(
            "smtp_source_owner_context_missing",
            "SMTP alert delivery source event no longer exists.",
        )
    if event.event_type != "alert_match":
        raise _source_context_error(
            "smtp_source_owner_context_mismatch",
            "SMTP alert delivery is linked to a different event type.",
        )
    return event


def _validate_delivery_owner_evidence(
    db: Session,
    *,
    delivery_payload: dict,
    delivery_schema_version: int,
    owner_ids: frozenset[uuid.UUID],
    event: IntegrationEvent | None,
) -> None:
    payload_owner_id = _optional_owner_id(delivery_payload, label="delivery")
    if delivery_schema_version >= 3:
        if payload_owner_id is None or owner_ids != {payload_owner_id}:
            raise _source_context_error(
                "smtp_source_owner_context_mismatch",
                "SMTP schema-3 alert delivery has inconsistent source ownership.",
            )
    elif payload_owner_id is not None and owner_ids != {payload_owner_id}:
        raise _source_context_error(
            "smtp_source_owner_context_mismatch",
            "SMTP alert delivery owner is absent from its source-owner context.",
        )

    if "alert_matches" in delivery_payload:
        payload_owner_ids = _snapshot_payload_owner_ids(
            delivery_payload,
            schema_version=delivery_schema_version,
            label="delivery",
        )
        if owner_ids != payload_owner_ids:
            raise _source_context_error(
                "smtp_source_owner_context_mismatch",
                "SMTP alert delivery source owners do not match its persisted snapshot.",
            )

    if event is None:
        if delivery_schema_version == 1 and payload_owner_id is None:
            raise _source_context_error(
                "smtp_source_owner_context_missing",
                "Legacy SMTP alert delivery has no immutable source-owner evidence. Replay the alert from a current ThreatLens event.",
            )
        elif (
            delivery_schema_version == 2
            and payload_owner_id is None
            and "alert_matches" not in delivery_payload
        ):
            raise _source_context_error(
                "smtp_source_owner_context_missing",
                "SMTP alert delivery has no independently verifiable source-owner context.",
            )
        return
    event_payload = _alert_payload(event.payload_json, label="event")
    event_schema_version = _supported_alert_schema_version(
        event.schema_version, label="event"
    )
    _validate_snapshot_schema_envelope(
        event_payload,
        schema_version=event_schema_version,
        label="event",
    )
    if event_schema_version >= 2:
        if delivery_schema_version != event_schema_version:
            raise _source_context_error(
                "smtp_source_owner_context_mismatch",
                "SMTP alert delivery schema does not match its source event.",
            )
        event_owner_ids = _snapshot_event_owner_ids(event, payload=event_payload)
        if not owner_ids.issubset(event_owner_ids):
            raise _source_context_error(
                "smtp_source_owner_context_mismatch",
                "SMTP alert delivery source owners do not match its source event.",
            )
        return
    event_owner_id = _optional_owner_id(event_payload, label="event")
    if event_owner_id is not None and owner_ids != {event_owner_id}:
        raise _source_context_error(
            "smtp_source_owner_context_mismatch",
            "SMTP alert delivery source owners do not match its legacy source event.",
        )
    if event_owner_id is None:
        expected_owner_ids = _legacy_alert_event_source_owner_ids(
            db,
            event=event,
            payload=event_payload or delivery_payload,
        )
        if not owner_ids.issubset(expected_owner_ids):
            raise _source_context_error(
                "smtp_source_owner_context_mismatch",
                "SMTP alert delivery source owners do not match its legacy source event.",
            )


def _snapshot_event_owner_ids(
    event: IntegrationEvent,
    *,
    payload: dict,
) -> frozenset[uuid.UUID]:
    schema_version = _supported_alert_schema_version(
        event.schema_version, label="event"
    )
    _validate_snapshot_schema_envelope(
        payload,
        schema_version=schema_version,
        label="event",
    )
    _validate_raw_snapshot_cardinality(
        payload,
        schema_version=schema_version,
        label="event",
    )
    from app.services.integration_events import alert_match_event_owner_ids

    try:
        owner_ids = alert_match_event_owner_ids(event)
    except ValueError as exc:
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            "SMTP alert source event has invalid source-owner context.",
        ) from exc
    if owner_ids is None:
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            "SMTP alert source event has no snapshot owner context.",
        )
    _validate_owner_count(owner_ids, schema_version=schema_version, label="event")
    entries = payload.get("alert_matches")
    if isinstance(entries, list) and len(entries) != len(owner_ids):
        raise _source_context_error(
            "smtp_source_owner_context_mismatch",
            "SMTP alert source event contains duplicate owner context.",
        )
    if not owner_ids:
        raise _source_context_error(
            "smtp_source_owner_context_missing",
            "SMTP alert source event has no source owner.",
        )
    return owner_ids


def _snapshot_payload_owner_ids(
    payload: dict,
    *,
    schema_version: int,
    label: str,
) -> frozenset[uuid.UUID]:
    _validate_raw_snapshot_cardinality(
        payload,
        schema_version=schema_version,
        label=label,
    )
    entries = payload.get("alert_matches")
    if not isinstance(entries, list):
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} has invalid alert_matches context.",
        )
    owner_ids: list[uuid.UUID] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise _source_context_error(
                "smtp_source_owner_context_invalid",
                f"SMTP alert {label} has invalid alert_matches context.",
            )
        owner_id = _optional_owner_id(entry, label=f"{label} alert match")
        if owner_id is None:
            raise _source_context_error(
                "smtp_source_owner_context_invalid",
                f"SMTP alert {label} match is missing its source owner.",
            )
        owner_ids.append(owner_id)
    if len(set(owner_ids)) != len(owner_ids):
        raise _source_context_error(
            "smtp_source_owner_context_mismatch",
            f"SMTP alert {label} contains duplicate source owners.",
        )
    resolved = frozenset(owner_ids)
    if not resolved:
        raise _source_context_error(
            "smtp_source_owner_context_missing",
            f"SMTP alert {label} has no source owner.",
        )
    return resolved


def _legacy_alert_event_source_owner_ids(
    db: Session,
    *,
    event: IntegrationEvent,
    payload: dict,
) -> frozenset[uuid.UUID]:
    item_id = _required_owner_context_uuid(payload.get("item_id"), label="item_id")
    cap = _SMTP_ALERT_SOURCE_OWNER_CAP_BY_SCHEMA[1]
    owner_ids = list(
        db.scalars(
            select(AlertOccurrence.owner_user_id)
            .where(
                AlertOccurrence.integration_event_id == event.id,
                AlertOccurrence.item_id_snapshot == item_id,
            )
            .distinct()
            .order_by(AlertOccurrence.owner_user_id.asc())
            .limit(cap + 1)
        ).all()
    )
    if len(owner_ids) > cap:
        raise _source_context_error(
            "smtp_source_owner_context_too_large",
            f"Legacy SMTP alert source-owner context exceeds the supported {cap}-owner limit.",
        )
    if not owner_ids:
        raise _source_context_error(
            "smtp_source_owner_context_missing",
            "Legacy SMTP alert event has no immutable occurrence evidence. Replay the alert from a current ThreatLens event.",
        )
    return frozenset(owner_ids)


def _owner_id_list(
    value: object,
    *,
    schema_version: int,
    label: str,
) -> frozenset[uuid.UUID]:
    if not isinstance(value, list) or not value:
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} has invalid source-owner context.",
        )
    cap = _SMTP_ALERT_SOURCE_OWNER_CAP_BY_SCHEMA[schema_version]
    if len(value) > cap:
        raise _source_context_error(
            "smtp_source_owner_context_too_large",
            f"SMTP alert {label} source-owner context exceeds the supported {cap}-owner limit.",
        )
    if any(not isinstance(entry, str) for entry in value):
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} has invalid source-owner context.",
        )
    try:
        parsed = [uuid.UUID(entry) for entry in value]
    except ValueError as exc:
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} has invalid source-owner context.",
        ) from exc
    if len(set(parsed)) != len(parsed):
        raise _source_context_error(
            "smtp_source_owner_context_mismatch",
            f"SMTP alert {label} contains duplicate source owners.",
        )
    return frozenset(parsed)


def _validate_raw_snapshot_cardinality(
    payload: dict,
    *,
    schema_version: int,
    label: str,
) -> None:
    entries = payload.get("alert_matches")
    if not isinstance(entries, list):
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} has invalid alert_matches context.",
        )
    cap = _SMTP_ALERT_SOURCE_OWNER_CAP_BY_SCHEMA[schema_version]
    if len(entries) > cap:
        raise _source_context_error(
            "smtp_source_owner_context_too_large",
            f"SMTP alert {label} source-owner context exceeds the supported {cap}-owner limit.",
        )


def _validate_snapshot_schema_envelope(
    payload: dict,
    *,
    schema_version: int,
    label: str,
) -> None:
    if schema_version < 2:
        return
    if "schema_version" not in payload:
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} snapshot is missing its schema version.",
        )
    payload_schema_version = _supported_alert_schema_version(
        payload["schema_version"],
        label=f"{label} payload",
    )
    if payload_schema_version != schema_version:
        raise _source_context_error(
            "smtp_source_owner_context_mismatch",
            f"SMTP alert {label} payload schema does not match its envelope.",
        )


def _validate_owner_count(
    owner_ids: frozenset[uuid.UUID],
    *,
    schema_version: int,
    label: str,
) -> None:
    cap = _SMTP_ALERT_SOURCE_OWNER_CAP_BY_SCHEMA[schema_version]
    if len(owner_ids) > cap:
        raise _source_context_error(
            "smtp_source_owner_context_too_large",
            f"SMTP alert {label} source-owner context exceeds the supported {cap}-owner limit.",
        )


def _alert_payload(value: object, *, label: str) -> dict:
    if not isinstance(value, dict):
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} payload is invalid.",
        )
    return dict(value)


def _supported_alert_schema_version(value: object, *, label: str) -> int:
    if value is None:
        schema_version = 1
    elif type(value) is int:
        schema_version = value
    elif (
        isinstance(value, str)
        and len(value) <= 9
        and value.isascii()
        and value.isdigit()
        and value == str(int(value))
    ):
        schema_version = int(value)
    else:
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} has an invalid schema version.",
        )
    if schema_version > max(_SMTP_ALERT_SOURCE_OWNER_CAP_BY_SCHEMA):
        raise SMTPDeliverySourceCompatibilityError(
            "smtp_source_owner_context_unsupported",
            f"SMTP alert {label} uses newer schema version {schema_version}; routing will retry after this worker is upgraded.",
        )
    if schema_version not in _SMTP_ALERT_SOURCE_OWNER_CAP_BY_SCHEMA:
        raise _source_context_error(
            "smtp_source_owner_context_unsupported",
            f"SMTP alert {label} uses unsupported schema version {schema_version}.",
        )
    return schema_version


def _optional_owner_id(payload: dict, *, label: str) -> uuid.UUID | None:
    value = payload.get("owner_user_id")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} has an invalid owner_user_id.",
        )
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"SMTP alert {label} has an invalid owner_user_id.",
        ) from exc


def _required_owner_context_uuid(value: object, *, label: str) -> uuid.UUID:
    if value is None or value == "":
        raise _source_context_error(
            "smtp_source_owner_context_missing",
            f"Legacy SMTP alert delivery is missing {label}.",
        )
    if not isinstance(value, str):
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"Legacy SMTP alert delivery has invalid {label}.",
        )
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise _source_context_error(
            "smtp_source_owner_context_invalid",
            f"Legacy SMTP alert delivery has invalid {label}.",
        ) from exc


def _optional_owner_context_uuid(
    value: object,
    *,
    label: str,
) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    return _required_owner_context_uuid(value, label=label)


def _source_context_error(code: str, message: str) -> SMTPDeliverySourceContextError:
    return SMTPDeliverySourceContextError(code, message)


def _lock_credential_source(
    db: Session, *, instance: IntegrationInstance
) -> IntegrationInstance | None:
    source_id = instance.credential_source_integration_id
    if source_id is None:
        return None
    source = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == source_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        source is None
        or source.integration_type != SMTP_INTEGRATION_TYPE
        or smtp_instance_is_archived(source)
        or source.credential_source_integration_id is not None
    ):
        raise SMTPDeliveryIneligibleError(
            "smtp_credential_source_invalid",
            "The shared SMTP credential source is no longer available.",
        )
    return source
