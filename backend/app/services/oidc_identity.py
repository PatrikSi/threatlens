from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.rbac import ALL_ROLES, ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER
from app.core.security import get_password_hash
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import User
from app.services.oidc_client import OIDCClaims
from app.services.user_access import (
    LastActiveAdminError,
    acquire_active_admin_invariant_lock,
    ensure_active_approved_admin_remains,
    load_user_for_access_update,
    revoke_user_credentials,
)

ROLE_PRECEDENCE = {ROLE_VIEWER: 1, ROLE_ANALYST: 2, ROLE_ADMIN: 3}


class OIDCIdentityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OIDCAuthenticationResult:
    user: User
    identity: ExternalIdentity
    provisioned: bool
    previous_role: str | None = None
    role_sync_skipped: str | None = None
    revoked_api_tokens: int = 0


def resolve_oidc_role(provider: OIDCProvider, claims: dict[str, Any]) -> str:
    claim_value = _nested_claim_value(claims, provider.role_claim)
    values = {claim_value} if isinstance(claim_value, str) else {
        value for value in claim_value if isinstance(value, str)
    } if isinstance(claim_value, list) else set()

    mapped_roles = {
        str(mapping.get("role"))
        for mapping in (provider.role_mappings_json or [])
        if isinstance(mapping, dict)
        and isinstance(mapping.get("claim_value"), str)
        and mapping["claim_value"] in values
        and mapping.get("role") in ALL_ROLES
    }
    if not mapped_roles:
        return provider.default_role if provider.default_role in ALL_ROLES else ROLE_VIEWER
    return max(mapped_roles, key=lambda role: ROLE_PRECEDENCE[role])


def authenticate_oidc_identity(
    db: Session,
    provider: OIDCProvider,
    oidc_claims: OIDCClaims,
) -> OIDCAuthenticationResult:
    identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == oidc_claims.issuer,
            ExternalIdentity.subject == oidc_claims.subject,
        )
    )
    provisioned = False
    if identity is None:
        if not provider.jit_provisioning_enabled:
            raise OIDCIdentityError("not_provisioned", "No linked ThreatLens account exists for this identity")
        user, identity = _provision_identity(db, provider, oidc_claims)
        provisioned = True
    else:
        if identity.provider_id != provider.id:
            raise OIDCIdentityError("identity_conflict", "The external identity belongs to another provider")
        user = load_user_for_access_update(db, identity.user_id)
        if user is None:
            raise OIDCIdentityError("account_missing", "The linked ThreatLens account no longer exists")

    previous_role: str | None = None
    role_sync_skipped: str | None = None
    revoked_api_tokens = 0
    mapped_role = resolve_oidc_role(provider, oidc_claims.claims)
    if provider.sync_roles_on_login and user.role != mapped_role:
        previous_role, role_sync_skipped, revoked_api_tokens = _synchronize_role(db, user, mapped_role)

    now = datetime.now(timezone.utc)
    identity.last_login_at = now
    current_email = _verified_email(oidc_claims.claims, required=False)
    if current_email:
        identity.email_at_link = current_email
    db.add(identity)
    return OIDCAuthenticationResult(
        user=user,
        identity=identity,
        provisioned=provisioned,
        previous_role=previous_role,
        role_sync_skipped=role_sync_skipped,
        revoked_api_tokens=revoked_api_tokens,
    )


def link_oidc_identity(
    db: Session,
    provider: OIDCProvider,
    user: User,
    oidc_claims: OIDCClaims,
) -> ExternalIdentity:
    existing_identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == oidc_claims.issuer,
            ExternalIdentity.subject == oidc_claims.subject,
        )
    )
    if existing_identity is not None:
        if existing_identity.user_id == user.id and existing_identity.provider_id == provider.id:
            return existing_identity
        raise OIDCIdentityError("identity_in_use", "This external identity is already linked to another account")

    user_identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.provider_id == provider.id,
            ExternalIdentity.user_id == user.id,
        )
    )
    if user_identity is not None:
        raise OIDCIdentityError("account_already_linked", "This account is already linked to an external identity")

    email = _verified_email(oidc_claims.claims, required=False) or user.email
    identity = ExternalIdentity(
        provider_id=provider.id,
        user_id=user.id,
        issuer=oidc_claims.issuer,
        subject=oidc_claims.subject,
        email_at_link=email,
        last_login_at=datetime.now(timezone.utc),
    )
    try:
        with db.begin_nested():
            db.add(identity)
            db.flush()
    except IntegrityError as exc:
        raise OIDCIdentityError("identity_conflict", "The identity link changed concurrently; try again") from exc
    return identity


def unlink_oidc_identity(db: Session, provider: OIDCProvider, user: User) -> ExternalIdentity:
    if not user.password_login_enabled:
        raise OIDCIdentityError(
            "local_login_required",
            "Set a local password before unlinking the only external sign-in method",
        )
    identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.provider_id == provider.id,
            ExternalIdentity.user_id == user.id,
        )
    )
    if identity is None:
        raise OIDCIdentityError("not_linked", "No OIDC identity is linked to this account")
    db.delete(identity)
    return identity


def _provision_identity(
    db: Session,
    provider: OIDCProvider,
    oidc_claims: OIDCClaims,
) -> tuple[User, ExternalIdentity]:
    email = _provisioning_email(
        oidc_claims.claims,
        require_verified=provider.require_verified_email,
    )
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user is not None:
        raise OIDCIdentityError(
            "email_link_required",
            "A ThreatLens account already uses this email; sign in locally and link the identity from Account settings",
        )

    now = datetime.now(timezone.utc)
    user = User(
        email=email,
        password_hash=get_password_hash(secrets.token_urlsafe(48)),
        password_login_enabled=False,
        role=resolve_oidc_role(provider, oidc_claims.claims),
        is_active=True,
        is_approved=provider.auto_approve_users,
        approved_at=now if provider.auto_approve_users else None,
    )
    identity = ExternalIdentity(
        provider_id=provider.id,
        user_id=user.id,
        issuer=oidc_claims.issuer,
        subject=oidc_claims.subject,
        email_at_link=email,
        last_login_at=now,
    )
    try:
        with db.begin_nested():
            db.add(user)
            db.flush()
            identity.user_id = user.id
            db.add(identity)
            db.flush()
    except IntegrityError as exc:
        concurrent_identity = db.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.issuer == oidc_claims.issuer,
                ExternalIdentity.subject == oidc_claims.subject,
            )
        )
        if concurrent_identity is not None:
            if concurrent_identity.provider_id != provider.id:
                raise OIDCIdentityError(
                    "identity_conflict",
                    "The external identity belongs to another provider",
                ) from exc
            concurrent_user = load_user_for_access_update(db, concurrent_identity.user_id)
            if concurrent_user is not None:
                return concurrent_user, concurrent_identity
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise OIDCIdentityError(
                "email_link_required",
                "A ThreatLens account already uses this email; sign in locally and link the identity from Account settings",
            ) from exc
        raise OIDCIdentityError("provisioning_conflict", "Account provisioning changed concurrently; try again") from exc
    return user, identity


def _synchronize_role(db: Session, user: User, mapped_role: str) -> tuple[str | None, str | None, int]:
    acquire_active_admin_invariant_lock(db)
    locked_user = load_user_for_access_update(db, user.id)
    if locked_user is None:
        raise OIDCIdentityError("account_missing", "The linked ThreatLens account no longer exists")
    if locked_user.role == mapped_role:
        return None, None, 0

    previous_role = locked_user.role
    try:
        ensure_active_approved_admin_remains(
            db,
            locked_user,
            next_role=mapped_role,
            next_is_active=locked_user.is_active,
            next_is_approved=locked_user.is_approved,
        )
    except LastActiveAdminError:
        return None, "last_active_admin", 0

    locked_user.role = mapped_role
    revoked_api_tokens = revoke_user_credentials(db, locked_user)
    return previous_role, None, revoked_api_tokens


def _verified_email(claims: dict[str, Any], *, required: bool) -> str | None:
    return _normalized_email(claims, required=required, require_verified=True)


def _provisioning_email(claims: dict[str, Any], *, require_verified: bool) -> str:
    email = _normalized_email(claims, required=True, require_verified=require_verified)
    assert email is not None
    return email


def _normalized_email(
    claims: dict[str, Any],
    *,
    required: bool,
    require_verified: bool,
) -> str | None:
    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        if required:
            raise OIDCIdentityError(
                "verified_email_required",
                "The identity provider must return an email address for account provisioning",
            )
        return None
    if require_verified and claims.get("email_verified") is not True:
        if required:
            raise OIDCIdentityError(
                "verified_email_required",
                "The identity provider must verify the email address used for account provisioning",
            )
        return None
    try:
        return validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        if required:
            raise OIDCIdentityError("verified_email_required", "The identity provider returned an invalid email address") from exc
        return None


def _nested_claim_value(claims: dict[str, Any], path: str) -> Any:
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
