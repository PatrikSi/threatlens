from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from starlette.requests import Request

from app.api import mcp_context
from app.core.api_errors import ApiHTTPException
from app.core.permissions import PERMISSION_BY_ID, SERVICE_ACCOUNT_PERMISSION_IDS
from app.core.token_scopes import (
    DEFAULT_API_TOKEN_SCOPES,
    SCOPE_READ_MCP,
    missing_role_token_scopes,
)
from app.models.user import User
from app.services import mcp_access
from app.services.export_job_access import ExportJobAccessDenied


def _request(*, headers=(), query=b""):
    return Request({
        "type": "http", "method": "POST", "path": "/v1/mcp",
        "headers": list(headers), "query_string": query,
    })


@pytest.mark.parametrize("headers,query", [
    ([(b"cookie", b"threatlens_session=tls_cookie")], b""),
    ([(b"authorization", b"Bearer tls_browser")], b""),
    ([(b"authorization", b"Bearer eyJhbGciOiJIUzI1NiJ9.jwt.signature")], b""),
    ([(b"authorization", b"Basic tlp_test_secret")], b""),
    ([(b"authorization", b"Bearer tlp_test_secret extra")], b""),
    ([(b"authorization", b"Bearer tlp_test_secret"),
      (b"authorization", b"Bearer tlp_other_secret")], b""),
    ([(b"authorization", b"Bearer tlp_test_secret")], b"access_token=tlp_uri_secret"),
    ([(b"authorization", b"Bearer tlp_test_secret")], b"API_KEY=tlp_uri_secret"),
    ([(b"authorization", b"Bearer tlp_" + b"x" * 512)], b""),
])
def test_bearer_boundary_rejects_ambiguous_or_non_api_credentials(headers, query):
    with pytest.raises(ApiHTTPException) as caught:
        mcp_context.parse_mcp_bearer_token(_request(headers=headers, query=query))
    assert caught.value.status_code == 401
    assert caught.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize("token", ["tlp_test_secret", "tlsa_test_secret"])
def test_local_bearer_compatibility_accepts_supported_token_families(token):
    request = _request(headers=[(b"authorization", f"bearer {token}".encode())])
    assert mcp_context.parse_mcp_bearer_token(request) == token


@pytest.mark.parametrize("scopes", [[], ["read:items"], ["read:*"], ["*:*"], ["admin:*"]])
def test_existing_tokens_do_not_implicitly_opt_in(monkeypatch, scopes):
    request = _request(headers=[(b"authorization", b"Bearer tlp_test_secret")])

    def authenticate(request, db, token):
        request.state.auth_credential_kind = "api_token"
        request.state.token_scopes = scopes
        request.state.authorization_context = SimpleNamespace(has=lambda value: True)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(mcp_context, "get_current_principal", authenticate)
    with pytest.raises(ApiHTTPException) as caught:
        mcp_context.resolve_mcp_read_context(request, object(), cursor_secret=b"x" * 32)
    assert caught.value.status_code == 403
    assert caught.value.error_code == "mcp_scope_required"


def test_mcp_permission_is_delegable_but_not_added_to_default_credentials():
    assert PERMISSION_BY_ID[SCOPE_READ_MCP].delegable
    assert SCOPE_READ_MCP in SERVICE_ACCOUNT_PERMISSION_IDS
    assert {"read:reports", "read:investigations", "read:teams"}.isdisjoint(
        SERVICE_ACCOUNT_PERMISSION_IDS
    )
    assert SCOPE_READ_MCP not in DEFAULT_API_TOKEN_SCOPES
    for role in ("admin", "analyst", "viewer"):
        assert missing_role_token_scopes(role, [SCOPE_READ_MCP]) == []


def _fenced_context(*, expires_at=None):
    context = SimpleNamespace()
    transaction = SimpleNamespace(is_active=True)
    db = SimpleNamespace(
        info={mcp_access._FENCE_KEY: mcp_access._ReadFence(context, transaction, expires_at)},
        get_transaction=lambda: transaction,
    )
    return db, context, transaction


def test_publication_refuses_a_transaction_reopened_after_commit():
    db, context, _transaction = _fenced_context()
    db.get_transaction = lambda: SimpleNamespace(is_active=True)
    with pytest.raises(ExportJobAccessDenied, match="transaction ended"):
        mcp_access.fence_mcp_read_context(db, context)


def test_publication_refuses_a_rolled_back_transaction():
    db, context, transaction = _fenced_context()
    transaction.is_active = False
    with pytest.raises(ExportJobAccessDenied, match="transaction ended"):
        mcp_access.fence_mcp_read_context(db, context)


def test_transfer_deadline_is_capped_by_credential_and_assertion_expiry():
    db, context, _transaction = _fenced_context(
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=2)
    )
    assert 0 < mcp_access.mcp_transfer_timeout_seconds(db, context, maximum=15) <= 2


def test_transfer_cannot_start_after_authorization_expires():
    db, context, _transaction = _fenced_context(
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    with pytest.raises(ExportJobAccessDenied, match="expired"):
        mcp_access.mcp_transfer_timeout_seconds(db, context, maximum=15)


def test_audit_omits_denied_identifiers_and_never_claims_network_delivery(monkeypatch):
    entries = []
    monkeypatch.setattr(mcp_access, "record_audit", lambda db, **entry: entries.append(entry))
    principal = User(id=uuid.uuid4(), email="reader@example.com")
    context = SimpleNamespace(
        principal=principal,
        authorization=SimpleNamespace(principal_type="user"),
        credential_snapshot=SimpleNamespace(credential_kind="api_token", credential_id=uuid.uuid4()),
    )
    request = _request()
    request.state.request_id = "test-correlation"
    for outcome in ("denied", "prepared"):
        mcp_access.record_mcp_audit(
            SimpleNamespace(info={}), request, context=context,
            operation="get_investigation", outcome=outcome,
            resource_id=uuid.uuid4(), label_ids=(),
        )
    assert all(entry["resource_id"] is None for entry in entries)
    assert all(entry["data_access_governed"] is False for entry in entries)
    assert all(entry["data_access_label_ids"] is None for entry in entries)
    assert entries[0]["success"] is False
    assert entries[1]["success"] is True
    assert entries[1]["metadata"] == {"operation": "get_investigation", "outcome": "prepared"}
    assert entries[1]["request_id"] == "test-correlation"


def test_audit_rejects_the_response_fence_session():
    db, context, _transaction = _fenced_context()
    with pytest.raises(ValueError, match="separate database session"):
        mcp_access.record_mcp_audit(
            db, _request(), context=context, operation="get_article", outcome="prepared"
        )


def test_scope_denial_is_attributed_after_successful_authentication(monkeypatch):
    entries = []
    monkeypatch.setattr(mcp_access, "record_audit", lambda db, **entry: entries.append(entry))
    user = User(id=uuid.uuid4(), email="reader@example.com")
    credential_id = uuid.uuid4()
    request = _request()
    request.state.authenticated_principal = user
    request.state.auth_credential_kind = "api_token"
    request.state.api_token_id = credential_id
    mcp_access.record_mcp_audit(
        SimpleNamespace(info={}), request, context=None,
        operation="get_article", outcome="denied",
    )
    assert entries[0]["actor_principal_id"] == user.id
    assert entries[0]["actor_principal_type"] == "user"
    assert entries[0]["credential_id"] == credential_id
    assert entries[0]["credential_kind"] == "api_token"
