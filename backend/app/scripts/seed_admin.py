from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.rbac import ROLE_ADMIN
from app.core.config import get_settings
from app.core.security import get_password_hash
from app.models.api_token import ApiToken
from app.db.session import SessionLocal
from app.models.user import User
from app.services.audit import record_audit
from app.services.user_access import revoke_user_credentials


def seed_admin() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        email = settings.admin_email.lower()
        existing = db.scalar(select(User).where(User.email == email))
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
                existing = db.scalar(select(User).where(User.email == email))
                if existing is None:
                    raise

        changed = False
        credentials_rotated = False
        changed_fields: list[str] = []
        if settings.seed_admin_reset_password_on_startup:
            existing.password_hash = get_password_hash(settings.admin_password)
            existing.auth_token_version = int(existing.auth_token_version or 0) + 1
            db.query(ApiToken).filter(ApiToken.user_id == existing.id).delete(synchronize_session=False)
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
            revoked_api_tokens = 0
            if not credentials_rotated:
                revoked_api_tokens = revoke_user_credentials(db, existing)
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
                },
            )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
