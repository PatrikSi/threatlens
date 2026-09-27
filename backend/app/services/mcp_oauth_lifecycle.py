"""Current administrator authority and bounded terminal MCP credential retention."""

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN
from app.services.credential_verification import CredentialVerificationContext
from app.models.api_token import ApiToken
from app.models.auth_session import AuthSession
from app.models.mcp_oauth import MCPDelegation, MCPOAuthClient, MCPOAuthCode
from app.models.user import User
from app.services.authorization import (
    authorization_context_for_user,
    fence_authorization_context,
    lock_iam_policy_for_mutation,
)

TERMINAL_GRANT_RETENTION = timedelta(days=1)
MAX_RETAINED_USER_GRANTS = 1000


def fence_admin_request(
    db: Session, context: CredentialVerificationContext, actor: User
) -> None:
    """Caller holds exclusive IAM; retain user then credential through mutation."""
    from app.services.mcp_oauth import oauth_error

    accepted = context.authorization
    if accepted is None:
        raise oauth_error(
            "admin_access_changed", "Sign in again before managing MCP clients.", 401
        )
    fence_authorization_context(db, accepted)
    user = db.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        user is None
        or not user.is_active
        or not user.is_approved
        or user.role != ROLE_ADMIN
    ):
        raise oauth_error(
            "admin_access_changed", "Administrator access changed. Sign in again.", 403
        )
    kind = context.credential_kind
    model, identifier = {
        "api_token": (ApiToken, context.credential_id),
        "session_cookie": (
            AuthSession,
            context.credential_id,
        ),
    }.get(kind, (None, None))
    if model is None or identifier is None:
        raise oauth_error(
            "admin_credential_required",
            "Use a current browser session or scoped API token to manage MCP clients.",
            401,
        )
    credential = db.scalar(
        select(model)
        .where(model.id == identifier)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    now = datetime.now(timezone.utc)
    if (
        credential is None
        or credential.user_id != user.id
        or credential.revoked_at is not None
    ):
        raise oauth_error(
            "admin_credential_changed",
            "The accepting administrator credential was revoked. Sign in again.",
            401,
        )
    if isinstance(credential, AuthSession):
        expiries = (credential.absolute_expires_at, credential.idle_expires_at)
        if credential.auth_token_version != user.auth_token_version:
            raise oauth_error(
                "admin_credential_changed",
                "The accepting administrator session was revoked. Sign in again.",
                401,
            )
        scopes = None
    else:
        expiries = (credential.expires_at,)
        scopes = credential.scopes
    if any(value is not None and value <= now for value in expiries):
        raise oauth_error(
            "admin_credential_changed",
            "The accepting administrator credential expired. Sign in again.",
            401,
        )
    current = authorization_context_for_user(db, user, credential_scopes=scopes)
    if not current.has("write:tokens"):
        raise oauth_error(
            "admin_access_changed",
            "Current access no longer permits MCP client administration.",
            403,
        )


def terminal_grant_candidates(
    *, now: datetime, user_id: uuid.UUID | None = None
) -> Select[tuple[uuid.UUID]]:
    terminal_at = func.coalesce(
        func.least(ApiToken.expires_at, ApiToken.revoked_at), ApiToken.created_at
    )
    statement = (
        select(ApiToken.id)
        .where(
            # Keep this fixed predicate identical to the partial index, including
            # under PostgreSQL generic prepared plans. No request text enters it.
            text("token_prefix LIKE 'tlmcp_%'"),
            ApiToken.id.in_(select(MCPDelegation.token_id)),
            terminal_at < now - TERMINAL_GRANT_RETENTION,
        )
        .order_by(terminal_at, ApiToken.id)
        .limit(100)
        .with_for_update(skip_locked=True)
    )
    if user_id is not None:
        statement = statement.where(ApiToken.user_id == user_id)
    return statement


def prune_terminal_grants(
    db: Session, *, now: datetime, user_id: uuid.UUID | None = None
) -> int:
    """Caller holds IAM or the selected user's lock; never prune usable tokens."""
    expired = terminal_grant_candidates(now=now, user_id=user_id)
    identities = list(db.scalars(expired))
    if identities:
        # Cascading removes only the delegation boundary for already unusable
        # credentials. Audit identities remain in the independent audit ledger.
        db.execute(delete(ApiToken).where(ApiToken.id.in_(identities)))
    return len(identities)


def maintain_authorization(db: Session, *, now: datetime) -> dict[str, int]:
    # Wait for bounded active MCP transfers before dropping any token boundary.
    # No user locks follow: only already-terminal tokens and empty registrations
    # can be removed. Client mutations take this same exclusive IAM fence first.
    lock_iam_policy_for_mutation(db)
    tokens = prune_terminal_grants(db, now=now)
    expired_codes = list(
        db.scalars(
            select(MCPOAuthCode.code_hash)
            .where(MCPOAuthCode.expires_at <= now)
            .order_by(MCPOAuthCode.expires_at, MCPOAuthCode.code_hash)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    )
    if expired_codes:
        db.execute(
            delete(MCPOAuthCode).where(MCPOAuthCode.code_hash.in_(expired_codes))
        )
    empty_clients = list(
        db.scalars(
            select(MCPOAuthClient.id)
            .where(
                MCPOAuthClient.revoked_at.is_not(None),
                ~select(MCPDelegation.token_id)
                .where(MCPDelegation.client_id == MCPOAuthClient.id)
                .exists(),
                ~select(MCPOAuthCode.code_hash)
                .where(MCPOAuthCode.client_id == MCPOAuthClient.id)
                .exists(),
            )
            .order_by(MCPOAuthClient.revoked_at, MCPOAuthClient.id)
            .limit(20)
            .with_for_update(skip_locked=True)
        )
    )
    if empty_clients:
        db.execute(delete(MCPOAuthClient).where(MCPOAuthClient.id.in_(empty_clients)))
    return {
        "tokens_pruned": tokens,
        "codes_pruned": len(expired_codes),
        "clients_pruned": len(empty_clients),
    }
