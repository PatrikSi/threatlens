"""Restore quarantine also fences durable automatic article repair."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.budgets import database_operation
from app.models.api_token import ApiToken
from app.models.article import Article
from app.models.feed import Feed
from app.models.processing_work import ProcessingWork
from app.services import processing_worker as worker
from app.services.feed_pipeline import list_item_ids_missing_articles
from app.services.processing_dispatch import (
    discover_processing_work,
    maintain_processing_work,
    prepare_processing_publications,
)
from app.tasks.article_fetch_tasks import ArticleFetchResult
from app.tasks import feed_tasks
from tests.integration.test_export_jobs import export_env as export_env
from tests.integration.test_processing_recovery import (
    _accept,
    processing_env as processing_env,
)


def _publish(env):
    with Session(env.engine) as db:
        discover_processing_work(db, stage="article")
        db.commit()
    with Session(env.engine) as db:
        result = prepare_processing_publications(db, canary_at=None, stage="article")
        db.commit()
    return result


def _automatic_obligation(env, monkeypatch):
    monkeypatch.setattr(
        get_settings(), "dispatch_items_missing_articles_after_seconds", 0
    )
    with Session(env.engine) as db:
        db.execute(delete(Article).where(Article.item_id == env.item_id))
        db.commit()


def _fake_http(monkeypatch):
    called = []

    def fetch(*_args, **_kwargs):
        called.append(True)
        return ArticleFetchResult(
            "https://example.invalid/synthetic",
            200,
            "text/html",
            body=b"<html>Synthetic</html>",
        )

    monkeypatch.setattr("app.tasks.article_fetch_tasks._fetch_candidates", fetch)
    monkeypatch.setattr(
        "app.services.extraction.extract_readable_text",
        lambda _html: {
            "text": "Synthetic recovered source",
            "method": "test",
            "word_count": 3,
        },
    )
    return called


def _disable(env, *, revoke=False):
    with Session(env.engine) as db:
        db.get(Feed, env.feed_id).enabled = False
        if revoke:
            db.get(ApiToken, env.credential_id).revoked_at = datetime.now(timezone.utc)
        db.commit()


def _legacy_task(env, monkeypatch):
    monkeypatch.setattr(feed_tasks, "db_session", lambda: Session(env.engine))
    monkeypatch.setattr(feed_tasks.classify_item, "delay", lambda *_args: None)
    return lambda: feed_tasks.fetch_article.run(str(env.item_id))


def test_restored_revoked_manual_run_cannot_detach_into_disabled_feed_outbound_work(
    processing_env, monkeypatch
):
    env = processing_env
    _automatic_obligation(env, monkeypatch)
    _accept(env, "article")
    identity, token = _publish(env)[0]
    _disable(env, revoke=True)
    called = _fake_http(monkeypatch)
    assert worker.execute_processing_work(identity, token)["status"] == "attention"
    for _ in range(3):
        assert _publish(env) == []
    assert called == []
    with Session(env.engine) as db:
        work = db.get(ProcessingWork, identity)
        assert work.reason == "authorization_changed"
        assert work.recovery_run_id is not None


def test_disabled_automatic_feed_is_excluded_before_discovery(
    processing_env, monkeypatch
):
    env = processing_env
    _automatic_obligation(env, monkeypatch)
    _disable(env)
    assert _publish(env) == []
    with Session(env.engine) as db:
        assert (
            list_item_ids_missing_articles(db, limit=10, dispatch_after_seconds=0) == []
        )
        assert (
            db.scalar(
                select(ProcessingWork.id).where(ProcessingWork.item_id == env.item_id)
            )
            is None
        )


def test_disabled_feed_blocks_publication_and_releases_existing_automatic_reservation(
    processing_env, monkeypatch
):
    env = processing_env
    _automatic_obligation(env, monkeypatch)
    with Session(env.engine) as db:
        assert discover_processing_work(db, stage="article") == 1
        db.commit()
    _disable(env)
    with Session(env.engine) as db:
        assert (
            prepare_processing_publications(db, canary_at=None, stage="article") == []
        )
        assert maintain_processing_work(db) == 1
        db.commit()
        work = db.scalar(
            select(ProcessingWork).where(ProcessingWork.item_id == env.item_id)
        )
        assert work.status == "attention" and work.reason == "feed_disabled"
        assert work.attempts == 0


def test_previously_published_automatic_work_rechecks_feed_before_fetch_and_resumes_after_enable(
    processing_env, monkeypatch
):
    env = processing_env
    _automatic_obligation(env, monkeypatch)
    identity, token = _publish(env)[0]
    _disable(env)
    called = _fake_http(monkeypatch)
    assert worker.execute_processing_work(identity, token)["status"] == "attention"
    assert called == []
    with Session(env.engine) as db:
        assert db.get(ProcessingWork, identity).reason == "feed_disabled"
        assert (
            db.scalar(select(Article.id).where(Article.item_id == env.item_id)) is None
        )
        db.get(Feed, env.feed_id).enabled = True
        db.commit()
    replacement = _publish(env)
    assert len(replacement) == 1 and replacement[0][1] != token
    assert worker.execute_processing_work(*replacement[0])["status"] == "succeeded"
    assert called == [True]
    with Session(env.engine) as db:
        assert db.get(ProcessingWork, identity).attempts == 2


def test_valid_deliberate_manual_recovery_can_fetch_a_disabled_feed(
    processing_env, monkeypatch
):
    env = processing_env
    _disable(env)
    _accept(env, "article")
    identity, token = _publish(env)[0]
    called = _fake_http(monkeypatch)
    assert worker.execute_processing_work(identity, token)["status"] == "succeeded"
    assert called == [True]


@pytest.mark.parametrize("entry_point", ["durable", "legacy"])
def test_automatic_attempt_fences_feed_disable_until_its_outbound_unit_finishes(
    processing_env, monkeypatch, entry_point
):
    env = processing_env
    _automatic_obligation(env, monkeypatch)
    identity, token = _publish(env)[0]
    execute = (
        (lambda: worker.execute_processing_work(identity, token))
        if entry_point == "durable"
        else _legacy_task(env, monkeypatch)
    )
    _fake_http(monkeypatch)
    entered, release = Event(), Event()

    def fetch(*_args, **_kwargs):
        entered.set()
        assert release.wait(10)
        return ArticleFetchResult(
            "https://example.invalid/synthetic",
            200,
            "text/html",
            body=b"<html>Synthetic</html>",
        )

    monkeypatch.setattr("app.tasks.article_fetch_tasks._fetch_candidates", fetch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(execute)
        assert entered.wait(10)
        try:
            with pytest.raises(OperationalError):
                with (
                    Session(env.engine) as db,
                    database_operation(
                        db, operation="interactive", timeout_seconds=0.1
                    ),
                ):
                    db.execute(
                        update(Feed).where(Feed.id == env.feed_id).values(enabled=False)
                    )
                    db.commit()
        finally:
            release.set()
        assert result.result(timeout=10)["status"] == (
            "succeeded" if entry_point == "durable" else "ok"
        )
    _disable(env)


@pytest.mark.parametrize("force", [False, True])
def test_legacy_public_task_skips_disabled_automatic_fetch_but_allows_force(
    processing_env, monkeypatch, force
):
    env = processing_env
    _automatic_obligation(env, monkeypatch)
    _disable(env)
    _legacy_task(env, monkeypatch)
    called = _fake_http(monkeypatch)
    result = feed_tasks.fetch_article.run(str(env.item_id), force=force)
    assert called == ([True] if force else [])
    if force:
        assert result["status"] == "ok"
    else:
        assert result["status"] == "skipped" and result["reason"] == "feed_disabled"
