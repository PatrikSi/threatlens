from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.governance_operation_receipt import GovernanceOperationReceipt


@dataclass(frozen=True)
class GovernanceOperationIdentity:
    operation: str
    key_hash: str
    request_fingerprint: str


class GovernanceIdempotencyError(RuntimeError):
    code = "governance_idempotency_error"


class GovernanceIdempotencyKeyInvalid(GovernanceIdempotencyError):
    code = "governance_idempotency_key_invalid"


class GovernanceIdempotencyConflict(GovernanceIdempotencyError):
    code = "governance_idempotency_conflict"


class GovernanceIdempotencySchemaUnsupported(GovernanceIdempotencyConflict):
    code = "governance_idempotency_schema_unsupported"


def build_governance_operation_identity(
    idempotency_key: str,
    *,
    operation: str,
    payload: dict[str, object],
) -> GovernanceOperationIdentity:
    normalized_key = idempotency_key.strip()
    if not 8 <= len(normalized_key) <= 255:
        raise GovernanceIdempotencyKeyInvalid(
            "Idempotency-Key must contain between 8 and 255 characters."
        )
    if any(
        ord(character) < 32 or ord(character) == 127 for character in normalized_key
    ):
        raise GovernanceIdempotencyKeyInvalid(
            "Idempotency-Key cannot contain control characters."
        )
    normalized_operation = operation.strip()
    if not normalized_operation or len(normalized_operation) > 96:
        raise ValueError("Governance operation names must contain 1 to 96 characters.")
    canonical_request = json.dumps(
        {
            "operation": normalized_operation,
            "payload": payload,
            "version": 1,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return GovernanceOperationIdentity(
        operation=normalized_operation,
        key_hash=hashlib.sha256(normalized_key.encode("utf-8")).hexdigest(),
        request_fingerprint=hashlib.sha256(canonical_request).hexdigest(),
    )


def find_governance_operation_replay(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    identity: GovernanceOperationIdentity,
) -> GovernanceOperationReceipt | None:
    receipt = db.scalar(
        select(GovernanceOperationReceipt).where(
            GovernanceOperationReceipt.actor_user_id == actor_user_id,
            GovernanceOperationReceipt.operation == identity.operation,
            GovernanceOperationReceipt.key_hash == identity.key_hash,
        )
    )
    if receipt is None:
        return None
    if receipt.request_fingerprint != identity.request_fingerprint:
        raise GovernanceIdempotencyConflict(
            "This Idempotency-Key was already used for a different governance request. Use a new key."
        )
    return receipt


def lock_governance_operation_identity(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    identity: GovernanceOperationIdentity,
) -> None:
    """Serialize one operation key before any governance side effects occur."""
    if db.get_bind().dialect.name != "postgresql":
        return
    material = f"{actor_user_id}:{identity.operation}:{identity.key_hash}".encode(
        "ascii"
    )
    unsigned = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    lock_id = unsigned if unsigned < 2**63 else unsigned - 2**64
    db.execute(select(func.pg_advisory_xact_lock(lock_id)))


def governance_operation_replay_payload(
    receipt: GovernanceOperationReceipt,
    *,
    supported_schema_version: int = 1,
) -> dict[str, object]:
    if receipt.response_schema_version != supported_schema_version:
        raise GovernanceIdempotencySchemaUnsupported(
            "The stored idempotent response uses an unsupported schema version. Reload the resource directly and retry with a new Idempotency-Key."
        )
    return dict(receipt.response_json)


def record_governance_operation_receipt(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    identity: GovernanceOperationIdentity,
    resource_type: str,
    resource_id: uuid.UUID,
    response_json: dict[str, object],
    http_status: int,
) -> GovernanceOperationReceipt:
    receipt = GovernanceOperationReceipt(
        actor_user_id=actor_user_id,
        operation=identity.operation,
        key_hash=identity.key_hash,
        request_fingerprint=identity.request_fingerprint,
        resource_type=resource_type,
        resource_id=resource_id,
        response_json=response_json,
        response_schema_version=1,
        http_status=http_status,
    )
    db.add(receipt)
    try:
        db.flush()
    except IntegrityError as exc:
        raise GovernanceIdempotencyConflict(
            "This governance request raced with a retry. Reload the resource before retrying with the same key."
        ) from exc
    return receipt


__all__ = [
    "GovernanceIdempotencyConflict",
    "GovernanceIdempotencyError",
    "GovernanceIdempotencyKeyInvalid",
    "GovernanceIdempotencySchemaUnsupported",
    "GovernanceOperationIdentity",
    "build_governance_operation_identity",
    "find_governance_operation_replay",
    "governance_operation_replay_payload",
    "lock_governance_operation_identity",
    "record_governance_operation_receipt",
]
