"""Real route/database fences must end when an artifact transfer stalls."""
import anyio
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.models.article import Article
from app.services.authorization import lock_iam_policy_for_mutation
from app.services.export_job_worker import execute_export_job
from app.services.export_transport import ExportTransferDeadlineExceeded
from tests.integration.test_export_jobs import _accept, export_env as export_env


def test_download_deadline_releases_policy_locks_through_actual_app_middleware(export_env, monkeypatch):
    env = export_env
    with Session(env.engine) as db:
        article = db.scalar(select(Article).where(Article.item_id == env.item_id))
        article.text = "private article evidence " * 25_000
        db.commit()
    job_id, _payload = _accept(env)
    assert execute_export_job(job_id)["status"] == "ready"
    monkeypatch.setattr(get_settings(), "export_transfer_timeout_seconds", .5)
    original = app.dependency_overrides[get_db]
    closed = []

    def sessions():
        with Session(env.engine) as db:
            try:
                yield db
            finally:
                db.close()
                closed.append(True)

    def try_policy_mutation():
        with Session(env.engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '75ms'"))
            try:
                lock_iam_policy_for_mutation(db)
                return "locked"
            except DBAPIError as exc:
                return exc.orig.sqlstate

    observed = []
    headers = [(b"host", b"testserver"), *(
        (name.lower().encode(), value.encode()) for name, value in env.headers.items()
    )]
    path = f"/exports/jobs/{job_id}/download"
    scope = {
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": headers, "scheme": "http", "server": ("test", 80),
        "client": ("127.0.0.1", 1234), "asgi": {"spec_version": "2.4"},
    }

    async def receive():
        await anyio.sleep_forever()

    async def blocked_send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 200
        if message["type"] == "http.response.body":
            observed.append(await anyio.to_thread.run_sync(try_policy_mutation))
            await anyio.sleep_forever()

    app.dependency_overrides[get_db] = sessions
    try:
        with pytest.raises(ExportTransferDeadlineExceeded):
            anyio.run(app, scope, receive, blocked_send)
        assert observed == ["57014"]
        assert closed == [True]
        assert try_policy_mutation() == "locked"
    finally:
        app.dependency_overrides[get_db] = original
