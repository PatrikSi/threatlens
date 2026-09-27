"""Durable opaque notifications; delivery never grants permission to read evidence.

A consumer token only reads its own subscribed identities and acknowledges them.
Evidence remains behind the ordinary publication download authorization. Losing a
custodian or captured handling access produces a permanent opaque withdrawal;
restoring access requires a deliberate new subscription/consumer registration.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.models.indicator_publication import IndicatorPublication
from app.models.publication_consumer import (
    PublicationChange,
    PublicationConsumer,
    PublicationSubscription,
)
from app.models.user import User
from app.services.audit import record_audit
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import data_access_context_for_authorization
from app.services.indicator_assessments import fence_indicator_request
from app.services.indicator_publications import load_publication, publication_access
from app.services.secret_storage import decrypt_json, encrypt_json
from app.services.team_access import team_access_predicate
from app.services.team_assessment_access import AssessmentRequest

REPLAY_DAYS = 90
MAX_CONSUMERS = 20
MAX_SUBSCRIPTIONS = 1000
MAX_PENDING_CHANGES = 10000
SOFT_RETAINED_CHANGES = 9000


def error(code: str, message: str, status: int = 409) -> ApiHTTPException:
    return ApiHTTPException(status_code=status, error_code=code, detail=message)


def manager_consumer(
    db: Session, actor: AssessmentRequest, team_id: uuid.UUID, consumer_id: uuid.UUID
) -> PublicationConsumer:
    fence_indicator_request(db, actor, team_id=team_id, write=True, manage=True)
    row = db.scalar(
        select(PublicationConsumer)
        .where(
            PublicationConsumer.id == consumer_id,
            PublicationConsumer.team_id == team_id,
        )
        .with_for_update()
    )
    if row is None:
        raise error("consumer_not_found", "Publication consumer not found.", 404)
    return row


def create_consumer(
    db: Session,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    *,
    name: str,
    expires_days: int,
    idempotency_key: uuid.UUID,
):
    fence_indicator_request(db, actor, team_id=team_id, write=True, manage=True)
    digest = hashlib.sha256(
        json.dumps(
            {"name": name.strip(), "expires_days": expires_days}, sort_keys=True
        ).encode()
    ).hexdigest()
    existing = db.scalar(
        select(PublicationConsumer).where(
            PublicationConsumer.team_id == team_id,
            PublicationConsumer.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_digest != digest:
            raise error(
                "consumer_request_conflict",
                "This registration request ID was already used with different settings. Refresh consumers and prepare a new request.",
            )
        raise ApiHTTPException(
            status_code=409,
            error_code="consumer_already_registered",
            detail="This consumer was already registered. Refresh the consumer list and rotate its credential if the original secret response was lost.",
            error_context={"consumer_id": str(existing.id)},
        )
    count = (
        db.scalar(
            select(func.count())
            .select_from(PublicationConsumer)
            .where(PublicationConsumer.team_id == team_id)
        )
        or 0
    )
    if count >= MAX_CONSUMERS:
        raise error(
            "consumer_capacity", "This team already has 20 retained consumers.", 429
        )
    token = "tlpc_" + secrets.token_urlsafe(40)
    row = PublicationConsumer(
        id=uuid.uuid4(),
        team_id=team_id,
        name=name.strip(),
        principal_id=actor.user.id,
        idempotency_key=idempotency_key,
        request_digest=digest,
        authorization_encrypted=encrypt_json(actor.snapshot.model_dump(mode="json")),
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_days),
        sequence=0,
        replay_floor=0,
        generation=1,
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        actor_user_id=actor.user.id,
        action="intelligence.consumer.create",
        resource_type="publication_consumer",
        resource_id=str(row.id),
        metadata={"team_id": str(team_id)},
    )
    return row, token


def authenticate_consumer(
    db: Session, authorization: str | None
) -> PublicationConsumer:
    parts = (authorization or "").split()
    if (
        len(parts) != 2
        or parts[0].lower() != "bearer"
        or not parts[1].startswith("tlpc_")
        or len(parts[1]) > 256
    ):
        raise error(
            "consumer_credential_invalid",
            "A publication-consumer bearer credential is required.",
            401,
        )
    row = db.scalar(
        select(PublicationConsumer)
        .where(
            PublicationConsumer.token_hash
            == hashlib.sha256(parts[1].encode()).hexdigest()
        )
        .with_for_update()
    )
    if (
        row is None
        or row.revoked_at is not None
        or row.expires_at <= datetime.now(timezone.utc)
    ):
        raise error(
            "consumer_credential_invalid",
            "Publication-consumer credential is expired, revoked or invalid.",
            401,
        )
    return row


def _append(
    db: Session,
    row: PublicationConsumer,
    subscription: PublicationSubscription,
    *,
    revision: int,
    kind: str,
) -> None:
    row.sequence += 1
    db.add(
        PublicationChange(
            id=uuid.uuid4(),
            consumer_id=row.id,
            sequence=row.sequence,
            publication_id=subscription.publication_id,
            revision=revision,
            kind=kind,
        )
    )
    subscription.last_revision = revision
    if kind == "withdrawn":
        subscription.withdrawn_at = datetime.now(timezone.utc)


def subscribe(
    db: Session,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    consumer_id: uuid.UUID,
    publication_id: uuid.UUID,
) -> None:
    # Global policy/credential -> team -> consumer -> publication. Polling only
    # reads publication metadata; it never takes upstream authorization locks.
    consumer = manager_consumer(db, actor, team_id, consumer_id)
    if consumer.retired_at is not None:
        raise error(
            "consumer_retired",
            "This consumer is retired. Finish acknowledgements and archive it; register a new consumer for new publications.",
        )
    if consumer.revoked_at or consumer.expires_at <= datetime.now(timezone.utc):
        raise error(
            "consumer_inactive",
            "Rotate the expired or revoked credential before adding subscriptions.",
        )
    existing = db.get(PublicationSubscription, (consumer_id, publication_id))
    if existing:
        if existing.withdrawn_at:
            raise error(
                "subscription_withdrawn",
                "This subscription was withdrawn. Register a new consumer after reapproval.",
            )
        return
    count = (
        db.scalar(
            select(func.count())
            .select_from(PublicationSubscription)
            .where(PublicationSubscription.consumer_id == consumer_id)
        )
        or 0
    )
    if count >= MAX_SUBSCRIPTIONS:
        raise error(
            "subscription_capacity",
            "A consumer can retain at most 1,000 publication subscriptions.",
            429,
        )
    prune_acknowledged_changes(db, consumer)
    retained = (
        db.scalar(
            select(func.count())
            .select_from(PublicationChange)
            .where(PublicationChange.consumer_id == consumer_id)
        )
        or 0
    )
    if retained >= MAX_PENDING_CHANGES:
        raise error(
            "consumer_retention_backpressure",
            "The consumer's retained change feed is full. Acknowledge its oldest changes or explicitly discard and reset before adding subscriptions.",
            429,
        )
    row = load_publication(
        db, actor=actor, team_id=team_id, publication_id=publication_id
    )
    custodian = _custodian_actor(db, consumer)
    if (
        custodian is None
        or db.scalar(
            select(IndicatorPublication.id).where(
                IndicatorPublication.id == row.id, publication_access(custodian)
            )
        )
        is None
    ):
        raise error(
            "consumer_clearance_denied",
            "The registered consumer's custodian and captured clearance do not authorize this publication. Register a new consumer with the intended clearance.",
            403,
        )
    if row.status == "withdrawn":
        raise error(
            "publication_withdrawn", "This publication has been fully withdrawn."
        )
    subscription = PublicationSubscription(
        consumer_id=consumer_id, publication_id=publication_id, last_revision=0
    )
    db.add(subscription)
    _append(db, consumer, subscription, revision=row.revision, kind="available")
    db.flush()
    record_audit(
        db,
        actor_user_id=actor.user.id,
        action="intelligence.consumer.subscribe",
        resource_type="publication_consumer",
        resource_id=str(consumer_id),
        metadata={"publication_id": str(publication_id)},
    )


def _custodian_actor(
    db: Session, consumer: PublicationConsumer
) -> AssessmentRequest | None:
    """Registration is a durable delegation capped by the accepting clearance.

    The browser session may expire, but removing the human's permissions, team
    membership, or handling access stops the delegation. Tokens never read data.
    """
    from dataclasses import replace
    from app.services.export_job_contracts import ExportAuthorizationSnapshot

    if consumer.principal_id is None or consumer.retired_at is not None:
        return None
    user = db.get(User, consumer.principal_id)
    if user is None or not db.scalar(
        select(team_access_predicate(consumer.team_id, consumer.principal_id))
    ):
        return None
    try:
        snapshot = ExportAuthorizationSnapshot.model_validate(
            decrypt_json(consumer.authorization_encrypted)
        )
        authorization = authorization_context_for_user(
            db, user, credential_scopes=snapshot.permissions
        )
        if not authorization.has("read:items") or not authorization.has("read:teams"):
            return None
        access = data_access_context_for_authorization(db, authorization)
        if snapshot.enforced:
            access = replace(
                access,
                mode="enforced",
                allowed_label_ids=access.allowed_label_ids & snapshot.allowed_label_ids,
            )
        return AssessmentRequest(user, authorization, access, snapshot)
    except (ValueError, TypeError):
        return None


def reconcile_consumer(
    db: Session, consumer: PublicationConsumer, *, limit: int = 50
) -> int:
    now = datetime.now(timezone.utc)
    actor = _custodian_actor(db, consumer)
    subscriptions = db.scalars(
        select(PublicationSubscription)
        .where(
            PublicationSubscription.consumer_id == consumer.id,
            PublicationSubscription.withdrawn_at.is_(None),
            PublicationSubscription.next_check_at <= now,
        )
        .order_by(
            PublicationSubscription.next_check_at,
            PublicationSubscription.publication_id,
        )
        .limit(limit)
        .with_for_update()
    ).all()
    ids = [s.publication_id for s in subscriptions]
    rows = (
        {
            r.id: r
            for r in db.execute(
                select(
                    IndicatorPublication.id,
                    IndicatorPublication.revision,
                    IndicatorPublication.status,
                ).where(
                    IndicatorPublication.id.in_(ids),
                    publication_access(actor),
                )
            )
        }
        if actor
        else {}
    )
    pending = (
        db.scalar(
            select(func.count())
            .select_from(PublicationChange)
            .where(PublicationChange.consumer_id == consumer.id)
        )
        or 0
    )
    for subscription in subscriptions:
        row = rows.get(subscription.publication_id)
        kind = "withdrawn" if row is None or row.status == "withdrawn" else "changed"
        revision = row.revision if row else subscription.last_revision
        if kind == "withdrawn" or revision != subscription.last_revision:
            # Withdrawals always take precedence over the ordinary update cap.
            # Per-consumer subscriptions bound the maximum extra obligations.
            if pending >= MAX_PENDING_CHANGES and kind != "withdrawn":
                subscription.next_check_at = now + timedelta(minutes=5)
                continue
            _append(db, consumer, subscription, revision=revision, kind=kind)
            pending += 1
        subscription.next_check_at = now + timedelta(minutes=5)
    db.flush()
    return len(subscriptions)


def consumer_feed(
    db: Session,
    consumer: PublicationConsumer,
    *,
    after: int,
    generation: int,
    limit: int,
) -> dict:
    if generation != consumer.generation or after < consumer.replay_floor:
        raise error(
            "consumer_replay_expired",
            "Replay history is no longer available. Discard all previously imported publications, acknowledge a reset, and reimport the current feed.",
            410,
        )
    if after > consumer.sequence:
        raise error(
            "consumer_cursor_invalid",
            "The cursor is ahead of this consumer's change feed.",
            400,
        )
    reconciled = reconcile_consumer(db, consumer)
    consumer.last_reconciled_at = datetime.now(timezone.utc)
    rows = db.scalars(
        select(PublicationChange)
        .where(
            PublicationChange.consumer_id == consumer.id,
            PublicationChange.sequence > after,
        )
        .order_by(PublicationChange.sequence)
        .limit(limit + 1)
    ).all()
    consumer.last_poll_at = datetime.now(timezone.utc)
    retained, unacknowledged = db.execute(
        select(
            func.count(),
            func.count().filter(PublicationChange.acknowledged_at.is_(None)),
        ).where(PublicationChange.consumer_id == consumer.id)
    ).one()
    selected = rows[:limit]
    return {
        "generation": consumer.generation,
        "changes": [
            {
                "id": str(r.id),
                "sequence": r.sequence,
                "publication_id": str(r.publication_id),
                "revision": r.revision,
                "kind": r.kind,
                "created_at": r.created_at.isoformat(),
                "acknowledged": r.acknowledged_at is not None,
            }
            for r in selected
        ],
        "next_after": selected[-1].sequence if selected else after,
        "has_more": len(rows) > limit,
        "reconciliation_batch_full": reconciled == 50,
        "replay_days": REPLAY_DAYS,
        "replay_floor": consumer.replay_floor,
        "capacity_compaction_enabled": True,
        "retained_changes": retained,
        "unacknowledged_changes": unacknowledged,
        "retention_backpressure": retained >= MAX_PENDING_CHANGES,
        "retention_guidance": "Acknowledge the oldest changes. If replay expired, discard all imported publications and explicitly reset before reimporting.",
        "evidence_access": "Use a current scoped API credential to download publications; this feed grants no evidence access.",
    }


def acknowledge(
    db: Session,
    consumer: PublicationConsumer,
    *,
    generation: int,
    change_ids: list[uuid.UUID],
) -> int:
    if generation != consumer.generation:
        raise error(
            "consumer_generation_changed",
            "Consumer was reset. Start from its current generation.",
        )
    rows = db.scalars(
        select(PublicationChange).where(
            PublicationChange.consumer_id == consumer.id,
            PublicationChange.id.in_(change_ids),
        )
    ).all()
    if len(rows) != len(set(change_ids)):
        raise error(
            "consumer_change_missing",
            "An acknowledgement refers to a missing or different consumer's change.",
            404,
        )
    now = datetime.now(timezone.utc)
    for row in rows:
        row.acknowledged_at = row.acknowledged_at or now
    return len(rows)


def reset_consumer(
    db: Session,
    consumer: PublicationConsumer,
    *,
    expected_generation: int,
    discarded: bool,
) -> dict:
    if not discarded:
        raise error(
            "consumer_reset_requires_discard",
            "Discard previously imported publications before acknowledging reset.",
        )
    if expected_generation != consumer.generation:
        raise error(
            "consumer_generation_changed",
            "Consumer was already reset. Read the current generation first.",
        )
    now = datetime.now(timezone.utc)
    # Reset acknowledges every old obligation only after explicit receiver discard.
    record_audit(
        db,
        actor_user_id=None,
        action="intelligence.consumer.reset",
        resource_type="publication_consumer",
        resource_id=str(consumer.id),
        metadata={
            "generation": consumer.generation,
            "discarded_through": consumer.sequence,
        },
    )
    db.execute(
        delete(PublicationChange).where(PublicationChange.consumer_id == consumer.id)
    )
    consumer.replay_floor = consumer.sequence
    consumer.generation += 1
    for subscription in db.scalars(
        select(PublicationSubscription).where(
            PublicationSubscription.consumer_id == consumer.id,
            PublicationSubscription.withdrawn_at.is_(None),
        )
    ):
        subscription.last_revision = 0
        subscription.next_check_at = now
    db.flush()
    return {"generation": consumer.generation, "after": consumer.replay_floor}


def prune_acknowledged_changes(db: Session, consumer: PublicationConsumer) -> int:
    """Compact only a contiguous acknowledged prefix, never an unmet obligation.

    Age retention and capacity compaction share the same replay-floor contract.
    Consumers behind that floor must explicitly discard/reset rather than treat
    a truncated feed as complete. Each transaction removes at most 100 records.
    """
    retained = (
        db.scalar(
            select(func.count())
            .select_from(PublicationChange)
            .where(PublicationChange.consumer_id == consumer.id)
        )
        or 0
    )
    soft_limit = min(SOFT_RETAINED_CHANGES, MAX_PENDING_CHANGES - 1)
    cutoff = datetime.now(timezone.utc) - timedelta(days=REPLAY_DAYS)
    rows = db.scalars(
        select(PublicationChange)
        .where(PublicationChange.consumer_id == consumer.id)
        .order_by(PublicationChange.sequence)
        .limit(100)
    ).all()
    selected = []
    for row in rows:
        if row.acknowledged_at is None:
            break
        if row.created_at >= cutoff and retained - len(selected) <= soft_limit:
            break
        selected.append(row.id)
        consumer.replay_floor = row.sequence
    if selected:
        db.execute(delete(PublicationChange).where(PublicationChange.id.in_(selected)))
    return len(selected)


def retire_consumer(
    db: Session, consumer: PublicationConsumer, *, actor_user_id: uuid.UUID
) -> dict:
    """End delegation without invalidating the credential needed for withdrawals."""
    if consumer.retired_at is not None:
        return {
            "retired": True,
            "consumer_id": str(consumer.id),
            "withdrawals_created": 0,
        }
    subscriptions = db.scalars(
        select(PublicationSubscription)
        .where(
            PublicationSubscription.consumer_id == consumer.id,
            PublicationSubscription.withdrawn_at.is_(None),
        )
        .order_by(PublicationSubscription.publication_id)
        .limit(MAX_SUBSCRIPTIONS + 1)
        .with_for_update()
    ).all()
    if len(subscriptions) > MAX_SUBSCRIPTIONS:
        raise error(
            "consumer_subscription_capacity_invalid",
            "The retained consumer exceeds its subscription bound. Repair its history before retirement.",
        )
    consumer.retired_at = datetime.now(timezone.utc)
    for subscription in subscriptions:
        _append(
            db,
            consumer,
            subscription,
            revision=subscription.last_revision,
            kind="withdrawn",
        )
    db.flush()
    record_audit(
        db,
        actor_user_id=actor_user_id,
        action="intelligence.consumer.retire",
        resource_type="publication_consumer",
        resource_id=str(consumer.id),
        metadata={
            "team_id": str(consumer.team_id),
            "withdrawals_created": len(subscriptions),
            "through_sequence": consumer.sequence,
        },
    )
    return {
        "retired": True,
        "consumer_id": str(consumer.id),
        "withdrawals_created": len(subscriptions),
    }


def archive_consumer(
    db: Session, consumer: PublicationConsumer, *, actor_user_id: uuid.UUID
) -> dict:
    """Reclaim a registration only after every withdrawal obligation is settled."""
    if consumer.retired_at is None:
        raise error(
            "consumer_retirement_required",
            "Retire this consumer and acknowledge its withdrawals before archiving.",
        )
    active = (
        db.scalar(
            select(func.count())
            .select_from(PublicationSubscription)
            .where(
                PublicationSubscription.consumer_id == consumer.id,
                PublicationSubscription.withdrawn_at.is_(None),
            )
        )
        or 0
    )
    pending = (
        db.scalar(
            select(func.count())
            .select_from(PublicationChange)
            .where(
                PublicationChange.consumer_id == consumer.id,
                PublicationChange.acknowledged_at.is_(None),
            )
        )
        or 0
    )
    if active or pending:
        raise error(
            "consumer_acknowledgements_pending",
            "The receiver must acknowledge every retained change and withdrawal, or explicitly discard and reset, before this consumer can be archived.",
        )
    identifier = consumer.id
    record_audit(
        db,
        actor_user_id=actor_user_id,
        action="intelligence.consumer.archive",
        resource_type="publication_consumer",
        resource_id=str(identifier),
        metadata={
            "team_id": str(consumer.team_id),
            "final_sequence": consumer.sequence,
            "generation": consumer.generation,
        },
    )
    db.delete(consumer)
    db.flush()
    return {"archived": True, "consumer_id": str(identifier)}
