"""Reauthorize durable exports without widening the accepting credential's scope."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.token_scopes import SCOPE_READ_ITEMS, has_required_scope
from app.models.api_token import ApiToken
from app.models.auth_session import AuthSession
from app.models.feed import Feed
from app.models.item import Item
from app.models.service_account import ServiceAccount, ServiceAccountCredential
from app.models.user import User
from app.services.authorization import (
    AuthorizationContext,
    authorization_context_for_service_account,
    authorization_context_for_user,
    fence_authorization_context,
)
from app.services.data_access_policy import (
    DataAccessContext,
    data_access_context_for_authorization,
    fence_data_access_context,
    handling_label_access_predicate,
)
from app.services.export_job_contracts import (
    CredentialBoundWork,
    ExportAuthorizationSnapshot,
    ExportSource,
)
from app.services.secret_storage import decrypt_json

ExportCredential = ApiToken | AuthSession | ServiceAccountCredential


class ExportJobAccessDenied(RuntimeError):
    pass


def capture_export_authorization(
    request: Request,
    authorization: AuthorizationContext | None,
    data_access: DataAccessContext,
) -> ExportAuthorizationSnapshot:
    kind = getattr(request.state, "auth_credential_kind", None)
    credential_id = {
        "session_cookie": getattr(request.state, "auth_session_id", None),
        "api_token": getattr(request.state, "api_token_id", None),
        "service_account_token": getattr(
            request.state, "service_account_credential_id", None
        ),
    }.get(kind)
    if credential_id is None or authorization is None:
        raise ExportJobAccessDenied("A durable export requires a current credential")
    return ExportAuthorizationSnapshot(
        credential_kind=kind,
        credential_id=credential_id,
        permissions=authorization.permissions,
        enforced=data_access.enforced,
        allowed_label_ids=data_access.allowed_label_ids,
    )


def load_export_authorization(job: CredentialBoundWork) -> ExportAuthorizationSnapshot:
    try:
        return ExportAuthorizationSnapshot.model_validate(
            decrypt_json(job.authorization_encrypted)
        )
    except (ValueError, TypeError) as exc:
        raise ExportJobAccessDenied(
            "The accepting authorization snapshot is unavailable"
        ) from exc


def authorize_export_job(
    db: Session,
    job: CredentialBoundWork,
    *,
    lock: bool = False,
    snapshot: ExportAuthorizationSnapshot | None = None,
    required_permissions: tuple[str, ...] = (SCOPE_READ_ITEMS,),
) -> tuple[AuthorizationContext, DataAccessContext]:
    snapshot = snapshot or load_export_authorization(job)
    model = User if job.principal_type == "user" else ServiceAccount
    statement = (
        select(model)
        .where(model.id == job.principal_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(read=True)
    principal = db.scalar(statement)
    if principal is None:
        raise ExportJobAccessDenied("The export owner no longer exists")
    # Authentication mutations lock the owner before its credentials. Keep
    # that order under the publication/download policy fences too; reversing
    # it can deadlock with token revocation or browser-session rotation.
    credential = _original_credential(db, job, snapshot, lock=lock)
    scope_cap = snapshot.permissions
    if any(
        not has_required_scope(set(scope_cap), permission)
        for permission in required_permissions
    ):
        raise ExportJobAccessDenied(
            "The accepting credential did not grant article access"
        )
    if isinstance(credential, (ApiToken, ServiceAccountCredential)) and any(
        not has_required_scope(set(credential.scopes), permission)
        for permission in required_permissions
    ):
        raise ExportJobAccessDenied(
            "The accepting credential no longer grants article access"
        )
    if isinstance(principal, User):
        if (
            isinstance(credential, AuthSession)
            and credential.auth_token_version != principal.auth_token_version
        ):
            raise ExportJobAccessDenied("The accepting session was revoked")
        authorization = authorization_context_for_user(
            db, principal, credential_scopes=scope_cap
        )
    else:
        authorization = authorization_context_for_service_account(
            db,
            principal,
            credential_id=credential.id,
            credential_scopes=scope_cap,
        )
    if any(not authorization.has(permission) for permission in required_permissions):
        raise ExportJobAccessDenied("The export owner no longer has article access")
    access = data_access_context_for_authorization(db, authorization)
    if snapshot.enforced:
        access = replace(
            access,
            mode="enforced",
            allowed_label_ids=access.allowed_label_ids & snapshot.allowed_label_ids,
        )
    return authorization, access


def _original_credential(
    db: Session,
    job: CredentialBoundWork,
    snapshot: ExportAuthorizationSnapshot,
    *,
    lock: bool,
) -> ExportCredential:
    models = {
        "session_cookie": AuthSession,
        "api_token": ApiToken,
        "service_account_token": ServiceAccountCredential,
    }
    model = models[snapshot.credential_kind]
    statement = (
        select(model)
        .where(model.id == snapshot.credential_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(read=True)
    credential = db.scalar(statement)
    now = datetime.now(timezone.utc)
    if credential is None or credential.revoked_at is not None:
        raise ExportJobAccessDenied("The accepting credential was revoked")
    owner_id = (
        credential.service_account_id
        if isinstance(credential, ServiceAccountCredential)
        else credential.user_id
    )
    if owner_id != job.principal_id:
        raise ExportJobAccessDenied("The accepting credential changed owner")
    expiries = (
        (credential.idle_expires_at, credential.absolute_expires_at)
        if isinstance(credential, AuthSession)
        else (credential.expires_at,)
    )
    if any(value is not None and value <= now for value in expiries):
        raise ExportJobAccessDenied("The accepting credential expired")
    return credential


def export_source_snapshot(db: Session, item_ids: list[uuid.UUID]) -> list[list[str]]:
    rows = db.execute(
        select(Item.id, Item.feed_id, Feed.handling_label_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.id.in_(item_ids))
    ).all()
    if len(rows) != len(item_ids):
        raise ExportJobAccessDenied("Export source membership changed")
    return [[str(value) for value in row] for row in rows]


def load_export_sources(job: CredentialBoundWork) -> list[ExportSource]:
    if job.source_encrypted is None:
        return []
    try:
        return [
            ExportSource(uuid.UUID(item), uuid.UUID(feed), uuid.UUID(label))
            for item, feed, label in decrypt_json(job.source_encrypted)
        ]
    except (ValueError, TypeError) as exc:
        raise ExportJobAccessDenied(
            "The export source snapshot is unavailable"
        ) from exc


def assert_export_sources_visible(
    db: Session, job: CredentialBoundWork, access: DataAccessContext
) -> None:
    sources = load_export_sources(job)
    for offset in range(0, len(sources), 500):
        chunk = sources[offset : offset + 500]
        expected = {(source.item_id, source.feed_id) for source in chunk}
        if any(not access.allows(source.captured_label_id) for source in chunk):
            raise ExportJobAccessDenied("Export source handling access changed")
        visible = set(
            db.execute(
                select(Item.id, Item.feed_id)
                .join(Feed, Feed.id == Item.feed_id)
                .where(
                    Item.id.in_([item for item, _feed in expected]),
                    handling_label_access_predicate(Feed.handling_label_id, access),
                )
            ).all()
        )
        if visible != expected:
            raise ExportJobAccessDenied(
                "Export source membership or handling access changed"
            )


def fence_export_authorization(
    db: Session,
    job: CredentialBoundWork,
    authorization: AuthorizationContext,
    access: DataAccessContext,
    *,
    snapshot: ExportAuthorizationSnapshot | None = None,
    required_permissions: tuple[str, ...] = (SCOPE_READ_ITEMS,),
) -> None:
    """Lock global policies, then owner and credential, before publication."""
    fence_authorization_context(db, authorization)
    fence_data_access_context(db, access)
    current_authorization, current_access = authorize_export_job(
        db, job, lock=True, snapshot=snapshot, required_permissions=required_permissions
    )
    if (
        current_authorization.policy_revision != authorization.policy_revision
        or current_access != access
    ):
        raise ExportJobAccessDenied("Export authorization changed at publication")


def fence_export_job_access(
    db: Session,
    job: CredentialBoundWork,
    authorization: AuthorizationContext,
    access: DataAccessContext,
) -> None:
    fence_export_authorization(db, job, authorization, access)
    assert_export_sources_visible(db, job, access)
