from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.rbac import ROLE_ADMIN
from app.core.config import get_settings
from app.core.security import get_password_hash
from app.models.api_token import ApiToken
from app.db.session import SessionLocal
from app.models.user import User


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
            try:
                db.commit()
                return
            except IntegrityError:
                db.rollback()
                existing = db.scalar(select(User).where(User.email == email))
                if existing is None:
                    raise

        changed = False
        if settings.seed_admin_reset_password_on_startup:
            existing.password_hash = get_password_hash(settings.admin_password)
            existing.auth_token_version = int(existing.auth_token_version or 0) + 1
            db.query(ApiToken).filter(ApiToken.user_id == existing.id).delete(synchronize_session=False)
            changed = True
        if settings.seed_admin_force_role and existing.role != ROLE_ADMIN:
            existing.role = ROLE_ADMIN
            changed = True
        if settings.seed_admin_reactivate_existing and not existing.is_active:
            existing.is_active = True
            changed = True
        if changed:
            db.add(existing)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
