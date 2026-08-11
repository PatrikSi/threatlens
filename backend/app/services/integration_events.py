from __future__ import annotations

from copy import deepcopy
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
)
from app.models.alert_interest import AlertInterest
from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.integration_connectors import IntegrationEventContextError
from app.services.integration_registry import (
    get_integration_connector,
    iter_integration_connectors_for_event,
    list_subscription_connector_types,
)
from app.services.notification_webhook_templates import AlertMatchContext

settings = get_settings()

EVENT_PENDING = "pending"
EVENT_ROUTING = "routing"
EVENT_ROUTED = "routed"
EVENT_FAILED = "failed"
EVENT_DEAD_LETTER = "dead_letter"
RESOURCE_SNAPSHOT_SCHEMA_VERSION = 2
RESOURCE_SNAPSHOT_EVENT_TYPES = frozenset({"rss_item_new", "alert_match", "feed_failing"})


@dataclass(frozen=True)
class ConnectorRoutingError:
    connector_type: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class RoutedIntegrationEvent:
    event_id: uuid.UUID
    status: str
    webhook_delivery_ids: list[uuid.UUID]
    integration_delivery_ids: list[uuid.UUID]
    routing_errors: tuple[ConnectorRoutingError, ...] = ()


@dataclass(frozen=True)
class IntegrationEventRecoveryReservation:
    event_ids: tuple[uuid.UUID, ...]
    reserved_at: datetime


@dataclass(frozen=True)
class IntegrationEventResources:
    item: Item | SimpleNamespace | None
    feed: Feed | SimpleNamespace | None
    alert_context: AlertMatchContext | None = None
    alert_contexts_by_owner: dict[uuid.UUID, AlertMatchContext] | None = None
    from_snapshot: bool = False

    def alert_context_for_owner(self, owner_user_id: uuid.UUID | None) -> AlertMatchContext | None:
        if owner_user_id is None:
            return self.alert_context
        return (self.alert_contexts_by_owner or {}).get(owner_user_id)


def emit_integration_event(
    db: Session,
    *,
    event_type: str,
    source_type: str,
    source_id: str | uuid.UUID | None,
    idempotency_key: str,
    payload: dict,
    schema_version: int | None = None,
    actor_user_id: uuid.UUID | None = None,
    available_at: datetime | None = None,
) -> IntegrationEvent:
    existing = db.scalar(select(IntegrationEvent).where(IntegrationEvent.idempotency_key == idempotency_key))
    if existing is not None:
        return existing

    resolved_schema_version, resolved_payload = _prepare_event_envelope(
        db,
        event_type=event_type,
        payload=payload,
        requested_schema_version=schema_version,
    )
    event = IntegrationEvent(
        event_type=event_type,
        schema_version=resolved_schema_version,
        source_type=source_type,
        source_id=str(source_id) if source_id is not None else None,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        payload_json=resolved_payload,
        routing_state=EVENT_PENDING,
        available_at=available_at or datetime.now(timezone.utc),
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(IntegrationEvent).where(IntegrationEvent.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing
    return event


def route_integration_event(db: Session, *, event_id: uuid.UUID) -> RoutedIntegrationEvent:
    event = db.scalar(
        select(IntegrationEvent)
        .where(IntegrationEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        raise IntegrationEventContextError("Integration event not found")
    if event.routing_state == EVENT_ROUTED:
        return _routed_event_result(db, event)
    if event.routing_state == EVENT_DEAD_LETTER:
        return _event_delivery_result(db, event, status=EVENT_DEAD_LETTER)

    event.routing_state = EVENT_ROUTING
    event.claimed_at = datetime.now(timezone.utc)
    event.routing_attempt_count = max(0, int(event.routing_attempt_count or 0)) + 1
    event.last_error = None
    db.add(event)

    db.flush()

    errors: list[ConnectorRoutingError] = []
    preparation_failures: set[str] = set()
    for connector in iter_integration_connectors_for_event(event.event_type):
        try:
            with db.begin_nested():
                connector.prepare_routing(db, event=event)
        except Exception as exc:
            connector_type = connector.definition.integration_type
            preparation_failures.add(connector_type)
            errors.append(
                ConnectorRoutingError(
                    connector_type=connector_type,
                    message=f"subscription preparation failed: {type(exc).__name__}: {exc}"[:1000],
                    retryable=True,
                )
            )

    for connector_type in list_subscription_connector_types(db, event_type=event.event_type):
        connector = get_integration_connector(connector_type)
        if connector is None:
            errors.append(
                ConnectorRoutingError(
                    connector_type=connector_type,
                    message=(
                        f"connector {connector_type!r} is not available on this worker; "
                        "routing will retry after the worker is upgraded"
                    ),
                    retryable=True,
                )
            )
            continue
        if connector_type in preparation_failures:
            continue
        if not connector.supports_event_type(event.event_type):
            errors.append(
                ConnectorRoutingError(
                    connector_type=connector_type,
                    message=(
                        f"connector {connector_type!r} does not support persisted event type "
                        f"{event.event_type!r}; update the subscription or connector"
                    ),
                    retryable=True,
                )
            )
            continue
        try:
            with db.begin_nested():
                connector.route_event(db, event=event)
        except IntegrationEventContextError as exc:
            errors.append(ConnectorRoutingError(connector_type, str(exc)[:1000], False))
        except Exception as exc:
            errors.append(
                ConnectorRoutingError(
                    connector_type,
                    f"{type(exc).__name__}: {exc}"[:1000],
                    True,
                )
            )

    current_time = datetime.now(timezone.utc)
    if errors:
        retryable = any(error.retryable for error in errors)
        max_attempts = max(1, int(settings.integration_event_routing_max_attempts))
        event.routing_state = (
            EVENT_FAILED
            if retryable and event.routing_attempt_count < max_attempts
            else EVENT_DEAD_LETTER
        )
        event.last_error = _format_routing_errors(errors)
        event.available_at = current_time + timedelta(
            seconds=_routing_backoff_seconds(event.routing_attempt_count)
        )
        event.routed_at = None
    else:
        event.routing_state = EVENT_ROUTED
        event.routed_at = current_time
        event.last_error = None
    event.claimed_at = None
    db.add(event)
    db.flush()
    return _event_delivery_result(db, event, status=event.routing_state, errors=errors)


def record_integration_event_failure(
    db: Session,
    *,
    event_id: uuid.UUID,
    error: str,
    terminal: bool,
    now: datetime | None = None,
) -> IntegrationEvent | None:
    current_time = now or datetime.now(timezone.utc)
    event = db.scalar(select(IntegrationEvent).where(IntegrationEvent.id == event_id).with_for_update())
    if event is None or event.routing_state == EVENT_ROUTED:
        return event
    attempts = max(0, int(event.routing_attempt_count or 0)) + 1
    max_attempts = max(1, int(settings.integration_event_routing_max_attempts))
    dead_letter = terminal or attempts >= max_attempts
    event.routing_state = EVENT_DEAD_LETTER if dead_letter else EVENT_FAILED
    event.routing_attempt_count = attempts
    event.claimed_at = None
    event.last_error = error[:4000]
    event.available_at = current_time + timedelta(seconds=_routing_backoff_seconds(attempts))
    db.add(event)
    return event


def list_recoverable_integration_event_ids(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    current_time = now or datetime.now(timezone.utc)
    batch_size = max(1, int(limit or settings.integration_event_routing_batch_size))
    return list(
        db.scalars(
            select(IntegrationEvent.id)
            .where(_recoverable_event_predicate(current_time))
            .order_by(IntegrationEvent.available_at.asc(), IntegrationEvent.created_at.asc())
            .limit(batch_size)
        ).all()
    )


def reserve_recoverable_integration_events(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> IntegrationEventRecoveryReservation:
    """Reserve routing publication so concurrent sweeps do not amplify queue work."""
    current_time = now or datetime.now(timezone.utc)
    batch_size = max(1, int(limit or settings.integration_event_routing_batch_size))
    events = list(
        db.scalars(
            select(IntegrationEvent)
            .where(_recoverable_event_predicate(current_time))
            .order_by(
                IntegrationEvent.available_at.asc(),
                IntegrationEvent.created_at.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        ).all()
    )
    for event in events:
        event.claimed_at = current_time
        db.add(event)
    return IntegrationEventRecoveryReservation(
        event_ids=tuple(event.id for event in events),
        reserved_at=current_time,
    )


def release_integration_event_publications(
    db: Session,
    *,
    event_ids: list[uuid.UUID] | tuple[uuid.UUID, ...],
    reserved_at: datetime,
) -> None:
    if not event_ids:
        return
    events = db.scalars(
        select(IntegrationEvent)
        .where(IntegrationEvent.id.in_(event_ids))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for event in events:
        if (
            event.routing_state in {EVENT_PENDING, EVENT_FAILED}
            and _coerce_utc(event.claimed_at) == reserved_at
        ):
            event.claimed_at = None
            db.add(event)


def _recoverable_event_predicate(current_time: datetime):
    stale_after = max(10, int(settings.integration_event_routing_stale_after_seconds))
    stale_cutoff = current_time - timedelta(seconds=stale_after)
    return or_(
        and_(
            IntegrationEvent.routing_state.in_([EVENT_PENDING, EVENT_FAILED]),
            IntegrationEvent.available_at <= current_time,
            or_(
                IntegrationEvent.claimed_at.is_(None),
                IntegrationEvent.claimed_at < stale_cutoff,
            ),
        ),
        and_(
            IntegrationEvent.routing_state == EVENT_ROUTING,
            or_(
                IntegrationEvent.claimed_at.is_(None),
                IntegrationEvent.claimed_at < stale_cutoff,
            ),
        ),
    )


def _routed_event_result(db: Session, event: IntegrationEvent) -> RoutedIntegrationEvent:
    return _event_delivery_result(db, event, status=EVENT_ROUTED)


def _event_delivery_result(
    db: Session,
    event: IntegrationEvent,
    *,
    status: str,
    errors: list[ConnectorRoutingError] | None = None,
) -> RoutedIntegrationEvent:
    generic = db.scalars(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == event.id,
            IntegrationDelivery.delivery_kind == "live",
        )
    ).all()
    generic_ids = [delivery.id for delivery in generic]
    webhook_ids = list(
        db.scalars(
            select(NotificationWebhookDelivery.id).where(
                NotificationWebhookDelivery.integration_delivery_id.in_(generic_ids)
            )
        ).all()
    ) if generic_ids else []
    return RoutedIntegrationEvent(event.id, status, webhook_ids, generic_ids, tuple(errors or ()))


def hydrate_integration_event_resources(db: Session, *, event: IntegrationEvent) -> IntegrationEventResources:
    """Load legacy v1 references or materialize immutable v2 resource snapshots."""
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    return hydrate_integration_event_payload_resources(
        db,
        event_type=event.event_type,
        schema_version=int(event.schema_version or 1),
        payload=payload,
    )


def hydrate_integration_event_payload_resources(
    db: Session,
    *,
    event_type: str,
    schema_version: int,
    payload: dict,
) -> IntegrationEventResources:
    """Hydrate a persisted event or delivery payload using its declared envelope."""
    if schema_version >= RESOURCE_SNAPSHOT_SCHEMA_VERSION:
        payload_version = _coerce_schema_version(payload.get("schema_version"))
        if payload_version != schema_version:
            raise IntegrationEventContextError(
                f"{event_type} event payload schema_version does not match the event envelope"
            )
        item = (
            _namespace_from_item_snapshot(payload.get("item"))
            if event_type in {"rss_item_new", "alert_match"}
            else None
        )
        feed = _namespace_from_feed_snapshot(payload.get("feed"))
        alert_context, by_owner = (
            _alert_contexts_from_snapshot(payload)
            if event_type == "alert_match"
            else (None, {})
        )
        return IntegrationEventResources(
            item=item,
            feed=feed,
            alert_context=alert_context,
            alert_contexts_by_owner=by_owner,
            from_snapshot=True,
        )

    item_id = _payload_uuid(payload, "item_id", event_type=event_type, required=False)
    feed_id = _payload_uuid(payload, "feed_id", event_type=event_type, required=False)
    item = db.get(Item, item_id) if item_id is not None else None
    if item_id is not None and item is None:
        raise IntegrationEventContextError(f"Item {item_id} no longer exists")
    resolved_feed_id = feed_id or getattr(item, "feed_id", None)
    feed = db.get(Feed, resolved_feed_id) if resolved_feed_id is not None else None
    if resolved_feed_id is not None and feed is None:
        raise IntegrationEventContextError(f"Feed {resolved_feed_id} no longer exists")
    return IntegrationEventResources(item=item, feed=feed)


def delivery_payload_for_owner(event: IntegrationEvent, *, owner_user_id: uuid.UUID | None) -> dict:
    payload = deepcopy(event.payload_json) if isinstance(event.payload_json, dict) else {}
    if event.event_type != "alert_match" or int(event.schema_version or 1) < RESOURCE_SNAPSHOT_SCHEMA_VERSION:
        return payload
    _global, by_owner = _alert_contexts_from_snapshot(payload)
    selected = by_owner.get(owner_user_id) if owner_user_id is not None else _global
    payload["alert"] = _serialize_alert_context(selected) if selected is not None else None
    payload.pop("alert_matches", None)
    return payload


def _prepare_event_envelope(
    db: Session,
    *,
    event_type: str,
    payload: dict,
    requested_schema_version: int | None,
) -> tuple[int, dict]:
    copied = deepcopy(payload)
    if requested_schema_version is not None:
        version = max(1, int(requested_schema_version))
        copied.setdefault("schema_version", version)
        return version, copied
    if event_type not in RESOURCE_SNAPSHOT_EVENT_TYPES:
        return 1, copied

    try:
        snapshot = _build_resource_snapshot_payload(db, event_type=event_type, payload=copied)
    except IntegrationEventContextError:
        # Preserve legacy behavior for malformed or unresolved producers. The v1
        # router will report the context error if a matching subscription exists.
        return 1, copied
    if snapshot is None:
        return 1, copied
    snapshot["schema_version"] = RESOURCE_SNAPSHOT_SCHEMA_VERSION
    return RESOURCE_SNAPSHOT_SCHEMA_VERSION, snapshot


def _build_resource_snapshot_payload(db: Session, *, event_type: str, payload: dict) -> dict | None:
    item_id = _payload_uuid(payload, "item_id", event_type=event_type, required=False)
    feed_id = _payload_uuid(payload, "feed_id", event_type=event_type, required=False)
    item = db.get(Item, item_id) if item_id is not None else None
    if event_type in {"rss_item_new", "alert_match"} and item is None:
        return None
    resolved_feed_id = feed_id or getattr(item, "feed_id", None)
    feed = db.get(Feed, resolved_feed_id) if resolved_feed_id is not None else None
    if feed is None:
        return None

    snapshot = deepcopy(payload)
    snapshot["feed_id"] = str(feed.id)
    snapshot["feed"] = _serialize_feed(feed)
    if item is not None:
        snapshot["item_id"] = str(item.id)
        snapshot["item"] = _serialize_item(item)
    if event_type == "alert_match":
        from app.services.notification_webhooks import build_alert_match_context_for_item

        global_context = build_alert_match_context_for_item(db, item=item)
        snapshot["alert"] = _serialize_alert_context(global_context) if global_context is not None else None
        owner_contexts: list[dict] = []
        owner_ids = db.scalars(
            select(AlertInterest.user_id)
            .where(AlertInterest.enabled.is_(True))
            .distinct()
            .order_by(AlertInterest.user_id.asc())
        ).all()
        for owner_user_id in owner_ids:
            context = build_alert_match_context_for_item(db, user_id=owner_user_id, item=item)
            if context is None:
                continue
            owner_contexts.append(
                {"owner_user_id": str(owner_user_id), **_serialize_alert_context(context)}
            )
        snapshot["alert_matches"] = owner_contexts
    return snapshot


def _serialize_item(item: Item) -> dict:
    return {
        "id": str(item.id),
        "feed_id": str(item.feed_id),
        "title": item.title,
        "url": item.url,
        "canonical_url": item.canonical_url,
        "summary": item.summary,
        "published_at": _isoformat_optional(item.published_at),
        "first_seen_at": _isoformat_optional(item.first_seen_at),
        "status": item.status,
    }


def _serialize_feed(feed: Feed) -> dict:
    from app.services.url_utils import redact_feed_url

    return {
        "id": str(feed.id),
        "name": feed.name,
        "url": redact_feed_url(feed.url),
        "site_url": feed.site_url,
        "error_count": max(0, int(feed.error_count or 0)),
        "last_error": feed.last_error,
        "last_fetch_at": _isoformat_optional(feed.last_fetch_at),
        "last_success_at": _isoformat_optional(feed.last_success_at),
    }


def _serialize_alert_context(context: AlertMatchContext) -> dict:
    return {
        "count": max(0, int(context.count)),
        "primary_name": str(context.primary_name),
        "names": list(context.names),
        "categories": list(context.categories),
        "matched_keywords": list(context.matched_keywords),
    }


def _namespace_from_item_snapshot(value: object) -> SimpleNamespace:
    snapshot = _required_snapshot(value, label="item")
    item_id = _snapshot_uuid(snapshot, "id", label="item")
    feed_id = _snapshot_uuid(snapshot, "feed_id", label="item")
    return SimpleNamespace(
        id=item_id,
        feed_id=feed_id,
        title=str(snapshot.get("title") or ""),
        url=str(snapshot.get("url") or ""),
        canonical_url=_optional_text(snapshot.get("canonical_url")),
        summary=_optional_text(snapshot.get("summary")),
        published_at=_snapshot_datetime(snapshot.get("published_at"), label="item.published_at"),
        first_seen_at=_snapshot_datetime(snapshot.get("first_seen_at"), label="item.first_seen_at"),
        status=str(snapshot.get("status") or "new"),
    )


def _namespace_from_feed_snapshot(value: object) -> SimpleNamespace:
    snapshot = _required_snapshot(value, label="feed")
    try:
        error_count = max(0, int(snapshot.get("error_count") or 0))
    except (TypeError, ValueError) as exc:
        raise IntegrationEventContextError("Integration event has invalid feed.error_count") from exc
    return SimpleNamespace(
        id=_snapshot_uuid(snapshot, "id", label="feed"),
        name=str(snapshot.get("name") or ""),
        url=str(snapshot.get("url") or ""),
        site_url=_optional_text(snapshot.get("site_url")),
        error_count=error_count,
        last_error=_optional_text(snapshot.get("last_error")),
        last_fetch_at=_snapshot_datetime(snapshot.get("last_fetch_at"), label="feed.last_fetch_at"),
        last_success_at=_snapshot_datetime(snapshot.get("last_success_at"), label="feed.last_success_at"),
    )


def _alert_contexts_from_snapshot(
    payload: dict,
) -> tuple[AlertMatchContext | None, dict[uuid.UUID, AlertMatchContext]]:
    def parse(value: object, *, label: str) -> AlertMatchContext:
        snapshot = _required_snapshot(value, label=label)
        try:
            count = max(0, int(snapshot.get("count") or 0))
        except (TypeError, ValueError) as exc:
            raise IntegrationEventContextError(f"Integration event has invalid {label}.count") from exc
        return AlertMatchContext(
            count=count,
            primary_name=str(snapshot.get("primary_name") or ""),
            names=_snapshot_string_list(snapshot.get("names"), label=f"{label}.names"),
            categories=_snapshot_string_list(snapshot.get("categories"), label=f"{label}.categories"),
            matched_keywords=_snapshot_string_list(
                snapshot.get("matched_keywords"), label=f"{label}.matched_keywords"
            ),
        )

    global_context = parse(payload["alert"], label="alert") if isinstance(payload.get("alert"), dict) else None
    by_owner: dict[uuid.UUID, AlertMatchContext] = {}
    entries = payload.get("alert_matches", [])
    if not isinstance(entries, list):
        raise IntegrationEventContextError("Integration event has invalid alert_matches")
    for index, entry in enumerate(entries):
        snapshot = _required_snapshot(entry, label=f"alert_matches[{index}]")
        owner_id = _snapshot_uuid(snapshot, "owner_user_id", label=f"alert_matches[{index}]")
        by_owner[owner_id] = parse(snapshot, label=f"alert_matches[{index}]")
    return global_context, by_owner


def _required_snapshot(value: object, *, label: str) -> dict:
    if not isinstance(value, dict):
        raise IntegrationEventContextError(f"Integration event is missing {label} snapshot")
    return value


def _snapshot_uuid(snapshot: dict, key: str, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(snapshot.get(key)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntegrationEventContextError(f"Integration event has invalid {label}.{key}") from exc


def _payload_uuid(payload: dict, key: str, *, event_type: str, required: bool) -> uuid.UUID | None:
    value = payload.get(key)
    if value is None or value == "":
        if required:
            raise IntegrationEventContextError(f"{event_type} event is missing {key}")
        return None
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntegrationEventContextError(f"{event_type} event has invalid {key}") from exc


def _snapshot_datetime(value: object, *, label: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise IntegrationEventContextError(f"Integration event has invalid {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrationEventContextError(f"Integration event has invalid {label}") from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _snapshot_string_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
        raise IntegrationEventContextError(f"Integration event has invalid {label}")
    return list(value)


def _coerce_schema_version(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _isoformat_optional(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_routing_errors(errors: list[ConnectorRoutingError]) -> str:
    return "; ".join(
        f"{error.connector_type}: {error.message} ({'retryable' if error.retryable else 'terminal'})"
        for error in errors
    )[:4000]


def _routing_backoff_seconds(attempts: int) -> int:
    base = max(1, int(settings.integration_event_routing_backoff_seconds))
    return min(base * (2 ** max(0, attempts - 1)), 3600)
