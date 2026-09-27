"""Consent grants retain their original handling ceiling under later policy changes."""

import hashlib
import pytest
from sqlalchemy import delete, select
from app.api.mcp_context import resolve_mcp_read_context
from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.api_token import ApiToken
from app.models.data_policy import DataPolicyRoleGrant, DataPolicyState
from app.services.mcp_read_contracts import MCPReadError
from app.services.mcp_read_service import call_read_tool
from tests.integration.test_mcp_oauth import (
    oauth_env as oauth_fixture,
    authorize,
    mcp_request,
)
from tests.integration.test_mcp_read_service import _item
from tests.integration.test_data_policy_read_coverage import _enable_enforcement


oauth_env = oauth_fixture


def _grant(db, label, actor):
    db.add(
        DataPolicyRoleGrant(
            label_id=label.id,
            role_id=SYSTEM_ROLE_IDS["analyst"],
            granted_by_user_id=actor.id,
        )
    )
    db.get(DataPolicyState, 1).revision += 1
    db.commit()


def _assert_hidden(db, token, item, label):
    context = resolve_mcp_read_context(mcp_request(token), db, cursor_secret=b"a" * 32)
    assert label.id not in context.data_access.allowed_label_ids
    with pytest.raises(MCPReadError) as error:
        call_read_tool(
            db,
            context=context,
            tool_name="get_article_evidence",
            arguments={"item_id": str(item.id)},
            canonical_base_url="https://threatlens.example",
            max_response_bytes=65536,
        )
    assert error.value.code == "not_found"
    db.rollback()


def test_new_handling_grant_does_not_widen_existing_oauth_consent(
    client, oauth_env, db_session, seed_users, monkeypatch
):  # noqa: F811
    label = _enable_enforcement(db_session, seed_users, monkeypatch)
    item = _item(db_session, label=label.id)
    db_session.commit()
    exchange = authorize(client, oauth_env)
    _grant(db_session, label, seed_users["admin"])
    response = client.post("/mcp/oauth/token", data=exchange)
    assert response.status_code == 200, response.text
    _assert_hidden(db_session, response.json()["access_token"], item, label)


def test_current_handling_revocation_overrides_original_oauth_cap(
    client, oauth_env, db_session, seed_users, monkeypatch
):  # noqa: F811
    label = _enable_enforcement(db_session, seed_users, monkeypatch)
    _grant(db_session, label, seed_users["admin"])
    item = _item(db_session, label=label.id)
    db_session.commit()
    response = client.post("/mcp/oauth/token", data=authorize(client, oauth_env))
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    context = resolve_mcp_read_context(
        mcp_request(token), db_session, cursor_secret=b"a" * 32
    )
    assert label.id in context.data_access.allowed_label_ids
    db_session.rollback()
    db_session.execute(
        delete(DataPolicyRoleGrant).where(
            DataPolicyRoleGrant.label_id == label.id,
            DataPolicyRoleGrant.role_id == SYSTEM_ROLE_IDS["analyst"],
        )
    )
    db_session.get(DataPolicyState, 1).revision += 1
    db_session.commit()
    _assert_hidden(db_session, token, item, label)
    # The token is still present; denial is current handling policy, not expiry.
    assert (
        db_session.scalar(
            select(ApiToken.revoked_at).where(
                ApiToken.token_hash == hashlib.sha256(token.encode()).hexdigest()
            )
        )
        is None
    )
