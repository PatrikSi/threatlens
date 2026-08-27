from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.rbac import ROLE_ADMIN
from app.core.config import get_settings
from app.core.security import get_password_hash
from app.db.session import SessionLocal
from app.models.user import User
from app.services.audit import record_audit
from app.services.user_access import revoke_user_credentials_with_counts


def seed_admin() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        email = settings.admin_email.lower()
        existing = db.scalar(
            select(User)
            .where(User.email == email)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if existing is None:
            admin = User(
                email=email,
                password_hash=get_password_hash(settings.admin_password),
                role=ROLE_ADMIN,
                is_active=True,
            )
            db.add(admin)
            db.flush()
            record_audit(
                db,
                actor_user_id=None,
                action="system.seed_admin.create",
                resource_type="user",
                resource_id=str(admin.id),
                metadata={"email": email},
            )
            try:
                db.commit()
                return
            except IntegrityError:
                db.rollback()
                existing = db.scalar(
                    select(User)
                    .where(User.email == email)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if existing is None:
                    raise

        changed = False
        credentials_rotated = False
        revoked_api_tokens = 0
        revoked_auth_sessions = 0
        changed_fields: list[str] = []
        if settings.seed_admin_reset_password_on_startup:
            existing.password_hash = get_password_hash(settings.admin_password)
            revoked = revoke_user_credentials_with_counts(db, existing)
            revoked_api_tokens = revoked.api_tokens
            revoked_auth_sessions = revoked.auth_sessions
            changed = True
            credentials_rotated = True
            changed_fields.append("password")
        if settings.seed_admin_force_role and existing.role != ROLE_ADMIN:
            existing.role = ROLE_ADMIN
            changed = True
            changed_fields.append("role")
        if settings.seed_admin_reactivate_existing and not existing.is_active:
            existing.is_active = True
            changed = True
            changed_fields.append("is_active")
        if changed:
            if not credentials_rotated:
                revoked = revoke_user_credentials_with_counts(db, existing)
                revoked_api_tokens = revoked.api_tokens
                revoked_auth_sessions = revoked.auth_sessions
            db.add(existing)
            record_audit(
                db,
                actor_user_id=None,
                action="system.seed_admin.update",
                resource_type="user",
                resource_id=str(existing.id),
                metadata={
                    "email": email,
                    "changed_fields": changed_fields,
                    "auth_token_version": int(existing.auth_token_version or 0),
                    "revoked_api_tokens": int(revoked_api_tokens),
                    "revoked_auth_sessions": int(revoked_auth_sessions),
                },
            )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
