from __future__ import annotations

import pytest
from starlette.requests import Request
from sqlalchemy.exc import OperationalError

from app.api import deps
from app.core.logging_config import remove_log_context
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import DataPolicyUnavailable


@pytest.fixture(autouse=True)
def _isolate_data_policy_log_context():
    fields = (
        "data_policy_mode",
        "data_policy_revision",
        "data_policy_coverage_version",
    )
    remove_log_context(*fields)
    yield
    remove_log_context(*fields)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/v1/items",
            "raw_path": b"/v1/items",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def test_request_data_access_context_is_resolved_once_and_cached(
    db_session, seed_users
):
    request = _request()
    authorization = authorization_context_for_user(db_session, seed_users["admin"])
    request.state.authorization_context = authorization

    first = deps.get_data_access_context(
        request,
        _principal=seed_users["admin"],
        db=db_session,
    )
    second = deps.get_data_access_context(
        request,
        _principal=seed_users["admin"],
        db=db_session,
    )

    assert second is first
    assert request.state.data_access_context is first
    assert first.principal_id == seed_users["admin"].id
    assert first.mode == "disabled"


def test_request_data_access_context_fails_closed_without_authorization(
    db_session, seed_users
):
    request = _request()

    try:
        deps.get_data_access_context(
            request,
            _principal=seed_users["admin"],
            db=db_session,
        )
    except DataPolicyUnavailable as exc:
        assert "effective access is missing" in str(exc)
    else:
        raise AssertionError("missing authorization must fail closed")


def test_request_data_access_context_maps_storage_errors_to_retryable_policy_error(
    db_session, seed_users, monkeypatch
):
    request = _request()
    request.state.authorization_context = authorization_context_for_user(
        db_session, seed_users["admin"]
    )

    def _raise_storage_error(*_args, **_kwargs):
        raise OperationalError("SELECT policy", {}, RuntimeError("offline"))

    monkeypatch.setattr(
        deps,
        "data_access_context_for_authorization",
        _raise_storage_error,
    )

    try:
        deps.get_data_access_context(
            request,
            _principal=seed_users["admin"],
            db=db_session,
        )
    except DataPolicyUnavailable as exc:
        assert str(exc) == "Data access policy could not be loaded. Retry the request."
    else:
        raise AssertionError("storage errors must fail closed")
