from datetime import datetime, timedelta, timezone
import itertools
import json
import logging
import uuid

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.api_token import ApiToken
from app.models.article import Article
from app.models.audit_log import AuditLog
from app.services.mcp_protocol import (
    CLIENT_CAPABILITIES_META, LATEST_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION,
    PROTOCOL_VERSION_META,
)


_REQUEST_IDS = itertools.count(1)
_DEFAULT_TOKEN = object()


def _request(env, method="tools/list", *, name=None, arguments=None, token=_DEFAULT_TOKEN,
             version=LATEST_PROTOCOL_VERSION, params=None, headers=None):
    request_id = next(_REQUEST_IDS)
    parameters = dict(params or {})
    http_headers = {
        "MCP-Protocol-Version": version,
        "X-Request-ID": f"{env.request_prefix}-{request_id}",
        "Accept": "application/json",
    }
    if version == LATEST_PROTOCOL_VERSION:
        parameters["_meta"] = {PROTOCOL_VERSION_META: version, CLIENT_CAPABILITIES_META: {}}
        http_headers["MCP-Method"] = method
    if name is not None:
        parameters["name"] = name
        http_headers["MCP-Name"] = name
    if arguments is not None:
        parameters["arguments"] = arguments
    if token is _DEFAULT_TOKEN:
        token = env.token
    if token is not None:
        http_headers["Authorization"] = f"Bearer {token}"
    http_headers.update(headers or {})
    return env.client.post("/v1/mcp", json={
        "jsonrpc": "2.0", "id": request_id, "method": method, "params": parameters,
    }, headers=http_headers)


def test_mcp_is_disabled_by_default_at_the_real_endpoint(mcp_http_environment, monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "false")
    get_settings.cache_clear()
    response = _request(mcp_http_environment)
    assert response.status_code == 404
    assert response.json()["error"]["data"]["code"] == "mcp_disabled"


def test_mcp_transport_owns_correlation_and_final_http_logging(mcp_http_environment, caplog, monkeypatch):
    # Alembic's test-database setup disables pre-existing loggers via fileConfig.
    # Restore the application loggers for this ordinary-request observation.
    monkeypatch.setattr(logging.getLogger("threatlens.mcp"), "disabled", False)
    monkeypatch.setattr(logging.getLogger("threatlens.api"), "disabled", False)
    caplog.set_level(logging.INFO, logger="threatlens.mcp")
    caplog.set_level(logging.INFO, logger="threatlens.api")
    env = mcp_http_environment
    response = _request(env)
    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    assert request_id.startswith(env.request_prefix)
    records = [record for record in caplog.records if getattr(record, "request_id", None) == request_id]
    completion = [record for record in records if record.getMessage().startswith("mcp_request_complete")]
    assert len(completion) == 1
    assert "transfer_complete=True" in completion[0].getMessage()
    assert "cleanup_outcome=completed" in completion[0].getMessage()
    assert not any(record.getMessage() == "request_complete" for record in records)


@pytest.mark.parametrize("credential_kind", ["missing", "cookie", "session_bearer", "broad"])
def test_mcp_http_requires_an_explicit_scoped_bearer(mcp_http_environment, credential_kind):
    env = mcp_http_environment
    token = None
    headers = {}
    if credential_kind == "cookie":
        headers["Cookie"] = "threatlens_session=tls_synthetic_browser_session"
    elif credential_kind == "session_bearer":
        token = "tls_synthetic_browser_session"
    elif credential_kind == "broad":
        token = env.issue_token(["*:*"]).value
    response = _request(env, token=token, headers=headers)
    assert response.status_code == (403 if credential_kind == "broad" else 401)
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert "tools" not in response.json().get("result", {})


def test_discovery_and_catalogue_respect_the_current_credential_scope(mcp_http_environment):
    env = mcp_http_environment
    discovery = _request(env, "server/discover")
    assert discovery.status_code == 200, discovery.text
    assert discovery.json()["result"]["supportedVersions"] == [LATEST_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION]
    response = _request(env)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert {tool["name"] for tool in result["tools"]} == {"search_articles", "get_article_evidence"}
    assert all(tool["annotations"]["readOnlyHint"] for tool in result["tools"])
    assert result["cacheScope"] == "private"
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["mcp-protocol-version"] == LATEST_PROTOCOL_VERSION


def test_real_http_search_and_evidence_return_only_stored_records(mcp_http_environment):
    env = mcp_http_environment
    search = _request(env, "tools/call", name="search_articles", arguments={"feed_id": str(env.feed_id), "limit": 5})
    assert search.status_code == 200, search.text
    assert search.json()["result"]["isError"] is False
    records = search.json()["result"]["structuredContent"]["data"]["articles"]
    assert [record["item_id"] for record in records] == [str(env.item_id)]
    evidence = _request(env, "tools/call", name="get_article_evidence", arguments={"item_id": str(env.item_id)})
    assert evidence.status_code == 200, evidence.text
    result = evidence.json()["result"]
    assert result["isError"] is False
    assert "Synthetic stored article evidence" in result["structuredContent"]["data"]["article_text"]
    assert result["structuredContent"]["provenance"]["content_trust"] == "untrusted_stored_content"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_invalid_arguments_are_tool_errors_without_input_echo(mcp_http_environment):
    sentinel = "SENSITIVE_INPUT_MUST_NOT_BE_ECHOED"
    response = _request(mcp_http_environment, "tools/call", name="search_articles", arguments={"limit": sentinel})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["isError"] is True
    assert "invalid_arguments" in response.json()["result"]["content"][0]["text"]
    assert "structuredContent" not in response.json()["result"]
    assert sentinel not in response.text


@pytest.mark.parametrize("change", ["revoked", "expired", "scope_removed"])
def test_mcp_reauthorizes_each_http_request(mcp_http_environment, change):
    env = mcp_http_environment
    assert _request(env).status_code == 200
    values = {
        "revoked": {"revoked_at": datetime.now(timezone.utc)},
        "expired": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
        "scope_removed": {"scopes": ["read:items"]},
    }[change]
    with Session(env.engine) as db:
        db.execute(update(ApiToken).where(ApiToken.id == env.credential_id).values(**values))
        db.commit()
    response = _request(env)
    assert response.status_code == (403 if change == "scope_removed" else 401)
    assert "result" not in response.json()


def test_unknown_method_and_mismatched_headers_are_protocol_errors(mcp_http_environment):
    env = mcp_http_environment
    unknown = _request(env, "tools/execute")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == -32601
    mismatch = _request(env, headers={"MCP-Method": "tools/call"})
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == -32020
    version = _request(env, headers={"MCP-Protocol-Version": "1900-01-01"})
    assert version.status_code == 400


def test_http_boundary_rejects_oversize_and_unsupported_requests(mcp_http_environment):
    env = mcp_http_environment
    oversized = env.client.post("/v1/mcp", content=b"x" * 17000, headers={"Content-Type": "application/json"})
    assert oversized.status_code == 413
    duplicate = env.client.post("/v1/mcp", content=b"{}", headers=[
        ("Content-Type", "application/json"), ("Authorization", f"Bearer {env.token}"),
        ("Authorization", "Bearer tlp_other_credential"),
    ])
    assert duplicate.status_code == 400
    query = env.client.post("/v1/mcp?access_token=never-accept-uri-tokens", json={})
    assert query.status_code == 400
    assert env.client.get("/v1/mcp").status_code == 405
    assert env.client.post("/mcp", json={}).status_code == 404


def test_durable_audit_is_redacted_and_identifies_the_authenticated_actor(mcp_http_environment):
    env = mcp_http_environment
    search_sentinel = "PRIVATE_SEARCH_CONTENT_NOT_FOR_AUDIT"
    guessed_id = str(uuid.uuid4())
    assert _request(env, "tools/call", name="search_articles", arguments={"q": search_sentinel}).status_code == 200
    denied = _request(env, "tools/call", name="get_investigation", arguments={"investigation_id": guessed_id})
    assert denied.json()["result"]["isError"] is True
    with Session(env.engine) as db:
        audits = db.scalars(select(AuditLog).where(
            AuditLog.request_id.startswith(env.request_prefix), AuditLog.action == "mcp.read",
        )).all()
        assert len(audits) == 2
        assert {entry.metadata_json["outcome"] for entry in audits} == {"prepared", "denied"}
        assert all(entry.actor_principal_id == env.user_id for entry in audits)
        assert all(entry.credential_id == env.credential_id for entry in audits)
        assert all(entry.resource_id is None for entry in audits)
        assert all(entry.data_access_governed is False for entry in audits)
        serialized = json.dumps([entry.metadata_json for entry in audits])
        assert search_sentinel not in serialized
        assert guessed_id not in serialized
        assert env.token not in serialized


def test_unknown_tool_name_is_not_persisted_as_audit_content(mcp_http_environment):
    env = mcp_http_environment
    private_name = "tlp_synthetic_private_identifier_not_a_tool"
    response = _request(env, "tools/call", name=private_name, arguments={})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["isError"] is True
    assert private_name not in response.text
    with Session(env.engine) as db:
        audit = db.scalar(select(AuditLog).where(
            AuditLog.request_id.startswith(env.request_prefix), AuditLog.action == "mcp.read",
        ))
        assert audit is not None
        assert audit.metadata_json == {"operation": "tools/call", "outcome": "denied"}
        assert private_name not in json.dumps(audit.metadata_json)


def test_supported_service_account_tokens_expose_only_article_tools(mcp_http_environment):
    env = mcp_http_environment
    credential = env.issue_service_token()
    response = _request(env, token=credential.value)
    assert response.status_code == 200, response.text
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == {
        "search_articles", "get_article_evidence",
    }
    evidence = _request(env, "tools/call", name="get_article_evidence", token=credential.value, arguments={"item_id": str(env.item_id)})
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["result"]["isError"] is False
    denied = _request(env, "tools/call", name="get_report", token=credential.value, arguments={"report_id": str(uuid.uuid4())})
    assert denied.status_code == 200, denied.text
    assert denied.json()["result"]["isError"] is True
    with Session(env.engine) as db:
        audits = db.scalars(select(AuditLog).where(
            AuditLog.request_id.startswith(env.request_prefix), AuditLog.action == "mcp.read",
        )).all()
        assert len(audits) == 3
        assert all(audit.actor_user_id is None for audit in audits)
        assert all(audit.actor_principal_type == "service_account" for audit in audits)
        assert all(audit.actor_principal_id == credential.principal_id for audit in audits)


def test_actual_response_obeys_the_smallest_configured_wire_budget(mcp_http_environment, monkeypatch):
    env = mcp_http_environment
    monkeypatch.setenv("MCP_RESPONSE_MAX_BYTES", "16384")
    get_settings.cache_clear()
    with Session(env.engine) as db:
        db.execute(update(Article).where(Article.item_id == env.item_id).values(text="quoted \" evidence \\ " * 2000))
        db.commit()
    response = _request(env, "tools/call", name="get_article_evidence", arguments={"item_id": str(env.item_id), "text_limit": 16000})
    assert response.status_code == 200, response.text
    assert len(response.content) <= 16384
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["truncation"]["truncated"] is True


def test_principal_rate_limit_is_shared_across_requests(mcp_http_environment, monkeypatch):
    env = mcp_http_environment
    monkeypatch.setenv("MCP_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()
    assert _request(env).status_code == 200
    response = _request(env)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1


def test_read_and_audit_connections_are_pinned_before_authentication_and_survive_auth_commit(
    mcp_http_environment,
):
    env = mcp_http_environment
    stale_use_time = datetime.now(timezone.utc) - timedelta(days=1)
    with Session(env.engine) as db:
        db.execute(update(ApiToken).where(ApiToken.id == env.credential_id).values(last_used_at=stale_use_time))
        db.commit()
    events = []

    def checked_out(_connection, record, _proxy):
        events.append(("checkout", id(record)))

    def checked_in(_connection, record):
        events.append(("checkin", id(record)))

    def executing(_connection, _cursor, statement, _parameters, _context, _many):
        events.append(("sql", " ".join(statement.lower().split())))

    def committed(_connection):
        events.append(("commit", None))

    listeners = [
        (env.engine.pool, "checkout", checked_out),
        (env.engine.pool, "checkin", checked_in),
        (env.engine, "before_cursor_execute", executing),
        (env.engine, "commit", committed),
    ]
    for target, name, callback in listeners:
        event.listen(target, name, callback)
    try:
        response = _request(env, "tools/call", name="search_articles", arguments={"feed_id": str(env.feed_id)})
    finally:
        for target, name, callback in reversed(listeners):
            event.remove(target, name, callback)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["isError"] is False
    assert [kind for kind, _value in events[:2]] == ["checkout", "checkout"]
    assert len([entry for entry in events if entry[0] == "checkout"]) == 2
    assert len([entry for entry in events if entry[0] == "checkin"]) == 2
    assert len([entry for entry in events if entry[0] == "commit"]) == 2
    assert any(kind == "sql" and statement.startswith("update api_tokens set last_used_at") for kind, statement in events)
    assert any(kind == "sql" and statement.startswith("insert into audit_logs") for kind, statement in events)


def test_audit_persistence_failure_withholds_read_data_and_releases_both_connections(
    mcp_http_environment, caplog,
):
    env = mcp_http_environment
    private_failure = "PRIVATE_AUDIT_DATABASE_FAILURE_DETAILS"
    checkouts, checkins, attempted_audits = [], [], []

    def checked_out(_connection, record, _proxy):
        checkouts.append(id(record))

    def checked_in(_connection, record):
        checkins.append(id(record))

    def reject_audit_insert(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lower().startswith("insert into audit_logs"):
            attempted_audits.append(statement)
            raise SQLAlchemyError(private_failure)

    listeners = [
        (env.engine.pool, "checkout", checked_out),
        (env.engine.pool, "checkin", checked_in),
        (env.engine, "before_cursor_execute", reject_audit_insert),
    ]
    for target, name, callback in listeners:
        event.listen(target, name, callback)
    try:
        response = _request(env, "tools/call", name="get_article_evidence", arguments={"item_id": str(env.item_id)})
    finally:
        for target, name, callback in reversed(listeners):
            event.remove(target, name, callback)

    assert response.status_code == 503, response.text
    assert response.json()["error"]["data"]["code"] == "mcp_unavailable"
    assert "result" not in response.json()
    assert response.headers["retry-after"] == "5"
    assert "no-store" in response.headers["cache-control"]
    assert "Synthetic stored article evidence" not in response.text
    assert str(env.item_id) not in response.text
    assert private_failure not in response.text
    assert private_failure not in caplog.text
    assert len(attempted_audits) == 2  # Prepared and fallback failure audit both failed.
    assert len(checkouts) == 2
    assert sorted(checkins) == sorted(checkouts)
    with Session(env.engine) as db:
        assert db.scalar(select(AuditLog.id).where(
            AuditLog.request_id.startswith(env.request_prefix), AuditLog.action == "mcp.read",
        )) is None
