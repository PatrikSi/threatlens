from types import SimpleNamespace
import uuid

from app.core.rbac import ROLE_VIEWER
from app.core.security import get_password_hash
from app.models.user import User
from app.scripts.seed_admin import seed_admin


def test_seed_admin_does_not_reactivate_or_force_role_by_default(db_session, monkeypatch):
    existing = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash=get_password_hash("InitialPass123!"),
        role=ROLE_VIEWER,
        is_active=False,
    )
    db_session.add(existing)
    db_session.commit()

    class _SessionProxy:
        def __init__(self, session):
            self._session = session

        def __getattr__(self, item):
            return getattr(self._session, item)

        def close(self):
            # Keep fixture-managed session alive for assertions.
            return None

    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", lambda: _SessionProxy(db_session))
    monkeypatch.setattr(
        "app.scripts.seed_admin.get_settings",
        lambda: SimpleNamespace(
            admin_email="admin@example.com",
            admin_password="AdminPass123!",
            seed_admin_force_role=False,
            seed_admin_reactivate_existing=False,
        ),
    )

    seed_admin()
    db_session.refresh(existing)

    assert existing.role == ROLE_VIEWER
    assert not existing.is_active
