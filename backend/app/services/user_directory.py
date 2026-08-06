from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import PROVISIONING_SOURCE_OIDC, User


@dataclass(frozen=True)
class UserManagementContext:
    identity: ExternalIdentity | None = None
    provider: OIDCProvider | None = None

    def authentication_methods(self, user: User) -> list[str]:
        methods: list[str] = []
        if user.password_login_enabled:
            methods.append("password")
        if self.identity is not None:
            methods.append("oidc")
        return methods

    @property
    def role_managed_by(self) -> str:
        if self.identity is not None and self.provider is not None and self.provider.sync_roles_on_login:
            return "oidc"
        return "local"

    @staticmethod
    def password_managed_by(user: User) -> str:
        return "oidc" if user.provisioning_source == PROVISIONING_SOURCE_OIDC else "local"


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
    contexts: dict[uuid.UUID, UserManagementContext] = {}
    for identity, provider in rows:
        contexts.setdefault(
            identity.user_id,
            UserManagementContext(identity=identity, provider=provider),
        )
    return contexts


def load_user_management_context(db: Session, user_id: uuid.UUID) -> UserManagementContext:
    return load_user_management_contexts(db, [user_id]).get(user_id, UserManagementContext())
