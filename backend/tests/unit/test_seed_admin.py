from types import SimpleNamespace
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.rbac import ROLE_ADMIN, ROLE_VIEWER
from app.core.security import get_password_hash, verify_password
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
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
            seed_admin_reset_password_on_startup=False,
        ),
    )

    seed_admin()
    db_session.refresh(existing)

    assert existing.role == ROLE_VIEWER
    assert not existing.is_active


def test_seed_admin_can_reset_existing_password(db_session, monkeypatch):
    existing = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash=get_password_hash("InitialPass123!"),
        role=ROLE_VIEWER,
        is_active=True,
    )
    db_session.add(existing)
    db_session.flush()
    api_token = ApiToken(
        id=uuid.uuid4(),
        user_id=existing.id,
        name="existing-token",
        token_prefix="tl_test",
        token_hash="hash",
        scopes=[],
    )
    db_session.add(api_token)
    db_session.commit()

    class _SessionProxy:
        def __init__(self, session):
            self._session = session

        def __getattr__(self, item):
            return getattr(self._session, item)

        def close(self):
            return None

    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", lambda: _SessionProxy(db_session))
    monkeypatch.setattr(
        "app.scripts.seed_admin.get_settings",
        lambda: SimpleNamespace(
            admin_email="admin@example.com",
            admin_password="AdminPass123!",
            seed_admin_force_role=False,
            seed_admin_reactivate_existing=False,
            seed_admin_reset_password_on_startup=True,
        ),
    )

    seed_admin()
    db_session.refresh(existing)

    assert verify_password("AdminPass123!", existing.password_hash)
    assert existing.auth_token_version == 1
    db_session.refresh(api_token)
    assert api_token.revoked_at is not None


def test_seed_admin_role_and_reactivation_rotate_credentials_and_audit(db_session, monkeypatch):
    existing = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash=get_password_hash("InitialPass123!"),
        role=ROLE_VIEWER,
        is_active=False,
    )
    db_session.add(existing)
    db_session.flush()
    api_token = ApiToken(
        id=uuid.uuid4(),
        user_id=existing.id,
        name="existing-token",
        token_prefix="tl_role_change",
        token_hash="role-change-hash",
        scopes=[],
    )
    db_session.add(api_token)
    db_session.commit()

    class _SessionProxy:
        def __init__(self, session):
            self._session = session

        def __getattr__(self, item):
            return getattr(self._session, item)

        def close(self):
            return None

    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", lambda: _SessionProxy(db_session))
    monkeypatch.setattr(
        "app.scripts.seed_admin.get_settings",
        lambda: SimpleNamespace(
            admin_email="admin@example.com",
            admin_password="AdminPass123!",
            seed_admin_force_role=True,
            seed_admin_reactivate_existing=True,
            seed_admin_reset_password_on_startup=False,
        ),
    )

    seed_admin()
    db_session.refresh(existing)
    db_session.refresh(api_token)

    assert existing.role == ROLE_ADMIN
    assert existing.is_active is True
    assert existing.auth_token_version == 1
    assert api_token.revoked_at is not None
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "system.seed_admin.update"))
    assert audit is not None
    assert audit.metadata_json["changed_fields"] == ["role", "is_active"]


def test_seed_admin_handles_concurrent_create_conflict(db_session, monkeypatch):
    class _SessionProxy:
        def __init__(self, session):
            self._session = session
            self._first_commit = True

        def __getattr__(self, item):
            return getattr(self._session, item)

        def commit(self):
            if self._first_commit:
                self._first_commit = False
                self._session.rollback()
                self._session.add(
                    User(
                        id=uuid.uuid4(),
                        email="admin@example.com",
                        password_hash=get_password_hash("RacedPass123!"),
                        role=ROLE_VIEWER,
                        is_active=True,
                    )
                )
                self._session.commit()
                raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))
            return self._session.commit()

        def close(self):
            return None

    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", lambda: _SessionProxy(db_session))
    monkeypatch.setattr(
        "app.scripts.seed_admin.get_settings",
        lambda: SimpleNamespace(
            admin_email="admin@example.com",
            admin_password="AdminPass123!",
            seed_admin_force_role=False,
            seed_admin_reactivate_existing=False,
            seed_admin_reset_password_on_startup=False,
        ),
    )

    seed_admin()

    users = db_session.scalars(select(User).where(User.email == "admin@example.com")).all()
    assert len(users) == 1
    assert verify_password("RacedPass123!", users[0].password_hash)
