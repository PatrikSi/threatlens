from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import event, inspect, select, update
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from starlette.requests import Request

from app.core.api_errors import ApiHTTPException
from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.iam import IAMRole, IAMRolePermission
from app.models.service_account import (
    ServiceAccount, ServiceAccountCredential, ServiceAccountRoleAssignment,
)
from app.services.export_job_access import ExportJobAccessDenied
from app.services.mcp_access import (
    resolve_mcp_read_context, fence_mcp_read_context, record_mcp_audit,
)
from app.services.user_access import revoke_user_credentials_with_counts
from app.services.service_accounts import _generate_service_account_token


def _token_request(db, user, *, scopes=None, expires_at=None):
    token, prefix, token_hash = generate_api_token()
    credential = ApiToken(
        user_id=user.id, name="MCP lifecycle test", token_prefix=prefix,
        token_hash=token_hash, scopes=scopes or ["read:mcp", "read:items"],
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=1),
        last_used_at=datetime.now(timezone.utc),
    )
    db.add(credential)
    db.commit()
    request = Request({
        "type": "http", "method": "POST", "path": "/v1/mcp", "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })
    return request, credential


def test_mcp_token_authentication_preserves_credential_attenuation(db_session, seed_users):
    request, credential = _token_request(db_session, seed_users["viewer"])
    context = resolve_mcp_read_context(request, db_session, cursor_secret=b"x" * 32)
    assert context.credential_snapshot.credential_id == credential.id
    assert context.authorization.has("read:items")
    assert not context.authorization.has("read:reports")
    fence_mcp_read_context(db_session, context, required_permissions=("read:items",))
    with pytest.raises(ExportJobAccessDenied):
        fence_mcp_read_context(db_session, context, required_permissions=("read:reports",))


@pytest.mark.parametrize("change", ["revoked", "expired", "scope_removed"])
def test_mcp_rechecks_credential_state_at_publication(db_session, seed_users, change):
    request, credential = _token_request(db_session, seed_users["viewer"])
    context = resolve_mcp_read_context(request, db_session, cursor_secret=b"x" * 32)
    values = {
        "revoked": {"revoked_at": datetime.now(timezone.utc)},
        "expired": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
        "scope_removed": {"scopes": ["read:*"]},
    }[change]
    db_session.execute(
        update(ApiToken).where(ApiToken.id == credential.id).values(**values)
        .execution_options(synchronize_session=False)
    )
    with pytest.raises((ExportJobAccessDenied, ApiHTTPException)):
        fence_mcp_read_context(db_session, context, required_permissions=("read:items",))


def test_mcp_revoke_all_credentials_applies_to_an_accepted_read(db_session, seed_users):
    user = seed_users["viewer"]
    request, _credential = _token_request(db_session, user)
    context = resolve_mcp_read_context(request, db_session, cursor_secret=b"x" * 32)
    revoke_user_credentials_with_counts(db_session, user, reason="test_revoke_all")
    with pytest.raises(ExportJobAccessDenied, match="revoked"):
        fence_mcp_read_context(db_session, context)


@pytest.mark.parametrize("end_transaction", ["commit", "rollback"])
def test_mcp_cannot_reestablish_a_lost_read_fence(db_session, seed_users, end_transaction):
    request, _credential = _token_request(db_session, seed_users["viewer"])
    context = resolve_mcp_read_context(request, db_session, cursor_secret=b"x" * 32)
    getattr(db_session, end_transaction)()
    db_session.begin()
    with pytest.raises(ExportJobAccessDenied, match="transaction ended"):
        fence_mcp_read_context(db_session, context)


def test_mcp_rejects_an_expired_token_before_resolving_access(db_session, seed_users):
    request, _credential = _token_request(
        db_session, seed_users["viewer"],
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(HTTPException) as caught:
        resolve_mcp_read_context(request, db_session, cursor_secret=b"x" * 32)
    assert caught.value.status_code == 401


def test_mcp_service_credentials_remain_scoped_and_revocable(db_session):
    suffix = uuid.uuid4().hex[:12]
    account = ServiceAccount(key=f"mcp-{suffix}", name="MCP test account")
    role = IAMRole(key=f"mcp-role-{suffix}", name="MCP test role", is_system=False)
    db_session.add_all([account, role])
    db_session.flush()
    db_session.add(ServiceAccountRoleAssignment(service_account_id=account.id, role_id=role.id))
    db_session.add_all([
        IAMRolePermission(role_id=role.id, permission="read:mcp"),
        IAMRolePermission(role_id=role.id, permission="read:items"),
    ])
    token, prefix, token_hash = _generate_service_account_token()
    credential = ServiceAccountCredential(
        service_account_id=account.id, name="MCP test credential",
        token_prefix=prefix, token_hash=token_hash,
        scopes=["read:mcp", "read:items"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        last_used_at=datetime.now(timezone.utc),
    )
    db_session.add(credential)
    db_session.commit()
    request = Request({
        "type": "http", "method": "POST", "path": "/v1/mcp", "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })
    context = resolve_mcp_read_context(request, db_session, cursor_secret=b"x" * 32)
    assert context.authorization.principal_type == "service_account"
    assert context.authorization.has("read:items")
    assert not context.authorization.has("read:investigations")
    fence_mcp_read_context(db_session, context, required_permissions=("read:items",))
    db_session.execute(
        update(ServiceAccountCredential).where(ServiceAccountCredential.id == credential.id)
        .values(revoked_at=datetime.now(timezone.utc)).execution_options(synchronize_session=False)
    )
    with pytest.raises(ExportJobAccessDenied, match="revoked"):
        fence_mcp_read_context(db_session, context)


@pytest.mark.parametrize("audit_path", ["context", "scope_denial", "manual_context"])
def test_failure_audit_never_refreshes_the_rolled_back_read_session(
    db_session, seed_users, audit_path,
):
    user = seed_users["viewer"]
    request, credential = _token_request(
        db_session, user,
        scopes=["*:*"] if audit_path == "scope_denial" else None,
    )
    context = None
    if audit_path == "scope_denial":
        with pytest.raises(ApiHTTPException) as caught:
            resolve_mcp_read_context(request, db_session, cursor_secret=b"x" * 32)
        assert caught.value.status_code == 403
    else:
        context = resolve_mcp_read_context(request, db_session, cursor_secret=b"x" * 32)
    identity = request.state.mcp_audit_identity
    expected_user_id, expected_credential_id = identity.principal_id, credential.id
    if audit_path == "manual_context":
        del request.state.mcp_audit_identity
    db_session.rollback()
    assert inspect(user).expired

    def reject_original_session_query(_state):
        raise AssertionError("Failure auditing must not refresh the original read session")

    event.listen(db_session, "do_orm_execute", reject_original_session_query)
    try:
        # Use the fixture's existing connection/savepoint only to keep this
        # isolated test transactional; production has a separately pinned audit
        # connection. The original ORM session must perform no further SQL.
        with Session(bind=db_session.get_bind(), join_transaction_mode="create_savepoint") as audit_db:
            record_mcp_audit(
                audit_db, request, context=context,
                operation="get_article_evidence", outcome="failed",
            )
            audit = audit_db.scalar(select(AuditLog).where(
                AuditLog.action == "mcp.read", AuditLog.credential_id == expected_credential_id,
            ))
            assert audit is not None
            assert audit.actor_principal_id == expected_user_id
            assert audit.credential_id == expected_credential_id
            assert audit.actor_label_snapshot == (
                "MCP user" if audit_path == "manual_context" else identity.actor_label
            )
            assert audit.metadata_json["outcome"] == "failed"
    finally:
        event.remove(db_session, "do_orm_execute", reject_original_session_query)
