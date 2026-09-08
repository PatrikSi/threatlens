"""Publication and authentication changes must share one row-lock order."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi import Response
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.api.routes.tokens import revoke_token
from app.core.config import get_settings
from app.models.auth_session import AuthSession
from app.models.export_job import ExportJob
from app.models.user import User
from app.services.auth_sessions import create_auth_session, lock_user_auth_state
from app.services.export_job_worker import execute_export_job
from tests.integration.test_export_jobs import _accept, export_env as export_env


def _accept_with_session(env):
    with Session(env.engine) as db:
        created = create_auth_session(
            db, user_id=env.owner_id, auth_method="local", mfa_method=None,
            client_ip=None, user_agent=None,
        )
        token, session_id = created.token, created.session.id
        db.commit()
    settings = get_settings()
    env.client.cookies.set(settings.auth_cookie_name, token)
    env.client.cookies.set(settings.auth_csrf_cookie_name, "test-export-csrf")
    env.headers = {settings.auth_csrf_header_name: "test-export-csrf"}
    return session_id


@pytest.mark.parametrize("credential_kind", ["api_token", "session_cookie"])
def test_publication_waits_for_owner_before_locking_accepting_credential(
    export_env, credential_kind,
):
    env = export_env
    session_id = _accept_with_session(env) if credential_kind == "session_cookie" else None
    job_id, _payload = _accept(env)
    owner_lock_requested = threading.Event()

    def observe_owner_lock(_connection, _cursor, statement, _parameters, _context, _many):
        if "FROM users" in statement and "FOR SHARE" in statement:
            owner_lock_requested.set()

    event.listen(env.engine, "before_cursor_execute", observe_owner_lock)
    try:
        with Session(env.engine) as mutation_db, ThreadPoolExecutor(max_workers=1) as pool:
            mutation_db.execute(text("SET LOCAL statement_timeout = '3s'"))
            owner = lock_user_auth_state(mutation_db, env.owner_id)
            future = pool.submit(execute_export_job, job_id)
            assert owner_lock_requested.wait(5)
            assert not future.done()
            if session_id is None:
                # This production route also locks the owner, then the token.
                revoke_token(env.credential_id, Response(), mutation_db, owner)
            else:
                session = mutation_db.scalar(
                    select(AuthSession).where(AuthSession.id == session_id).with_for_update()
                )
                session.revoked_at = datetime.now(timezone.utc)
                mutation_db.commit()
            result = future.result(timeout=5)
        assert result["status"] == "failed"
        with Session(env.engine) as db:
            job = db.get(ExportJob, job_id)
            assert job.status == "failed"
            assert job.error_code == "authorization_changed"
            assert db.get(User, env.owner_id).is_active
    finally:
        event.remove(env.engine, "before_cursor_execute", observe_owner_lock)
