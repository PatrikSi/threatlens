"""Credential admission serializes concurrent transactions without lock upgrades."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.api.routes.webhook_automation import create_credential_profile
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.webhook_credential import WebhookCredentialProfile
from app.schemas.webhook_automation import WebhookCredentialWrite
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import data_access_context_for_authorization


def test_concurrent_profile_creation_admits_only_remaining_slot(database_engine):
    owner_id, token_id = uuid.uuid4(), uuid.uuid4()
    with Session(database_engine) as db:
        db.add(
            User(
                id=owner_id,
                email=f"profile-race-{owner_id}@example.com",
                password_hash="test-only",
                role="admin",
                is_active=True,
                is_approved=True,
            )
        )
        db.flush()
        db.add(
            ApiToken(
                id=token_id,
                user_id=owner_id,
                name="Concurrency test",
                token_prefix=uuid.uuid4().hex,
                token_hash=uuid.uuid4().hex * 2,
                scopes=["write:notifications"],
            )
        )
        db.add_all(
            [
                WebhookCredentialProfile(
                    user_id=owner_id,
                    name=f"Existing {index}",
                    enabled=True,
                    auth_type="none",
                )
                for index in range(99)
            ]
        )
        db.commit()
    start = Barrier(2)

    def create(name):
        with Session(database_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            owner = db.get(User, owner_id)
            authorization = authorization_context_for_user(
                db, owner, credential_scopes=frozenset({"write:notifications"})
            )
            access = data_access_context_for_authorization(db, authorization)
            request = Request({"type": "http"})
            request.state.auth_credential_kind = "api_token"
            request.state.api_token_id = token_id
            start.wait(timeout=5)
            try:
                create_credential_profile(
                    WebhookCredentialWrite(name=name),
                    request,
                    db=db,
                    user=owner,
                    authorization=authorization,
                    data_access=access,
                )
                return 201
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [executor.submit(create, name) for name in ("First", "Second")]
            assert sorted(result.result(timeout=15) for result in results) == [201, 409]
        with Session(database_engine) as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(WebhookCredentialProfile)
                    .where(WebhookCredentialProfile.user_id == owner_id)
                )
                == 100
            )
    finally:
        with Session(database_engine) as db:
            db.execute(delete(AuditLog).where(AuditLog.actor_user_id == owner_id))
            db.execute(delete(User).where(User.id == owner_id))
            db.commit()
