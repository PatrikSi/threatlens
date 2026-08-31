from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.token_scopes import SCOPE_WRITE_NOTIFICATIONS
from app.models.ai_daily_brief import AIDailyBrief
from app.models.audit_log import AuditLog
from app.models.data_policy import HandlingLabel
from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.schemas.notification import NotificationWebhookTestResponse
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    fence_authorization_context,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    get_data_access_envelope,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyError,
    DataPolicyUnavailable,
    fence_data_access_context,
)
from app.services.data_policy_audit import record_data_policy_decision
from app.services.notification_webhook_requests import RenderedNotificationRequest


NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION = "notifications.webhook.test.receipt"
NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION = "notifications.webhook.test.outcome"
NOTIFICATION_WEBHOOK_TEST_RESOURCE_TYPE = "notification_webhook_test"
_RECEIPT_SCHEMA_VERSION = 1

NotificationWebhookTestPolicyDecision = Literal[
    "allowed",
    "egress_would_deny",
    "egress_denied",
    "egress_not_served",
]


class NotificationWebhookTestPolicyError(RuntimeError):
    status_code = 409
    code = "notification_webhook_test_policy_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotificationWebhookTestPolicyDenied(NotificationWebhookTestPolicyError):
    code = "notification_webhook_test_policy_denied"


class NotificationWebhookTestPolicyUnavailable(NotificationWebhookTestPolicyError):
    status_code = 503
    code = "notification_webhook_test_policy_unavailable"


class NotificationWebhookTestReplayConflict(NotificationWebhookTestPolicyError):
    code = "notification_webhook_test_replay_conflict"


class NotificationWebhookTestReplayUnsafe(NotificationWebhookTestPolicyError):
    code = "notification_webhook_test_replay_unsafe"


@dataclass(frozen=True, slots=True)
class NotificationWebhookTestSourceRefs:
    feed_id: uuid.UUID | None = None
    item_id: uuid.UUID | None = None
    daily_brief_id: uuid.UUID | None = None

    @property
    def data_access_governed(self) -> bool:
        return any(
            value is not None
            for value in (self.feed_id, self.item_id, self.daily_brief_id)
        )

    def as_metadata(self) -> dict[str, list[str]]:
        return {
            "feed_ids": [str(self.feed_id)] if self.feed_id is not None else [],
            "item_ids": [str(self.item_id)] if self.item_id is not None else [],
            "daily_brief_ids": (
                [str(self.daily_brief_id)]
                if self.daily_brief_id is not None
                else []
            ),
        }


@dataclass(frozen=True, slots=True)
class NotificationWebhookTestPolicySnapshot:
    iam_revision: int
    data_policy_revision: int
    data_policy_mode: str
    source_refs: NotificationWebhookTestSourceRefs
    feed_ids: tuple[uuid.UUID, ...]
    handling_label_ids: tuple[uuid.UUID, ...]
    decision: NotificationWebhookTestPolicyDecision

    @property
    def allowed(self) -> bool:
        return self.decision in {"allowed", "egress_would_deny"}

    @property
    def signature(self) -> str:
        return _digest_json(self.as_metadata())

    def as_metadata(self) -> dict[str, object]:
        return {
            "iam_revision": self.iam_revision,
            "data_policy_revision": self.data_policy_revision,
            "data_policy_mode": self.data_policy_mode,
            "source_ids": self.source_refs.as_metadata(),
            "feed_ids": [str(value) for value in self.feed_ids],
            "handling_label_ids": [
                str(value) for value in self.handling_label_ids
            ],
            "policy_decision": self.decision,
        }


@dataclass(frozen=True, slots=True)
class NotificationWebhookTestReceiptReservation:
    receipt_id: uuid.UUID
    created: bool
    replay_response: NotificationWebhookTestResponse | None = None


def authorize_notification_webhook_test(
    db: Session,
    *,
    user: User,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
    source_refs: NotificationWebhookTestSourceRefs,
) -> NotificationWebhookTestPolicySnapshot:
    try:
        fence_authorization_context(db, authorization)
        fence_data_access_context(db, data_access)
    except (AuthorizationStateUnavailable, DataPolicyError) as exc:
        raise NotificationWebhookTestPolicyUnavailable(
            "Webhook test authorization changed. Retry the request before sending."
        ) from exc

    if (
        authorization.principal_type != "user"
        or authorization.principal_id != user.id
        or data_access.principal_type != "user"
        or data_access.principal_id != user.id
    ):
        raise NotificationWebhookTestPolicyUnavailable(
            "Webhook test authorization does not match the current actor."
        )

    locked_user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        locked_user is None
        or not locked_user.is_active
        or not locked_user.is_approved
        or not authorization.account_eligible
        or not authorization.has(SCOPE_WRITE_NOTIFICATIONS)
    ):
        raise NotificationWebhookTestPolicyUnavailable(
            "Webhook test authorization is no longer eligible for outbound delivery."
        )

    feed_ids: set[uuid.UUID] = set()
    if source_refs.feed_id is not None:
        feed_ids.add(source_refs.feed_id)
    if source_refs.item_id is not None:
        item_row = db.execute(
            select(Item.id, Item.feed_id)
            .where(Item.id == source_refs.item_id)
            .with_for_update(read=True, of=Item)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if item_row is None:
            raise NotificationWebhookTestPolicyUnavailable(
                "Webhook test source provenance is no longer available."
            )
        feed_ids.add(item_row.feed_id)
        if (
            source_refs.feed_id is not None
            and source_refs.feed_id != item_row.feed_id
        ):
            raise NotificationWebhookTestPolicyUnavailable(
                "Webhook test source provenance changed before outbound delivery."
            )

    handling_label_ids: set[uuid.UUID] = set()
    if feed_ids:
        feed_rows = db.execute(
            select(Feed.id, Feed.handling_label_id)
            .where(Feed.id.in_(feed_ids))
            .with_for_update(read=True, of=Feed)
            .execution_options(populate_existing=True)
        ).all()
        if {row.id for row in feed_rows} != feed_ids:
            raise NotificationWebhookTestPolicyUnavailable(
                "Webhook test feed provenance is no longer available."
            )
        handling_label_ids.update(row.handling_label_id for row in feed_rows)

    if source_refs.daily_brief_id is not None:
        brief_id = db.scalar(
            select(AIDailyBrief.id)
            .where(
                AIDailyBrief.id == source_refs.daily_brief_id,
                AIDailyBrief.status == "ready",
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if brief_id is None:
            raise NotificationWebhookTestPolicyUnavailable(
                "Webhook test daily-brief provenance is no longer available."
            )
        if data_access.mode in {"audit", "enforced"}:
            try:
                envelope = get_data_access_envelope(
                    db,
                    resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
                    resource_id=brief_id,
                    for_update=True,
                )
            except DataPolicyUnavailable as exc:
                raise NotificationWebhookTestPolicyUnavailable(
                    "Webhook test daily-brief provenance is invalid."
                ) from exc
            if envelope is None:
                raise NotificationWebhookTestPolicyUnavailable(
                    "Webhook test daily-brief provenance is missing."
                )
            handling_label_ids.update(envelope.label_ids)

    if data_access.mode in {"audit", "enforced"} and handling_label_ids:
        active_label_ids = set(
            db.scalars(
                select(HandlingLabel.id)
                .where(
                    HandlingLabel.id.in_(handling_label_ids),
                    HandlingLabel.is_active.is_(True),
                )
                .with_for_update(read=True, of=HandlingLabel)
            ).all()
        )
        if active_label_ids != handling_label_ids:
            raise NotificationWebhookTestPolicyUnavailable(
                "Webhook test source provenance references an inactive handling label."
            )

    restricted_label_ids = handling_label_ids.difference(
        data_access.allowed_label_ids
    )
    decision: NotificationWebhookTestPolicyDecision = "allowed"
    if not data_access.principal_eligible:
        decision = "egress_not_served"
    elif data_access.auditing and restricted_label_ids:
        decision = "egress_would_deny"
    elif data_access.enforced and restricted_label_ids:
        decision = "egress_denied"

    return NotificationWebhookTestPolicySnapshot(
        iam_revision=authorization.policy_revision,
        data_policy_revision=data_access.policy_revision,
        data_policy_mode=data_access.mode,
        source_refs=source_refs,
        feed_ids=tuple(sorted(feed_ids, key=str)),
        handling_label_ids=tuple(sorted(handling_label_ids, key=str)),
        decision=decision,
    )


def unavailable_notification_webhook_test_snapshot(
    *,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
    source_refs: NotificationWebhookTestSourceRefs,
) -> NotificationWebhookTestPolicySnapshot:
    feed_ids = (
        (source_refs.feed_id,) if source_refs.feed_id is not None else ()
    )
    return NotificationWebhookTestPolicySnapshot(
        iam_revision=authorization.policy_revision,
        data_policy_revision=data_access.policy_revision,
        data_policy_mode=data_access.mode,
        source_refs=source_refs,
        feed_ids=feed_ids,
        handling_label_ids=(),
        decision="egress_not_served",
    )


def notification_webhook_test_request_digests(
    rendered: RenderedNotificationRequest,
    *,
    snapshot: NotificationWebhookTestPolicySnapshot,
    logical_request: object,
) -> tuple[str, str]:
    destination_digest = _digest_json(
        {
            "method": rendered.method.upper(),
            "url": rendered.url,
            "query_params": [
                [field.key, field.value] for field in rendered.query_params
            ],
        }
    )
    request_fingerprint = _digest_json(
        {
            # Rendered requests contain per-attempt delivery IDs and timestamps.
            # Bind the stable caller intent instead so an identical operation can
            # safely replay its settled, redacted response without sending again.
            "logical_request": logical_request,
            "policy_snapshot": snapshot.as_metadata(),
            "schema_version": _RECEIPT_SCHEMA_VERSION,
        }
    )
    return destination_digest, request_fingerprint


def reserve_notification_webhook_test_receipt(
    db: Session,
    *,
    operation_id: str,
    user: User,
    snapshot: NotificationWebhookTestPolicySnapshot,
    destination_digest: str,
    request_fingerprint: str,
) -> NotificationWebhookTestReceiptReservation:
    _lock_test_operation(db, actor_user_id=user.id, operation_id=operation_id)
    receipts = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action == NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION,
                AuditLog.actor_principal_type == "user",
                AuditLog.actor_principal_id == user.id,
                AuditLog.request_id == operation_id,
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).all()
    )
    if len(receipts) > 1:
        raise NotificationWebhookTestReplayConflict(
            "Webhook test operation has conflicting durable receipts. Use a new request ID."
        )
    if receipts:
        return _existing_receipt_reservation(
            db,
            receipt=receipts[0],
            request_fingerprint=request_fingerprint,
        )

    receipt = record_audit(
        db,
        actor_user_id=user.id,
        actor_principal_type="user",
        actor_principal_id=user.id,
        request_id=operation_id,
        action=NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION,
        resource_type=NOTIFICATION_WEBHOOK_TEST_RESOURCE_TYPE,
        resource_id=operation_id,
        success=False,
        metadata={
            "schema_version": _RECEIPT_SCHEMA_VERSION,
            "state": "reserved",
            "io_outcome": "reserved",
            "destination_digest": destination_digest,
            "request_fingerprint": request_fingerprint,
            "policy_snapshot_digest": snapshot.signature,
            **snapshot.as_metadata(),
        },
        data_access_governed=snapshot.source_refs.data_access_governed,
        data_access_label_ids=snapshot.handling_label_ids,
    )
    return NotificationWebhookTestReceiptReservation(
        receipt_id=receipt.id,
        created=True,
    )


def record_notification_webhook_test_policy_decision(
    db: Session,
    *,
    context: DataAccessContext,
    snapshot: NotificationWebhookTestPolicySnapshot,
    receipt_id: uuid.UUID,
    destination_digest: str,
) -> AuditLog | None:
    if snapshot.decision == "allowed":
        return None
    if (
        snapshot.decision == "egress_not_served"
        and context.mode == "disabled"
    ):
        return None
    return record_data_policy_decision(
        db,
        context=context,
        decision=snapshot.decision,
        resource_type=NOTIFICATION_WEBHOOK_TEST_RESOURCE_TYPE,
        resource_id=receipt_id,
        surface="notifications.webhooks.test",
        handling_label_ids=snapshot.handling_label_ids,
        request_served_known=False,
        metadata_extra={
            "notification_webhook_test_receipt_id": str(receipt_id),
            "destination_digest": destination_digest,
            "source_ids": snapshot.source_refs.as_metadata(),
            "iam_revision": snapshot.iam_revision,
        },
    )


def record_notification_webhook_test_outcome(
    db: Session,
    *,
    receipt_id: uuid.UUID,
    operation_id: str,
    user: User,
    snapshot: NotificationWebhookTestPolicySnapshot,
    destination_digest: str,
    request_fingerprint: str,
    io_outcome: Literal["not_sent", "response_received", "ambiguous"],
    state: Literal["settled", "denied", "unavailable", "ambiguous"],
    response: NotificationWebhookTestResponse | None = None,
    error_code: str | None = None,
) -> AuditLog:
    metadata: dict[str, object] = {
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "state": state,
        "io_outcome": io_outcome,
        "destination_digest": destination_digest,
        "request_fingerprint": request_fingerprint,
        "policy_snapshot_digest": snapshot.signature,
        "settled_at": datetime.now(timezone.utc).isoformat(),
        **snapshot.as_metadata(),
    }
    if response is not None:
        metadata["response"] = response.model_dump(mode="json")
    if error_code is not None:
        metadata["error_code"] = error_code
    return record_audit(
        db,
        actor_user_id=user.id,
        actor_principal_type="user",
        actor_principal_id=user.id,
        request_id=operation_id,
        action=NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION,
        resource_type=NOTIFICATION_WEBHOOK_TEST_RESOURCE_TYPE,
        resource_id=str(receipt_id),
        execution_receipt_id=receipt_id,
        success=bool(response is not None and response.success),
        metadata=metadata,
        data_access_governed=snapshot.source_refs.data_access_governed,
        data_access_label_ids=snapshot.handling_label_ids,
    )


def lock_notification_webhook_test_receipt_for_outcome(
    db: Session,
    *,
    receipt_id: uuid.UUID,
    request_fingerprint: str,
) -> AuditLog:
    receipt = db.scalar(
        select(AuditLog)
        .where(
            AuditLog.id == receipt_id,
            AuditLog.action == NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if receipt is None:
        raise NotificationWebhookTestReplayConflict(
            "Webhook test durable receipt is unavailable. Do not retry this operation."
        )
    if (receipt.metadata_json or {}).get("request_fingerprint") != request_fingerprint:
        raise NotificationWebhookTestReplayConflict(
            "Webhook test durable receipt no longer matches this request."
        )
    existing_outcome = db.scalar(
        select(AuditLog.id).where(
            AuditLog.action == NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION,
            AuditLog.resource_type == NOTIFICATION_WEBHOOK_TEST_RESOURCE_TYPE,
            AuditLog.resource_id == str(receipt_id),
        )
    )
    if existing_outcome is not None:
        raise NotificationWebhookTestReplayConflict(
            "Webhook test durable receipt already has an outcome."
        )
    return receipt


def require_matching_notification_webhook_test_snapshot(
    expected: NotificationWebhookTestPolicySnapshot,
    current: NotificationWebhookTestPolicySnapshot,
) -> None:
    if current.signature != expected.signature:
        raise NotificationWebhookTestPolicyUnavailable(
            "Webhook test source authorization changed before outbound delivery. Retry the request."
        )


def policy_error_from_snapshot(
    snapshot: NotificationWebhookTestPolicySnapshot,
) -> NotificationWebhookTestPolicyError | None:
    if snapshot.decision == "egress_denied":
        return NotificationWebhookTestPolicyDenied(
            "Webhook test source is not available for outbound delivery."
        )
    if snapshot.decision == "egress_not_served":
        return NotificationWebhookTestPolicyUnavailable(
            "Webhook test source authorization is unavailable for outbound delivery."
        )
    return None


def _existing_receipt_reservation(
    db: Session,
    *,
    receipt: AuditLog,
    request_fingerprint: str,
) -> NotificationWebhookTestReceiptReservation:
    metadata = receipt.metadata_json or {}
    if metadata.get("schema_version") != _RECEIPT_SCHEMA_VERSION:
        raise NotificationWebhookTestReplayConflict(
            "Webhook test receipt schema is unsupported. Use a new request ID."
        )
    if metadata.get("request_fingerprint") != request_fingerprint:
        raise NotificationWebhookTestReplayConflict(
            "This request ID was already used for a different webhook test. Use a new request ID."
        )
    outcomes = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action == NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION,
                AuditLog.resource_type == NOTIFICATION_WEBHOOK_TEST_RESOURCE_TYPE,
                AuditLog.resource_id == str(receipt.id),
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).all()
    )
    if len(outcomes) > 1:
        raise NotificationWebhookTestReplayConflict(
            "Webhook test operation has conflicting durable outcomes. Use a new request ID."
        )
    if not outcomes:
        raise NotificationWebhookTestReplayUnsafe(
            "The prior webhook test may have reached its destination. Do not replay it with the same request ID."
        )
    outcome = outcomes[0].metadata_json or {}
    if outcome.get("request_fingerprint") != request_fingerprint:
        raise NotificationWebhookTestReplayConflict(
            "Webhook test outcome does not match its durable receipt. Use a new request ID."
        )
    io_outcome = outcome.get("io_outcome")
    if io_outcome == "ambiguous":
        raise NotificationWebhookTestReplayUnsafe(
            "The prior webhook test may have reached its destination. Do not replay it with the same request ID."
        )
    response = outcome.get("response")
    if isinstance(response, dict):
        try:
            replay = NotificationWebhookTestResponse.model_validate(response)
        except ValueError as exc:
            raise NotificationWebhookTestReplayConflict(
                "Webhook test receipt response is invalid. Use a new request ID."
            ) from exc
        return NotificationWebhookTestReceiptReservation(
            receipt_id=receipt.id,
            created=False,
            replay_response=replay,
        )
    error_code = outcome.get("error_code")
    if error_code == NotificationWebhookTestPolicyDenied.code:
        raise NotificationWebhookTestPolicyDenied(
            "Webhook test source is not available for outbound delivery."
        )
    if error_code == NotificationWebhookTestPolicyUnavailable.code:
        raise NotificationWebhookTestPolicyUnavailable(
            "Webhook test source authorization is unavailable for outbound delivery."
        )
    raise NotificationWebhookTestReplayUnsafe(
        "The prior webhook test has no replayable response. Use a new request ID after reviewing its audit receipt."
    )


def _lock_test_operation(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    operation_id: str,
) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    material = (
        f"notification-webhook-test:{actor_user_id}:{operation_id}".encode("utf-8")
    )
    unsigned = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    lock_id = unsigned if unsigned < 2**63 else unsigned - 2**64
    db.execute(select(func.pg_advisory_xact_lock(lock_id)))


def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION",
    "NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION",
    "NotificationWebhookTestPolicyDenied",
    "NotificationWebhookTestPolicyError",
    "NotificationWebhookTestPolicySnapshot",
    "NotificationWebhookTestPolicyUnavailable",
    "NotificationWebhookTestReceiptReservation",
    "NotificationWebhookTestReplayConflict",
    "NotificationWebhookTestReplayUnsafe",
    "NotificationWebhookTestSourceRefs",
    "authorize_notification_webhook_test",
    "lock_notification_webhook_test_receipt_for_outcome",
    "notification_webhook_test_request_digests",
    "policy_error_from_snapshot",
    "record_notification_webhook_test_outcome",
    "record_notification_webhook_test_policy_decision",
    "require_matching_notification_webhook_test_snapshot",
    "reserve_notification_webhook_test_receipt",
    "unavailable_notification_webhook_test_snapshot",
]
