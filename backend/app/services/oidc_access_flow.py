from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.oidc import OIDCProvider
from app.models.user import User
from app.services.oidc_access import (
    OIDCAccessSyncResult,
    oidc_access_policy_matches,
    oidc_access_policy_snapshot,
    record_oidc_access_sync_audit,
    sync_oidc_access,
)
from app.services.oidc_client import OIDCClaims
from app.services.oidc_transaction import OIDCTransaction


def oidc_access_policy_transaction_fields(
    db: Session,
    *,
    provider_id: uuid.UUID,
    mode: str,
) -> dict[str, object | None]:
    if mode != "login":
        return {}
    snapshot = oidc_access_policy_snapshot(db, provider_id)
    return {
        "access_policy_id": (
            str(snapshot.policy_id) if snapshot.policy_id is not None else None
        ),
        "access_policy_revision": snapshot.revision,
        "access_policy_generation": snapshot.generation,
    }


def oidc_transaction_access_policy_matches(
    db: Session,
    *,
    provider_id: uuid.UUID,
    transaction: OIDCTransaction,
) -> bool:
    if transaction.mode != "login":
        return True
    return oidc_access_policy_matches(
        db,
        provider_id,
        expected_policy_id=_policy_id(transaction),
        expected_revision=transaction.access_policy_revision,
        expected_generation=transaction.access_policy_generation,
    )


def sync_oidc_transaction_access(
    db: Session,
    *,
    provider: OIDCProvider,
    user: User,
    claims: OIDCClaims,
    transaction: OIDCTransaction,
    credentials_already_rotated: bool,
) -> OIDCAccessSyncResult:
    result = sync_oidc_access(
        db,
        provider_id=provider.id,
        user=user,
        claims=claims.claims,
        expected_policy_id=_policy_id(transaction),
        expected_policy_revision=transaction.access_policy_revision,
        expected_policy_generation=transaction.access_policy_generation,
        credentials_already_rotated=credentials_already_rotated,
    )
    record_oidc_access_sync_audit(db, provider=provider, user=user, result=result)
    return result


def _policy_id(transaction: OIDCTransaction) -> uuid.UUID | None:
    return (
        uuid.UUID(transaction.access_policy_id)
        if transaction.access_policy_id is not None
        else None
    )


__all__ = [
    "oidc_access_policy_transaction_fields",
    "oidc_transaction_access_policy_matches",
    "sync_oidc_transaction_access",
]
