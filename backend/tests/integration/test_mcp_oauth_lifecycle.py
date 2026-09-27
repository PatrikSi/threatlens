"""OAuth mutations recheck authority and terminal credentials do not grow forever."""

from datetime import datetime, timedelta, timezone
import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from app.api.routes import mcp_oauth as routes
from app.models.api_token import ApiToken
from app.models.mcp_oauth import MCPDelegation, MCPOAuthClient, MCPOAuthCode
from app.services import mcp_oauth_lifecycle as lifecycle
from tests.integration.test_mcp_oauth import oauth_env as oauth_fixture, authorize

oauth_env = oauth_fixture


@pytest.mark.parametrize("operation", ["register", "revoke"])
@pytest.mark.parametrize(
    "mutation", ["revoke_credential", "remove_scope", "disable_account", "remove_admin"]
)
def test_client_admin_mutations_recheck_credential_and_user_after_policy_lock(
    client, oauth_env, auth_headers, seed_users, monkeypatch, operation, mutation
):
    original = routes.lock_iam_policy_for_mutation
    digest = hashlib.sha256(
        auth_headers["admin"]["Authorization"].split()[1].encode()
    ).hexdigest()

    def change_after_dependency_auth(db):
        original(db)
        user = db.get(type(seed_users["admin"]), seed_users["admin"].id)
        token = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
        if mutation == "revoke_credential":
            token.revoked_at = datetime.now(timezone.utc)
        elif mutation == "remove_scope":
            token.scopes = ["read:tokens"]
        elif mutation == "disable_account":
            user.is_active = False
        else:
            user.role = "analyst"
        db.flush()

    monkeypatch.setattr(
        routes, "lock_iam_policy_for_mutation", change_after_dependency_auth
    )
    if operation == "register":
        response = client.post(
            "/mcp/oauth/clients",
            headers=auth_headers["admin"],
            json={
                "name": "Late client",
                "redirect_uris": ["https://client.example/callback"],
            },
        )
    else:
        response = client.delete(
            f"/mcp/oauth/clients/{oauth_env[0]['client_id']}",
            headers=auth_headers["admin"],
        )
    assert response.status_code in {401, 403}, response.text
    assert "admin_" in response.text


def _delegation(db, client_id, user_id, *, expires_at, revoked_at=None):
    token = ApiToken(
        user_id=user_id,
        name="Lifecycle grant",
        token_prefix="tlmcp_" + uuid.uuid4().hex[:16],
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        scopes=["read:mcp", "read:items"],
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
    db.add(token)
    db.flush()
    db.add(
        MCPDelegation(
            token_id=token.id,
            client_id=client_id,
            resource="https://threatlens.example/api/v1/mcp",
            label_cap_json={"enforced": False},
        )
    )
    db.flush()
    return token


def test_lifecycle_cleanup_is_bounded_and_keeps_usable_or_recent_authority(
    client, oauth_env, db_session, seed_users
):
    now = datetime.now(timezone.utc)
    identifier = uuid.UUID(oauth_env[0]["client_id"])
    owner = seed_users["analyst"].id
    old = [
        _delegation(
            db_session, identifier, owner, expires_at=now - timedelta(days=2)
        ).id
        for _ in range(102)
    ]
    active = _delegation(
        db_session, identifier, owner, expires_at=now + timedelta(minutes=15)
    ).id
    recent = _delegation(
        db_session, identifier, owner, expires_at=now - timedelta(minutes=15)
    ).id
    ordinary = ApiToken(
        user_id=owner,
        name="Ordinary API history",
        token_prefix="tlp_" + uuid.uuid4().hex[:16],
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        scopes=["read:items"],
        expires_at=now - timedelta(days=2),
    )
    db_session.add(ordinary)
    for index in range(105):
        db_session.add(
            MCPOAuthCode(
                code_hash=f"{index:064x}",
                client_id=identifier,
                user_id=owner,
                auth_token_version=0,
                redirect_uri="https://client.example/callback",
                resource="https://threatlens.example/api/v1/mcp",
                challenge="a" * 43,
                scopes=["read:mcp", "read:items"],
                label_cap_json={},
                expires_at=now - timedelta(minutes=1),
            )
        )
    for _ in range(25):
        db_session.add(
            MCPOAuthClient(
                name="Retired empty client",
                redirect_uris=["https://client.example/callback"],
                revoked_at=now,
            )
        )
    db_session.flush()
    counts = lifecycle.maintain_authorization(db_session, now=now)
    assert counts == {"tokens_pruned": 100, "codes_pruned": 100, "clients_pruned": 20}
    assert (
        db_session.scalar(
            select(func.count()).select_from(ApiToken).where(ApiToken.id.in_(old))
        )
        == 2
    )
    assert all(
        db_session.get(ApiToken, key) is not None
        for key in (active, recent, ordinary.id)
    )
    assert db_session.get(MCPOAuthClient, identifier) is not None
    assert lifecycle.maintain_authorization(db_session, now=now) == {
        "tokens_pruned": 2,
        "codes_pruned": 5,
        "clients_pruned": 5,
    }


def test_revoked_client_slot_is_reclaimed_only_after_its_grants_are_terminal(
    client, oauth_env, db_session, seed_users
):
    now = datetime.now(timezone.utc)
    row = db_session.get(MCPOAuthClient, uuid.UUID(oauth_env[0]["client_id"]))
    row.revoked_at = now
    token = _delegation(
        db_session,
        row.id,
        seed_users["analyst"].id,
        expires_at=now + timedelta(minutes=15),
        revoked_at=now,
    )
    assert lifecycle.maintain_authorization(db_session, now=now)["clients_pruned"] == 0
    token.revoked_at = now - timedelta(days=2)
    db_session.flush()
    assert lifecycle.maintain_authorization(db_session, now=now)["clients_pruned"] == 1
    assert db_session.get(MCPOAuthClient, row.id) is None


def test_unexchanged_codes_reserve_retained_grant_capacity(
    client, oauth_env, monkeypatch
):
    monkeypatch.setattr(lifecycle, "MAX_RETAINED_USER_GRANTS", 1)
    exchange = authorize(client, oauth_env)
    request, _, headers = oauth_env
    blocked = client.post(
        "/mcp/oauth/authorize",
        headers=headers,
        json={**request, "approve": True, "current_password": "AnalystPass123!"},
    )
    assert blocked.status_code == 429 and "delegation_capacity" in blocked.text
    assert client.post("/mcp/oauth/token", data=exchange).status_code == 200
    still_full = client.post(
        "/mcp/oauth/authorize",
        headers=headers,
        json={**request, "approve": True, "current_password": "AnalystPass123!"},
    )
    assert still_full.status_code == 429 and "delegation_capacity" in still_full.text


def test_terminal_grant_cleanup_uses_index_among_large_ordinary_token_inventory(
    client, oauth_env, db_session, seed_users
):
    import json
    from sqlalchemy import insert, text
    from sqlalchemy.dialects import postgresql

    now = datetime.now(timezone.utc)
    owner = seed_users["analyst"].id
    client_id = uuid.UUID(oauth_env[0]["client_id"])
    tokens, grants = [], []
    for index in range(5250):
        identifier = uuid.uuid4()
        delegated = index >= 5000
        terminal = index >= 5245
        tokens.append(
            {
                "id": identifier,
                "user_id": owner,
                "name": "Index fixture",
                "token_prefix": ("tlmcp_" if delegated else "tlp_")
                + uuid.uuid4().hex[:16],
                "token_hash": uuid.uuid4().hex + uuid.uuid4().hex,
                "scopes": ["read:mcp", "read:items"],
                "expires_at": now - timedelta(days=2)
                if terminal
                else now + timedelta(days=1),
            }
        )
        if delegated:
            grants.append(
                {
                    "token_id": identifier,
                    "client_id": client_id,
                    "resource": "https://threatlens.example/api/v1/mcp",
                    "label_cap_json": {},
                }
            )
    db_session.execute(insert(ApiToken), tokens)
    db_session.execute(insert(MCPDelegation), grants)
    db_session.execute(text("ANALYZE api_tokens"))
    db_session.execute(text("ANALYZE mcp_delegations"))
    statement = lifecycle.terminal_grant_candidates(now=now)
    compiled = statement.compile(
        dialect=postgresql.dialect(paramstyle="named"),
        compile_kwargs={"literal_binds": True},
    )
    plan = db_session.scalar(text("EXPLAIN (FORMAT JSON) " + str(compiled)))
    assert "ix_api_tokens_mcp_terminal" in json.dumps(plan), json.dumps(plan, indent=2)
    assert lifecycle.prune_terminal_grants(db_session, now=now) == 5
