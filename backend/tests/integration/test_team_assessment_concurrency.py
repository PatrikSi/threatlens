from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import uuid

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.feed import Feed
from app.models.iam import IAMGroup, IAMGroupMembership
from app.models.item import Item
from app.models.team import Team
from app.models.user import User
from app.services import team_assessment_access
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import data_access_context_for_authorization
from app.services.export_job_contracts import ExportAuthorizationSnapshot


def test_same_actor_assessment_reads_do_not_upgrade_shared_authorization_locks(
    database_engine, monkeypatch
):
    """Both requests hold actor SHARE before either enters the team boundary."""
    user_id, group_id, team_id, feed_id, item_id, credential_id = [
        uuid.uuid4() for _ in range(6)
    ]
    _, prefix, digest = generate_api_token()
    scopes = ["read:items", "read:teams", "write:teams"]
    with Session(database_engine) as db:
        db.add(
            User(
                id=user_id,
                email=f"assessment-concurrency-{user_id}@example.test",
                password_hash="x",
                role="analyst",
                is_active=True,
                is_approved=True,
            )
        )
        db.add(
            IAMGroup(
                id=group_id, key=f"assessment-{group_id.hex}", name="Assessment readers"
            )
        )
        db.add(
            Feed(
                id=feed_id,
                name="Concurrent assessment",
                url=f"https://example.test/{feed_id}",
            )
        )
        db.flush()
        db.add_all(
            [
                Team(
                    id=team_id,
                    key=f"assessment-{team_id.hex}",
                    name="Assessment readers",
                    membership_group_id=group_id,
                ),
                IAMGroupMembership(group_id=group_id, user_id=user_id),
                Item(
                    id=item_id,
                    feed_id=feed_id,
                    url=f"https://example.test/{item_id}",
                    title="Source",
                    dedupe_key=f"assessment:{item_id}",
                    content_hash="a" * 64,
                ),
                ApiToken(
                    id=credential_id,
                    user_id=user_id,
                    name="Concurrent readers",
                    token_prefix=prefix,
                    token_hash=digest,
                    scopes=scopes,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ),
            ]
        )
        db.commit()
    barrier = Barrier(2)
    original_fence = team_assessment_access.fence_assessment_request

    def simultaneous_fence(db, actor, **kwargs):
        original_fence(db, actor, **kwargs)
        barrier.wait(timeout=5)

    monkeypatch.setattr(
        team_assessment_access, "fence_assessment_request", simultaneous_fence
    )

    def read():
        with Session(database_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            user = db.get(User, user_id)
            authorization = authorization_context_for_user(
                db, user, credential_scopes=scopes
            )
            access = data_access_context_for_authorization(db, authorization)
            snapshot = ExportAuthorizationSnapshot(
                credential_kind="api_token",
                credential_id=credential_id,
                permissions=authorization.permissions,
                enforced=access.enforced,
                allowed_label_ids=access.allowed_label_ids,
            )
            actor = team_assessment_access.AssessmentRequest(
                user, authorization, access, snapshot
            )
            state = team_assessment_access.load_assessment_state(
                db, actor=actor, team_id=team_id, item_id=item_id, write=False
            )
            assert state.item.id == item_id
            db.commit()
            return True

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(read) for _ in range(2)]
            assert all(future.result(timeout=10) for future in futures)
    finally:
        with Session(database_engine) as db:
            db.execute(delete(Team).where(Team.id == team_id))
            db.execute(delete(IAMGroup).where(IAMGroup.id == group_id))
            db.execute(delete(Feed).where(Feed.id == feed_id))
            db.execute(delete(User).where(User.id == user_id))
            db.commit()
