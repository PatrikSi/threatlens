from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.db.session import SessionLocal
from app.models.user import User


def seed_admin() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        email = settings.admin_email.lower()
        existing = db.scalar(select(User).where(User.email == email))
        if existing is None:
            admin = User(email=email, password_hash=get_password_hash(settings.admin_password))
            db.add(admin)
            db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
