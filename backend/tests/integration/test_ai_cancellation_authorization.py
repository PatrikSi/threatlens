"""Real HTTP authorization across independently committed cancellation batches."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.api.routes import ai as ai_routes
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.core.security import generate_api_token
from app.db.session import get_db
from app.main import app
from app.models.ai_task_run import AITaskRun
from app.models.api_token import ApiToken
from app.models.iam import IAMPolicyState
from app.models.user import User
from app.services import ai_ops
from app.services.ai_reprocess import ensure_reprocess_child
from app.services.ai_task_cancellation import reconcile_canceled_ai_tasks
from app.services.authorization import bump_iam_policy_revision
from tests.unit.ai_workflow_test_support import cleanup_ai_workflow_probe as cleanup_ai_workflow_probe
from tests.unit.test_ai_workflow_durability import item, parent


def test_http_cancellation_stops_after_committed_token_revocation(database_engine, client, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    with Session(database_engine) as db:
        initial_revision = db.get(IAMPolicyState, 1).revision
        user = User(
            email=f"cancel-{uuid.uuid4().hex}@example.test", password_hash="unused",
            password_login_enabled=False, role=ROLE_ADMIN, is_active=True,
        )
        db.add(user)
        db.flush()
        token_value, token_prefix, token_hash = generate_api_token()
        token = ApiToken(user_id=user.id, name="Cancellation regression", token_prefix=token_prefix,
                         token_hash=token_hash, scopes=["*:*"], last_used_at=datetime.now(timezone.utc))
        db.add(token)
        db.commit()
        user_id, token_id = user.id, token.id
        rows = [item(db, "cancel-auth-" + uuid.uuid4().hex) for _ in range(2)]
        run = parent(db, rows)
        run_id = run.id
        child_ids = [ensure_reprocess_child(db, parent_id=run_id, item_id=row.id, model=None).id for row in rows]
        for child_id in child_ids:
            db.get(AITaskRun, child_id).celery_task_id = str(uuid.uuid4())
        db.commit()

    def request_session():
        with Session(database_engine) as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    monkeypatch.setattr(ai_ops, "_load_live_task_snapshot", lambda: (False, [], [], [], []))
    revocations = []
    monkeypatch.setattr(ai_ops.celery_app.control, "revoke", lambda delivery, **kwargs: revocations.append(delivery))
    original_refence = ai_routes.refence_ai_context
    revoked = False

    def revoke_between_committed_children(db, **kwargs):
        nonlocal revoked
        if not revoked:
            with Session(database_engine) as change:
                settled = list(change.scalars(select(AITaskRun.id).where(
                    AITaskRun.id.in_(child_ids), AITaskRun.status == "skipped"
                )))
                if settled:
                    assert len(settled) == 1
                    assert change.get(AITaskRun, run_id).metadata_json["cancel_requested_at"]
                    change.execute(text("SET LOCAL lock_timeout = '3s'"))
                    bump_iam_policy_revision(change)
                    change.get(ApiToken, token_id).revoked_at = datetime.now(timezone.utc)
                    change.commit()
                    revoked = True
        return original_refence(db, **kwargs)

    monkeypatch.setattr(ai_routes, "refence_ai_context", revoke_between_committed_children)
    headers = {"Authorization": f"Bearer {token_value}"}
    try:
        response = client.post(f"/ai/ops/runs/{run_id}/cancel", headers=headers)
        assert response.status_code == 409
        assert "authorization changed" in response.json()["detail"]
        assert revoked and len(revocations) == 1
        with Session(database_engine) as db:
            children = [db.get(AITaskRun, child_id) for child_id in child_ids]
            assert sorted(child.status for child in children) == ["queued", "skipped"]
            canceled = next(child for child in children if child.status == "skipped")
            assert revocations == [canceled.celery_task_id]
            assert db.get(ApiToken, token_id).revoked_at is not None
            assert db.get(IAMPolicyState, 1).revision == initial_revision + 1
            intent = db.get(AITaskRun, run_id).metadata_json["cancel_requested_at"]

        # The revoked credential cannot resume HTTP side effects; maintenance
        # independently completes the cancellation accepted before revocation.
        assert client.post(f"/ai/ops/runs/{run_id}/cancel", headers=headers).status_code == 401
        with Session(database_engine) as db:
            assert reconcile_canceled_ai_tasks(db) >= 1
            db.commit()
            assert all(db.get(AITaskRun, child_id).status == "skipped" for child_id in child_ids)
            run = db.get(AITaskRun, run_id)
            assert run.status == "skipped" and run.finished_at is not None
            assert run.metadata_json["cancel_requested_at"] == intent
        assert len(revocations) == 1
    finally:
        with Session(database_engine) as db:
            db.execute(delete(User).where(User.id == user_id))
            db.get(IAMPolicyState, 1).revision = initial_revision
            db.commit()
