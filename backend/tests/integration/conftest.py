"""Committed fixtures for MCP's independently owned request/audit sessions."""

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest
import redis
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import SERVICE_ACCOUNT_PERMISSION_IDS
from app.core.security import generate_api_token
from app.main import app
from app.models.api_token import ApiToken
from app.models.article import Article
from app.models.audit_log import AuditLog
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.item import Item
from app.models.iam import IAMRole, IAMRolePermission
from app.models.service_account import (
    ServiceAccount, ServiceAccountCredential, ServiceAccountRoleAssignment,
)
from app.models.user import User
from app.services.service_accounts import _generate_service_account_token


@pytest.fixture
def mcp_http_environment(database_engine, test_redis_url, monkeypatch):
    if test_redis_url is None:
        pytest.fail("MCP HTTP tests require the disposable Redis test fixture")
    suffix = uuid.uuid4().hex
    request_prefix = f"mcp-http-{suffix}"
    source_ip = f"192.0.2.{int(suffix[:2], 16) % 254 + 1}"
    monkeypatch.setenv("MCP_ENABLED", "true")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://client.example")
    monkeypatch.setenv("MCP_RATE_LIMIT_PER_MINUTE", "1000")
    monkeypatch.setenv("REDIS_URL", test_redis_url)
    monkeypatch.setenv("PUBLIC_APP_URL", "https://threatlens.example")
    get_settings.cache_clear()
    with Session(database_engine) as db:
        user = User(
            email=f"mcp-reader-{suffix}@example.test", password_hash="!",
            role="viewer", is_active=True, is_approved=True,
        )
        feed = Feed(
            name=f"MCP fixture {suffix}", url=f"https://feed.example.test/{suffix}.xml",
            enabled=False, handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        )
        db.add_all([user, feed])
        db.flush()
        item = Item(
            feed_id=feed.id, url=f"https://article.example.test/{suffix}",
            title=f"MCP stored evidence {suffix}", summary="Synthetic defensive evidence summary",
            dedupe_key=f"mcp-http-{suffix}", content_hash="a" * 64,
        )
        db.add(item)
        db.flush()
        db.add(Article(
            item_id=item.id, final_url=item.url, http_status=200,
            text="Synthetic stored article evidence. No external network retrieval is needed.",
            extraction_method="test_fixture", content_type="text/plain",
        ))
        db.commit()
        user_id, feed_id, item_id = user.id, feed.id, item.id

    def issue_token(scopes, *, expires_at=None):
        value, prefix, token_hash = generate_api_token()
        with Session(database_engine) as db:
            token = ApiToken(
                user_id=user_id, name=f"MCP HTTP test {suffix}",
                token_prefix=prefix, token_hash=token_hash, scopes=scopes,
                expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=1),
                last_used_at=datetime.now(timezone.utc),
            )
            db.add(token)
            db.commit()
            return SimpleNamespace(value=value, id=token.id)

    service_account_ids = []
    service_role_ids = []

    def issue_service_token():
        machine_suffix = uuid.uuid4().hex[:12]
        with Session(database_engine) as db:
            account = ServiceAccount(key=f"mcp-http-{machine_suffix}", name="MCP HTTP machine reader")
            role = IAMRole(key=f"mcp-http-role-{machine_suffix}", name="MCP HTTP machine role", is_system=False)
            db.add_all([account, role])
            db.flush()
            db.add(ServiceAccountRoleAssignment(service_account_id=account.id, role_id=role.id))
            scopes = sorted(permission for permission in SERVICE_ACCOUNT_PERMISSION_IDS if permission.startswith("read:"))
            db.add_all(IAMRolePermission(role_id=role.id, permission=permission) for permission in scopes)
            value, prefix, token_hash = _generate_service_account_token()
            token = ServiceAccountCredential(
                service_account_id=account.id, name="MCP HTTP machine credential",
                token_prefix=prefix, token_hash=token_hash, scopes=scopes,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                last_used_at=datetime.now(timezone.utc),
            )
            db.add(token)
            db.commit()
            service_account_ids.append(account.id)
            service_role_ids.append(role.id)
            return SimpleNamespace(value=value, id=token.id, principal_id=account.id)

    credential = issue_token(["read:mcp", "read:items"])
    redis_client = redis.Redis.from_url(test_redis_url, decode_responses=True)
    buckets = (f"source:{source_ip}", f"principal:user:{user_id}")
    redis_keys = ["threatlens:mcp:rate:" + hashlib.sha256(bucket.encode()).hexdigest() for bucket in buckets]
    redis_client.delete(*redis_keys)
    try:
        with TestClient(app, client=(source_ip, 50000)) as client:
            yield SimpleNamespace(
                app=app, engine=database_engine, client=client,
                token=credential.value, credential_id=credential.id,
                user_id=user_id, feed_id=feed_id, item_id=item_id,
                source_ip=source_ip, request_prefix=request_prefix,
                issue_token=issue_token, redis=redis_client, redis_keys=redis_keys,
                issue_service_token=issue_service_token,
            )
    finally:
        with Session(database_engine) as db:
            db.execute(delete(AuditLog).where(AuditLog.request_id.startswith(request_prefix)))
            db.execute(delete(AuditLog).where(AuditLog.actor_principal_id == user_id))
            if service_account_ids:
                db.execute(delete(AuditLog).where(AuditLog.actor_principal_id.in_(service_account_ids)))
                db.execute(delete(ServiceAccount).where(ServiceAccount.id.in_(service_account_ids)))
                db.execute(delete(IAMRole).where(IAMRole.id.in_(service_role_ids)))
            db.execute(delete(Feed).where(Feed.id == feed_id))
            db.execute(delete(User).where(User.id == user_id))
            db.commit()
        redis_client.delete(*redis_keys)
        if service_account_ids:
            redis_client.delete(*[
                "threatlens:mcp:rate:" + hashlib.sha256(f"principal:service_account:{identifier}".encode()).hexdigest()
                for identifier in service_account_ids
            ])
        redis_client.close()
        get_settings.cache_clear()
