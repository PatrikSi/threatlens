from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.orm import Session

from app.models.auth_session import AuthSession
from app.models.mfa import UserTOTPCredential
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import PROVISIONING_SOURCE_LOCAL, PROVISIONING_SOURCE_OIDC, User


@dataclass(frozen=True)
class UserManagementContext:
    identity: ExternalIdentity | None = None
    provider: OIDCProvider | None = None
    mfa_confirmed_at: datetime | None = None
    mfa_enabled: bool = False
    active_session_count: int = 0

    def authentication_methods(self, user: User) -> list[str]:
        methods: list[str] = []
        if user.password_login_enabled:
            methods.append("password")
        if self.identity is not None:
            methods.append("oidc")
        return methods

    @property
    def role_managed_by(self) -> str:
        if (
            self.identity is not None
            and self.provider is not None
            and self.provider.sync_roles_on_login
        ):
            return "oidc"
        return "local"

    @staticmethod
    def password_managed_by(user: User) -> str:
        return (
            "oidc" if user.provisioning_source == PROVISIONING_SOURCE_OIDC else "local"
        )


def user_directory_search_filter(query: str) -> ColumnElement[bool]:
    """Match the same account labels and identity fields presented by the directory UI."""

    normalized = query.strip().lower()
    identity_exists = exists(
        select(ExternalIdentity.id).where(ExternalIdentity.user_id == User.id)
    )
    conditions: list[ColumnElement[bool]] = [
        func.lower(User.email).contains(normalized, autoescape=True),
        func.lower(User.role).contains(normalized, autoescape=True),
        exists(
            select(ExternalIdentity.id)
            .join(OIDCProvider, OIDCProvider.id == ExternalIdentity.provider_id)
            .where(
                ExternalIdentity.user_id == User.id,
                func.lower(OIDCProvider.name).contains(normalized, autoescape=True),
            )
        ),
    ]

    if len(normalized) >= 2 and "approved".startswith(normalized):
        conditions.append(User.is_approved.is_(True))
    if len(normalized) >= 2 and "pending".startswith(normalized):
        conditions.append(User.is_approved.is_(False))
    if len(normalized) >= 3 and "active".startswith(normalized):
        conditions.append(User.is_active.is_(True))
    if len(normalized) >= 3 and "inactive".startswith(normalized):
        conditions.append(User.is_active.is_(False))
    if normalized in {"enabled", "enabled account"}:
        conditions.append(User.is_active.is_(True))
    if normalized in {"disabled", "disabled account"}:
        conditions.append(User.is_active.is_(False))

    if normalized in {"local", "local account", "password", "password login"}:
        conditions.append(User.provisioning_source == PROVISIONING_SOURCE_LOCAL)
    if normalized in {
        "sso",
        "oidc",
        "sso-provisioned",
        "sso provisioned",
        "identity provider",
    }:
        conditions.append(
            or_(User.provisioning_source == PROVISIONING_SOURCE_OIDC, identity_exists)
        )
    if normalized in {"hybrid", "local + sso", "local and sso"}:
        conditions.append(
            and_(User.provisioning_source == PROVISIONING_SOURCE_LOCAL, identity_exists)
        )

    return or_(*conditions)


def load_user_management_contexts(
    db: Session,
    user_ids: list[uuid.UUID],
) -> dict[uuid.UUID, UserManagementContext]:
    if not user_ids:
        return {}

    rows = db.execute(
        select(ExternalIdentity, OIDCProvider)
        .join(OIDCProvider, OIDCProvider.id == ExternalIdentity.provider_id)
        .where(ExternalIdentity.user_id.in_(user_ids))
        .order_by(ExternalIdentity.created_at.asc())
    ).all()
    identities: dict[uuid.UUID, tuple[ExternalIdentity, OIDCProvider]] = {}
    for identity, provider in rows:
        identities.setdefault(identity.user_id, (identity, provider))

    mfa_rows = dict(
        db.execute(
            select(UserTOTPCredential.user_id, UserTOTPCredential.confirmed_at).where(
                UserTOTPCredential.user_id.in_(user_ids),
                UserTOTPCredential.status == "active",
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    session_rows = dict(
        db.execute(
            select(AuthSession.user_id, func.count(AuthSession.id))
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.user_id.in_(user_ids),
                AuthSession.auth_token_version == User.auth_token_version,
                AuthSession.revoked_at.is_(None),
                AuthSession.idle_expires_at > now,
                AuthSession.absolute_expires_at > now,
            )
            .group_by(AuthSession.user_id)
        ).all()
    )
    return {
        user_id: UserManagementContext(
            identity=identities.get(user_id, (None, None))[0],
            provider=identities.get(user_id, (None, None))[1],
            mfa_confirmed_at=mfa_rows.get(user_id),
            mfa_enabled=user_id in mfa_rows,
            active_session_count=int(session_rows.get(user_id, 0)),
        )
        for user_id in user_ids
    }


def load_user_management_context(
    db: Session, user_id: uuid.UUID
) -> UserManagementContext:
    return load_user_management_contexts(db, [user_id]).get(
        user_id, UserManagementContext()
    )
