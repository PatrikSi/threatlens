"""Reauthorize durable exports without widening the accepting credential's scope."""
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.token_scopes import SCOPE_READ_ITEMS, has_required_scope
from app.models.api_token import ApiToken
from app.models.auth_session import AuthSession
from app.models.export_job import ExportJob
from app.models.feed import Feed
from app.models.item import Item
from app.models.service_account import ServiceAccount, ServiceAccountCredential
from app.models.user import User
from app.services.authorization import (
    AuthorizationContext, authorization_context_for_service_account,
    authorization_context_for_user, fence_authorization_context,
)
from app.services.data_access_policy import (
    DataAccessContext, data_access_context_for_authorization,
    fence_data_access_context, handling_label_access_predicate,
)
from app.services.secret_storage import decrypt_json


class ExportJobAccessDenied(RuntimeError):
    pass


def capture_export_authorization(request, authorization, data_access) -> dict:
    kind = getattr(request.state, "auth_credential_kind", None)
    credential_id = {
        "session_cookie": getattr(request.state, "auth_session_id", None),
        "api_token": getattr(request.state, "api_token_id", None),
        "service_account_token": getattr(request.state, "service_account_credential_id", None),
    }.get(kind)
    if credential_id is None or authorization is None:
        raise ExportJobAccessDenied("A durable export requires a current credential")
    return {
        "credential_kind": kind,
        "credential_id": str(credential_id),
        "permissions": sorted(authorization.permissions),
        "enforced": data_access.enforced,
        "allowed_label_ids": [str(value) for value in data_access.allowed_label_ids],
    }


def authorize_export_job(db: Session, job: ExportJob, *, lock: bool = False):
    snapshot = decrypt_json(job.authorization_encrypted)
    credential = _original_credential(db, job, snapshot, lock=lock)
    model = User if job.principal_type == "user" else ServiceAccount
    statement = select(model).where(model.id == job.principal_id).execution_options(populate_existing=True)
    if lock:
        statement = statement.with_for_update(read=True)
    principal = db.scalar(statement)
    if principal is None:
        raise ExportJobAccessDenied("The export owner no longer exists")
    scope_cap = snapshot["permissions"]
    if not has_required_scope(set(scope_cap), SCOPE_READ_ITEMS):
        raise ExportJobAccessDenied("The accepting credential did not grant article access")
    if hasattr(credential, "scopes") and not has_required_scope(set(credential.scopes), SCOPE_READ_ITEMS):
        raise ExportJobAccessDenied("The accepting credential no longer grants article access")
    if isinstance(principal, User):
        if isinstance(credential, AuthSession) and credential.auth_token_version != principal.auth_token_version:
            raise ExportJobAccessDenied("The accepting session was revoked")
        authorization = authorization_context_for_user(db, principal, credential_scopes=scope_cap)
    else:
        authorization = authorization_context_for_service_account(
            db, principal, credential_id=credential.id, credential_scopes=scope_cap,
        )
    if not authorization.has(SCOPE_READ_ITEMS):
        raise ExportJobAccessDenied("The export owner no longer has article access")
    access = data_access_context_for_authorization(db, authorization)
    if snapshot["enforced"]:
        access = replace(access, mode="enforced", allowed_label_ids=(
            access.allowed_label_ids & frozenset(uuid.UUID(value) for value in snapshot["allowed_label_ids"])
        ))
    return authorization, access


def _original_credential(db, job, snapshot, *, lock):
    models = {"session_cookie": AuthSession, "api_token": ApiToken, "service_account_token": ServiceAccountCredential}
    model = models.get(snapshot["credential_kind"])
    if model is None:
        raise ExportJobAccessDenied("Unsupported accepting credential")
    statement = select(model).where(model.id == uuid.UUID(snapshot["credential_id"])).execution_options(populate_existing=True)
    if lock:
        statement = statement.with_for_update(read=True)
    credential = db.scalar(statement)
    now = datetime.now(timezone.utc)
    if credential is None or credential.revoked_at is not None:
        raise ExportJobAccessDenied("The accepting credential was revoked")
    owner_id = credential.service_account_id if isinstance(credential, ServiceAccountCredential) else credential.user_id
    if owner_id != job.principal_id:
        raise ExportJobAccessDenied("The accepting credential changed owner")
    expiries = (
        (credential.idle_expires_at, credential.absolute_expires_at)
        if isinstance(credential, AuthSession) else (credential.expires_at,)
    )
    if any(value is not None and value <= now for value in expiries):
        raise ExportJobAccessDenied("The accepting credential expired")
    return credential


def export_source_snapshot(db, item_ids: list[uuid.UUID]) -> list[list[str]]:
    rows = db.execute(select(Item.id, Item.feed_id, Feed.handling_label_id).join(Feed, Feed.id == Item.feed_id).where(Item.id.in_(item_ids))).all()
    if len(rows) != len(item_ids):
        raise ExportJobAccessDenied("Export source membership changed")
    return [[str(value) for value in row] for row in rows]


def assert_export_sources_visible(db, job, access: DataAccessContext):
    sources = decrypt_json(job.source_encrypted) if job.source_encrypted else []
    for offset in range(0, len(sources), 500):
        chunk = sources[offset:offset + 500]
        expected = {(uuid.UUID(item), uuid.UUID(feed)) for item, feed, _label in chunk}
        if any(not access.allows(uuid.UUID(label)) for _item, _feed, label in chunk):
            raise ExportJobAccessDenied("Export source handling access changed")
        visible = set(db.execute(
            select(Item.id, Item.feed_id).join(Feed, Feed.id == Item.feed_id).where(
                Item.id.in_([item for item, _feed in expected]),
                handling_label_access_predicate(Feed.handling_label_id, access),
            )
        ).all())
        if visible != expected:
            raise ExportJobAccessDenied("Export source membership or handling access changed")


def fence_export_job_access(db, job, authorization: AuthorizationContext, access: DataAccessContext):
    # Shared policy locks are acquired only at publication/download boundaries.
    fence_authorization_context(db, authorization)
    fence_data_access_context(db, access)
    current_authorization, current_access = authorize_export_job(db, job, lock=True)
    if current_authorization.policy_revision != authorization.policy_revision or current_access != access:
        raise ExportJobAccessDenied("Export authorization changed at publication")
    assert_export_sources_visible(db, job, current_access)
