from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.routes import auth, users
from app.schemas.auth import RegisterRequest
from app.schemas.user import UserCreateRequest


def _unique_violation() -> IntegrityError:
    return IntegrityError("insert", {}, RuntimeError("duplicate key value violates unique constraint"))


def test_registration_maps_concurrent_email_conflict_to_public_error(monkeypatch):
    db = MagicMock()
    db.scalar.return_value = None
    db.flush.side_effect = _unique_violation()
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(allow_self_registration=True))
    monkeypatch.setattr(auth, "resolve_client_ip", lambda _request: "203.0.113.10")
    monkeypatch.setattr(auth, "record_self_registration_attempt", lambda *_args: None)
    monkeypatch.setattr(auth, "check_self_registration_throttle", lambda *_args: SimpleNamespace(blocked=False))

    with pytest.raises(HTTPException) as raised:
        auth.register(
            RegisterRequest(email="raced@example.com", password="StrongPass123!"),
            MagicMock(),
            db,
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "Email already in use"
    db.rollback.assert_called_once_with()


def test_admin_user_create_maps_concurrent_email_conflict_to_public_error():
    db = MagicMock()
    db.scalar.return_value = None
    db.flush.side_effect = _unique_violation()
    admin = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(HTTPException) as raised:
        users.create_user(
            UserCreateRequest(email="raced@example.com", password="StrongPass123!"),
            db,
            admin,
            admin,
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "Email already in use"
    db.rollback.assert_called_once_with()
