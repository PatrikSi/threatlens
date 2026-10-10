from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.rbac import ROLE_ADMIN, ROLE_VIEWER
from app.core.security import get_password_hash, verify_password
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.auth import LoginRequest
from app.scripts.seed_admin import seed_admin


@pytest.mark.parametrize("email", [
    "bootstrap-review@example.test",
    "bootstrap-review@example.invalid",
    "invalid-address",
    "a..b@example.com",
])
def test_seed_admin_bootstrap_email_rejects_invalid_addresses_before_session(email, monkeypatch):
    password = "private-bootstrap-password-must-not-appear"
    session = Mock(side_effect=AssertionError("Database session opened before identity validation"))
    password_hash, audit, revoke = Mock(), Mock(), Mock()
    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", session)
    monkeypatch.setattr("app.scripts.seed_admin.get_password_hash", password_hash)
    monkeypatch.setattr("app.scripts.seed_admin.record_audit", audit)
    monkeypatch.setattr("app.scripts.seed_admin.revoke_user_credentials_with_counts", revoke)
    monkeypatch.setattr("app.scripts.seed_admin.get_settings", lambda: SimpleNamespace(
        admin_email=email, admin_password=password,
    ))

    with pytest.raises(ValueError, match="ADMIN_EMAIL must be a valid login email address") as error:
        seed_admin()

    assert str(error.value) == "ADMIN_EMAIL must be a valid login email address"
    assert email not in str(error.value) and password not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__ is True
    session.assert_not_called()
    password_hash.assert_not_called()
    audit.assert_not_called()
    revoke.assert_not_called()


@pytest.mark.parametrize("email", [
    "  SOC.Admin@Security.Example.COM  ",
    "SOC.Admin@Security.Example.COM",
    "analyst+review@example.com",
])
def test_seed_admin_bootstrap_email_matches_login_identity_without_changing_existing_user(email, monkeypatch):
    password = "ValidBootstrapPassword123!"
    expected_email = LoginRequest(email=email, password=password).email.lower()
    existing = SimpleNamespace(role=ROLE_VIEWER, is_active=False)
    db = Mock()
    db.scalar.return_value = existing
    password_hash, audit, revoke = Mock(), Mock(), Mock()
    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", Mock(return_value=db))
    monkeypatch.setattr("app.scripts.seed_admin.get_password_hash", password_hash)
    monkeypatch.setattr("app.scripts.seed_admin.record_audit", audit)
    monkeypatch.setattr("app.scripts.seed_admin.revoke_user_credentials_with_counts", revoke)
    monkeypatch.setattr("app.scripts.seed_admin.get_settings", lambda: SimpleNamespace(
        admin_email=email, admin_password=password, seed_admin_force_role=False,
        seed_admin_reactivate_existing=False, seed_admin_reset_password_on_startup=False,
    ))

    seed_admin()

    statement = db.scalar.call_args.args[0]
    assert statement.compile().params["email_1"] == expected_email
    assert existing.role == ROLE_VIEWER and existing.is_active is False
    db.commit.assert_called_once_with()
    db.close.assert_called_once_with()
    db.add.assert_not_called()
    password_hash.assert_not_called()
    audit.assert_not_called()
    revoke.assert_not_called()


@pytest.mark.parametrize("password", [
    pytest.param("", id="empty"),
    pytest.param("p" * 257, id="over-login-limit"),
])
def test_seed_admin_bootstrap_password_rejects_login_bounds_before_session(password, monkeypatch):
    email = "bootstrap-review@example.com"
    with pytest.raises(ValidationError) as login_error:
        LoginRequest(email=email, password=password)
    assert any(error["loc"] == ("password",) for error in login_error.value.errors(include_input=False))

    session = Mock(side_effect=AssertionError("Database session opened before credential validation"))
    password_hash, audit, revoke = Mock(), Mock(), Mock()
    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", session)
    monkeypatch.setattr("app.scripts.seed_admin.get_password_hash", password_hash)
    monkeypatch.setattr("app.scripts.seed_admin.record_audit", audit)
    monkeypatch.setattr("app.scripts.seed_admin.revoke_user_credentials_with_counts", revoke)
    monkeypatch.setattr("app.scripts.seed_admin.get_settings", lambda: SimpleNamespace(
        admin_email=email, admin_password=password,
    ))

    with pytest.raises(ValueError, match="ADMIN_PASSWORD must be between 1 and 256 characters") as error:
        seed_admin()

    assert str(error.value) == "ADMIN_PASSWORD must be between 1 and 256 characters"
    assert email not in str(error.value)
    if password:
        assert password not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__ is True
    session.assert_not_called()
    password_hash.assert_not_called()
    audit.assert_not_called()
    revoke.assert_not_called()


@pytest.mark.parametrize("password", [
    pytest.param("p", id="login-minimum"),
    pytest.param("p" * 256, id="login-maximum"),
    pytest.param("ValidBootstrapPassword123!", id="ordinary"),
])
def test_seed_admin_bootstrap_password_accepts_login_bounds_without_changing_existing_user(password, monkeypatch):
    email = "  SOC.Admin@Security.Example.COM  "
    expected_email = LoginRequest(email=email, password=password).email.lower()
    existing = SimpleNamespace(role=ROLE_VIEWER, is_active=False)
    db = Mock()
    db.scalar.return_value = existing
    session = Mock(return_value=db)
    password_hash, audit, revoke = Mock(), Mock(), Mock()
    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", session)
    monkeypatch.setattr("app.scripts.seed_admin.get_password_hash", password_hash)
    monkeypatch.setattr("app.scripts.seed_admin.record_audit", audit)
    monkeypatch.setattr("app.scripts.seed_admin.revoke_user_credentials_with_counts", revoke)
    monkeypatch.setattr("app.scripts.seed_admin.get_settings", lambda: SimpleNamespace(
        admin_email=email, admin_password=password, seed_admin_force_role=False,
        seed_admin_reactivate_existing=False, seed_admin_reset_password_on_startup=False,
    ))

    seed_admin()

    session.assert_called_once_with()
    statement = db.scalar.call_args.args[0]
    assert statement.compile().params["email_1"] == expected_email
    assert existing.role == ROLE_VIEWER and existing.is_active is False
    db.commit.assert_called_once_with()
    db.close.assert_called_once_with()
    db.add.assert_not_called()
    password_hash.assert_not_called()
    audit.assert_not_called()
    revoke.assert_not_called()


def test_seed_admin_bootstrap_password_keeps_email_error_priority(monkeypatch):
    session = Mock(side_effect=AssertionError("Database session opened before credential validation"))
    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", session)
    monkeypatch.setattr("app.scripts.seed_admin.get_settings", lambda: SimpleNamespace(
        admin_email="bootstrap-review@example.test", admin_password="",
    ))

    with pytest.raises(ValueError, match="ADMIN_EMAIL must be a valid login email address") as error:
        seed_admin()

    assert str(error.value) == "ADMIN_EMAIL must be a valid login email address"
    assert error.value.__cause__ is None and error.value.__suppress_context__ is True
    session.assert_not_called()


@pytest.mark.parametrize("separator", [
    pytest.param("\r", id="carriage-return"),
    pytest.param("\n", id="line-feed"),
    pytest.param("\r\n", id="carriage-return-line-feed"),
])
def test_seed_admin_bootstrap_password_single_line_rejects_browser_stripped_characters(separator, monkeypatch):
    email = "bootstrap-review@example.com"
    password = f"Bootstrap{separator}Password123!"
    # LoginRequest preserves these characters, but the browser's password
    # input strips them, making such a seeded password impossible to enter.
    assert LoginRequest(email=email, password=password).password == password
    session = Mock(side_effect=AssertionError("Database session opened before credential validation"))
    password_hash, audit, revoke = Mock(), Mock(), Mock()
    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", session)
    monkeypatch.setattr("app.scripts.seed_admin.get_password_hash", password_hash)
    monkeypatch.setattr("app.scripts.seed_admin.record_audit", audit)
    monkeypatch.setattr("app.scripts.seed_admin.revoke_user_credentials_with_counts", revoke)
    monkeypatch.setattr("app.scripts.seed_admin.get_settings", lambda: SimpleNamespace(
        admin_email=email, admin_password=password,
    ))

    with pytest.raises(ValueError, match="ADMIN_PASSWORD must be a single-line login password") as error:
        seed_admin()

    assert str(error.value) == "ADMIN_PASSWORD must be a single-line login password"
    assert email not in str(error.value) and password not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__ is True
    session.assert_not_called()
    password_hash.assert_not_called()
    audit.assert_not_called()
    revoke.assert_not_called()


@pytest.mark.parametrize("failure_phase", ["flush", "audit", "commit"])
@pytest.mark.parametrize("winner_available", [True, False], ids=["winner-reloaded", "no-winner"])
def test_seed_admin_create_integrity_failure_recovers_or_reraises_without_credential_changes(
    failure_phase, winner_available, monkeypatch,
):
    winner = SimpleNamespace(
        password_hash="winner-password-hash", role=ROLE_VIEWER, is_active=False,
        auth_token_version=7, password_login_enabled=False,
    )
    original_winner = vars(winner).copy()
    conflict = IntegrityError("insert", {}, Exception("synthetic unique conflict"))
    db = Mock()
    db.scalar.side_effect = [None, winner if winner_available else None]
    audit, revoke = Mock(), Mock()
    if failure_phase == "flush":
        db.flush.side_effect = conflict
    elif failure_phase == "audit":
        audit.side_effect = conflict
    else:
        db.commit.side_effect = [conflict, None]
    password_hash = Mock(return_value="discarded-candidate-password-hash")
    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", Mock(return_value=db))
    monkeypatch.setattr("app.scripts.seed_admin.get_password_hash", password_hash)
    monkeypatch.setattr("app.scripts.seed_admin.record_audit", audit)
    monkeypatch.setattr("app.scripts.seed_admin.revoke_user_credentials_with_counts", revoke)
    monkeypatch.setattr("app.scripts.seed_admin.get_settings", lambda: SimpleNamespace(
        admin_email="ADMIN@example.com", admin_password="AdminPass123!",
        seed_admin_force_role=False, seed_admin_reactivate_existing=False,
        seed_admin_reset_password_on_startup=False,
    ))

    if winner_available:
        seed_admin()
    else:
        with pytest.raises(IntegrityError) as raised:
            seed_admin()
        assert raised.value is conflict

    assert vars(winner) == original_winner
    password_hash.assert_called_once_with("AdminPass123!")
    revoke.assert_not_called()
    db.add.assert_called_once()
    candidate = db.add.call_args.args[0]
    assert candidate.email == "admin@example.com" and candidate.role == ROLE_ADMIN
    assert candidate.password_hash == "discarded-candidate-password-hash"
    db.flush.assert_called_once_with()
    db.rollback.assert_called_once_with()
    assert db.scalar.call_count == 2
    for lookup in db.scalar.call_args_list:
        statement = lookup.args[0]
        assert statement.compile().params["email_1"] == "admin@example.com"
        assert statement._for_update_arg is not None
        assert statement.get_execution_options()["populate_existing"] is True
    rollback_index = next(i for i, call in enumerate(db.mock_calls) if call[0] == "rollback")
    lookup_indices = [i for i, call in enumerate(db.mock_calls) if call[0] == "scalar"]
    assert lookup_indices[0] < rollback_index < lookup_indices[1]
    assert db.commit.call_count == int(failure_phase == "commit") + int(winner_available)
    if failure_phase == "flush":
        audit.assert_not_called()
    else:
        audit.assert_called_once()
        assert audit.call_args.kwargs["action"] == "system.seed_admin.create"
    db.close.assert_called_once_with()


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


def test_seed_admin_handles_real_unique_flush_conflict_without_changing_winner(db_session, monkeypatch):
    winner = User(
        id=uuid.uuid4(), email="admin@example.com",
        password_hash=get_password_hash("RacedPass123!"), role=ROLE_VIEWER,
        is_active=False, password_login_enabled=False, auth_token_version=7,
    )
    db_session.add(winner)
    db_session.flush()
    token = ApiToken(
        id=uuid.uuid4(), user_id=winner.id, name="winner-token",
        token_prefix="tl_flush_race", token_hash="flush-race-token-hash", scopes=[],
    )
    db_session.add(token)
    db_session.commit()
    winner_hash = winner.password_hash

    class _SessionProxy:
        def __init__(self, session):
            self._session = session
            self.lookups = 0
            self.rollbacks = 0
            self.flush_conflict = None

        def __getattr__(self, item):
            return getattr(self._session, item)

        def scalar(self, statement):
            self.lookups += 1
            if self.lookups == 1:
                # Reproduce the seeder's stale absence check while a winner
                # already exists. The duplicate INSERT uses PostgreSQL itself.
                return None
            return self._session.scalar(statement)

        def flush(self):
            try:
                return self._session.flush()
            except IntegrityError as error:
                self.flush_conflict = error
                raise

        def rollback(self):
            self.rollbacks += 1
            return self._session.rollback()

        def close(self):
            # The fixture owns the outer transaction and cleanup.
            return None

    proxy = _SessionProxy(db_session)
    monkeypatch.setattr("app.scripts.seed_admin.SessionLocal", lambda: proxy)
    monkeypatch.setattr("app.scripts.seed_admin.get_settings", lambda: SimpleNamespace(
        admin_email="ADMIN@example.com", admin_password="AdminPass123!",
        seed_admin_force_role=False, seed_admin_reactivate_existing=False,
        seed_admin_reset_password_on_startup=False,
    ))

    seed_admin()

    assert proxy.flush_conflict is not None
    assert proxy.flush_conflict.orig.sqlstate == "23505"
    assert proxy.rollbacks == 1 and proxy.lookups == 2
    users = db_session.scalars(select(User).where(User.email == "admin@example.com")).all()
    assert len(users) == 1 and users[0].id == winner.id
    assert users[0].password_hash == winner_hash
    assert verify_password("RacedPass123!", users[0].password_hash)
    assert users[0].role == ROLE_VIEWER and users[0].is_active is False
    assert users[0].password_login_enabled is False and users[0].auth_token_version == 7
    db_session.refresh(token)
    assert token.revoked_at is None
    assert db_session.scalar(select(AuditLog).where(
        AuditLog.action.in_(["system.seed_admin.create", "system.seed_admin.update"]),
    )) is None
