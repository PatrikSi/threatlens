from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from email_validator import EmailNotValidError, EmailSyntaxError, validate_email
from email_validator.syntax import validate_email_domain_name, validate_email_local_part
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.rbac import ALL_ROLES, ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER
from app.core.security import get_password_hash
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import PROVISIONING_SOURCE_OIDC, User
from app.services.authorization import (
    bump_iam_policy_revision,
    lock_iam_policy_for_mutation,
)
from app.services.oidc_client import OIDCClaims
from app.services.oidc_role_provenance import mark_oidc_role_synchronized
from app.services.user_access import (
    LastActiveAdminError,
    acquire_active_admin_invariant_lock,
    ensure_active_approved_admin_remains,
    load_user_for_access_update,
    revoke_user_credentials_with_counts,
)
from app.services.investigation_ownership import (
    InvestigationOwnerReassignmentRequired,
    reconcile_user_investigation_access_change,
)

ROLE_PRECEDENCE = {ROLE_VIEWER: 1, ROLE_ANALYST: 2, ROLE_ADMIN: 3}
EMAIL_MAX_OCTETS = 254
INTERNAL_DOMAIN_VALIDATION_SUFFIX = ".x"
MAX_ROLE_CLAIM_VALUES = 256
MAX_ROLE_CLAIM_VALUE_LENGTH = 1024
MAX_ROLE_CLAIM_VALUE_BYTES = 64 * 1024


class OIDCIdentityError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        user_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.user_id = user_id
        self.details = details or {}


@dataclass(frozen=True)
class OIDCAuthenticationResult:
    user: User
    identity: ExternalIdentity
    provisioned: bool
    previous_role: str | None = None
    role_sync_skipped: str | None = None
    revoked_api_tokens: int = 0
    revoked_auth_sessions: int = 0
    cleared_investigation_assignments: int = 0


def resolve_oidc_role(provider: OIDCProvider, claims: dict[str, Any]) -> str:
    claim_present, claim_value = _nested_claim(claims, provider.role_claim)
    values = _validated_role_claim_values(
        claim_value,
        claim_present=claim_present,
        claim_path=provider.role_claim,
    )

    mapped_roles = {
        str(mapping.get("role"))
        for mapping in (provider.role_mappings_json or [])
        if isinstance(mapping, dict)
        and isinstance(mapping.get("claim_value"), str)
        and mapping["claim_value"] in values
        and mapping.get("role") in ALL_ROLES
    }
    if not mapped_roles:
        return (
            provider.default_role if provider.default_role in ALL_ROLES else ROLE_VIEWER
        )
    return max(mapped_roles, key=lambda role: ROLE_PRECEDENCE[role])


def authenticate_oidc_identity(
    db: Session,
    provider: OIDCProvider,
    oidc_claims: OIDCClaims,
    *,
    active_admin_invariant_locked: bool = False,
) -> OIDCAuthenticationResult:
    if provider.sync_roles_on_login and not active_admin_invariant_locked:
        # Role synchronization must take the invariant before any user row.
        lock_iam_policy_for_mutation(db)
        acquire_active_admin_invariant_lock(db)
    identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == oidc_claims.issuer,
            ExternalIdentity.subject == oidc_claims.subject,
        )
    )
    provisioned = False
    if identity is None:
        if not provider.jit_provisioning_enabled:
            raise OIDCIdentityError(
                "not_provisioned",
                "No linked ThreatLens account exists for this identity",
            )
        user, identity = _provision_identity(db, provider, oidc_claims)
        provisioned = True
        bump_iam_policy_revision(db)
    else:
        if identity.provider_id != provider.id:
            raise OIDCIdentityError(
                "identity_conflict", "The external identity belongs to another provider"
            )
        user = load_user_for_access_update(db, identity.user_id)
        if user is None:
            raise OIDCIdentityError(
                "account_missing", "The linked ThreatLens account no longer exists"
            )

    previous_role: str | None = None
    role_sync_skipped: str | None = None
    revoked_api_tokens = 0
    revoked_auth_sessions = 0
    cleared_investigation_assignments = 0
    mapped_role = (
        resolve_oidc_role(provider, oidc_claims.claims)
        if provider.sync_roles_on_login
        else None
    )
    if mapped_role is not None and user.role != mapped_role:
        (
            previous_role,
            role_sync_skipped,
            revoked_api_tokens,
            revoked_auth_sessions,
            cleared_investigation_assignments,
        ) = _synchronize_role(
            db,
            user,
            mapped_role,
        )
    if mapped_role is not None and role_sync_skipped is None:
        mark_oidc_role_synchronized(
            identity,
            user=user,
            applied_role=mapped_role,
            previous_role=previous_role,
        )

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
        revoked_auth_sessions=revoked_auth_sessions,
        cleared_investigation_assignments=cleared_investigation_assignments,
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
        if (
            existing_identity.user_id == user.id
            and existing_identity.provider_id == provider.id
        ):
            return existing_identity
        raise OIDCIdentityError(
            "identity_in_use",
            "This external identity is already linked to another account",
        )

    user_identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.provider_id == provider.id,
            ExternalIdentity.user_id == user.id,
        )
    )
    if user_identity is not None:
        raise OIDCIdentityError(
            "account_already_linked",
            "This account is already linked to an external identity",
        )

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
        raise OIDCIdentityError(
            "identity_conflict", "The identity link changed concurrently; try again"
        ) from exc
    return identity


def unlink_oidc_identity(
    db: Session, provider: OIDCProvider, user: User
) -> ExternalIdentity:
    if user.provisioning_source == PROVISIONING_SOURCE_OIDC:
        raise OIDCIdentityError(
            "sso_managed_account",
            "SSO-provisioned accounts cannot unlink their managed sign-in identity",
        )
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
        raise OIDCIdentityError(
            "not_linked", "No OIDC identity is linked to this account"
        )
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
        provisioning_source=PROVISIONING_SOURCE_OIDC,
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
        role_sync_provenance="tracked",
        role_sync_applied_role=user.role,
        role_sync_updated_at=now,
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
            concurrent_user = load_user_for_access_update(
                db, concurrent_identity.user_id
            )
            if concurrent_user is not None:
                return concurrent_user, concurrent_identity
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise OIDCIdentityError(
                "email_link_required",
                "A ThreatLens account already uses this email; sign in locally and link the identity from Account settings",
            ) from exc
        raise OIDCIdentityError(
            "provisioning_conflict",
            "Account provisioning changed concurrently; try again",
        ) from exc
    return user, identity


def _synchronize_role(
    db: Session,
    user: User,
    mapped_role: str,
) -> tuple[str | None, str | None, int, int, int]:
    locked_user = load_user_for_access_update(db, user.id)
    if locked_user is None:
        raise OIDCIdentityError(
            "account_missing", "The linked ThreatLens account no longer exists"
        )
    if locked_user.role == mapped_role:
        return None, None, 0, 0, 0

    previous_role = locked_user.role
    try:
        ensure_active_approved_admin_remains(
            db,
            locked_user,
            next_role=mapped_role,
            next_is_active=locked_user.is_active,
            next_is_approved=locked_user.is_approved,
        )
    except LastActiveAdminError as exc:
        _ = exc
        # Preserve the viable administrator and permit sign-in so the mapping can
        # be repaired. The skipped synchronization is emitted to the audit log.
        return None, "last_active_admin", 0, 0, 0

    try:
        investigation_access = reconcile_user_investigation_access_change(
            db,
            user=locked_user,
            next_role=mapped_role,
            next_is_active=locked_user.is_active,
            next_is_approved=locked_user.is_approved,
            actor_user_id=locked_user.id,
        )
    except InvestigationOwnerReassignmentRequired as exc:
        raise OIDCIdentityError(
            "role_sync_blocked",
            "The identity-provider role cannot be applied until investigation ownership is reassigned.",
            user_id=str(locked_user.id),
            details={
                "role_sync_reason": "investigation_owner_reassignment_required",
                "current_role": previous_role,
                "mapped_role": mapped_role,
                "affected_investigation_count": len(exc.investigations),
            },
        ) from exc

    locked_user.role = mapped_role
    bump_iam_policy_revision(db)
    revoked = revoke_user_credentials_with_counts(db, locked_user)
    return (
        previous_role,
        None,
        revoked.api_tokens,
        revoked.auth_sessions,
        investigation_access.cleared_assignment_count,
    )


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
    if email is None or (isinstance(email, str) and not email.strip()):
        if required:
            raise OIDCIdentityError(
                "email_required",
                "The identity provider must return an email address for account provisioning",
            )
        return None
    if not isinstance(email, str):
        if required:
            raise OIDCIdentityError(
                "invalid_email",
                "The identity provider returned an invalid email address",
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
        return _normalize_email_identifier(
            email, allow_internal_domain=not require_verified
        )
    except EmailNotValidError as exc:
        if required:
            raise OIDCIdentityError(
                "invalid_email",
                "The identity provider returned an invalid email address",
            ) from exc
        return None


def _normalize_email_identifier(email: str, *, allow_internal_domain: bool) -> str:
    try:
        return validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        if not allow_internal_domain:
            raise

    local_part, separator, domain = email.rpartition("@")
    if not separator:
        raise EmailSyntaxError("An email address must have an @-sign")

    local = validate_email_local_part(local_part)
    domain_with_suffix = validate_email_domain_name(
        f"{domain}{INTERNAL_DOMAIN_VALIDATION_SUFFIX}",
        globally_deliverable=False,
    )
    normalized_domain = domain_with_suffix["domain"][
        : -len(INTERNAL_DOMAIN_VALIDATION_SUFFIX)
    ]
    ascii_domain = domain_with_suffix["ascii_domain"][
        : -len(INTERNAL_DOMAIN_VALIDATION_SUFFIX)
    ]
    normalized = f"{local['local_part']}@{normalized_domain}".lower()
    ascii_local_part = local["ascii_local_part"]
    ascii_email = (
        f"{ascii_local_part}@{ascii_domain}"
        if ascii_local_part is not None
        else normalized
    )
    if any(
        len(value.encode("utf-8")) > EMAIL_MAX_OCTETS
        for value in (email, normalized, ascii_email)
    ):
        raise EmailSyntaxError("The email address is too long")
    return normalized


def _nested_claim_value(claims: dict[str, Any], path: str) -> Any:
    return _nested_claim(claims, path)[1]


def _nested_claim(claims: dict[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _validated_role_claim_values(
    value: Any,
    *,
    claim_present: bool,
    claim_path: str,
) -> set[str]:
    if not claim_present:
        return set()
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, list):
        if len(value) > MAX_ROLE_CLAIM_VALUES or not all(
            isinstance(item, str) for item in value
        ):
            raise _invalid_role_claim(claim_path)
        raw_values = value
    else:
        raise _invalid_role_claim(claim_path)
    if any(
        not item
        or item != item.strip()
        or len(item) > MAX_ROLE_CLAIM_VALUE_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in item)
        for item in raw_values
    ):
        raise _invalid_role_claim(claim_path)
    if (
        sum(len(item.encode("utf-8")) for item in raw_values)
        > MAX_ROLE_CLAIM_VALUE_BYTES
    ):
        raise _invalid_role_claim(claim_path)
    return set(raw_values)


def _invalid_role_claim(claim_path: str) -> OIDCIdentityError:
    return OIDCIdentityError(
        "role_claim_invalid",
        "The identity provider returned an invalid role claim",
        details={"claim_path": claim_path},
    )
