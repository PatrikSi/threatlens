"""Narrow machine authority, fenced against credential rotation until commit."""

from datetime import datetime, timezone
import hashlib
import hmac
import uuid
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.automation_receiver import AutomationReceiverCredential


def authenticate_receiver(
    db: Session, request: Request
) -> AutomationReceiverCredential:
    if len(request.headers.getlist("authorization")) != 1 or any(
        key.lower() in {"access_token", "token", "api_key", "authorization"}
        for key in request.query_params
    ):
        raise HTTPException(
            401,
            "Use exactly one Authorization header; query credentials are not accepted",
        )
    header = request.headers.get("authorization", "")
    prefix, _, token = header.partition(" ")
    try:
        marker, identity, secret = token.split("_", 2)
        if (
            prefix.lower() != "bearer"
            or marker != "tlrecv"
            or len(secret) < 32
            or len(token) > 200
        ):
            raise ValueError
        identity = uuid.UUID(hex=identity)
    except ValueError:
        raise HTTPException(
            401, "A destination-scoped receiver credential is required"
        ) from None
    row = db.scalar(
        select(AutomationReceiverCredential)
        .where(AutomationReceiverCredential.id == identity)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    now = datetime.now(timezone.utc)
    if (
        row is None
        or row.revoked_at is not None
        or row.expires_at <= now
        or not hmac.compare_digest(
            row.token_hash, hashlib.sha256(token.encode()).hexdigest()
        )
    ):
        raise HTTPException(401, "Receiver credential is invalid, expired or revoked")
    return row


def assert_receiver_current(
    db: Session, credential: AutomationReceiverCredential
) -> None:
    # SHARE locking fences revocation; expiry is clock based and must be checked
    # again after waiting for an execution or policy row.
    from sqlalchemy import func

    if not db.scalar(
        select(AutomationReceiverCredential.id).where(
            AutomationReceiverCredential.id == credential.id,
            AutomationReceiverCredential.revoked_at.is_(None),
            AutomationReceiverCredential.expires_at > func.clock_timestamp(),
        )
    ):
        raise HTTPException(
            401, "Receiver credential expired while waiting; renew it and retry"
        )
