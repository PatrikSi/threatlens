"""Automatic repair waits for article input; explicit recovery stays available."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.article import Article
from app.models.item import Item
from app.models.processing_work import ProcessingWork
from app.services import processing_worker as worker
from app.services.processing_dispatch import (
    discover_processing_work,
    prepare_processing_publications,
)
from app.tasks.article_fetch_tasks import ArticleFetchResult, run_fetch_article
from app.tasks.feed_task_dependencies import ArticleFetchDependencies, ArticleFetchOptions
from tests.integration.test_export_jobs import export_env as export_env
from tests.integration.test_processing_recovery import (
    _accept,
    _selection,
    processing_env as processing_env,
)


def _pending_fetch(env, *, prior_article=True):
    with Session(env.engine) as db:
        item = db.get(Item, env.item_id)
        item.status = "new"
        item.tagging_pending = True
        item.tagging_pending_since_at = datetime.now(timezone.utc)
        item.tagging_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        if not prior_article:
            db.execute(delete(Article).where(Article.item_id == item.id))
        version = item.classification_required_version
        db.commit()
    return version


def _mock_article_fetch(env, monkeypatch, *, failed=False):
    monkeypatch.setattr(
        "app.tasks.article_fetch_tasks._fetch_candidates",
        lambda *_args, **_kwargs: ArticleFetchResult(
            "https://example.invalid/synthetic",
            500 if failed else 200,
            "text/html",
            body=b"<html>Synthetic</html>",
            error="http_status:500" if failed else None,
        ),
    )
    monkeypatch.setattr(
        "app.services.extraction.extract_readable_text",
        lambda _html: {
            "text": "Current article evidence contains 203.0.113.7",
            "method": "test",
            "word_count": 6,
        },
    )
    # Keep this local regression's intelligence events durable without publishing
    # Celery messages to any integration worker.
    monkeypatch.setattr(
        "app.tasks.integration_tasks.enqueue_integration_event_routing",
        lambda _ids: True,
    )
    return run_fetch_article(
        SimpleNamespace(request=SimpleNamespace(retries=3)),
        str(env.item_id),
        dependencies=ArticleFetchDependencies(
            db_session=lambda: Session(env.engine),
            settings=ArticleFetchOptions.from_settings(get_settings()),
            enqueue_classification=lambda _item: True,
        ),
    )


def _discover_and_finish(env, stage):
    with Session(env.engine) as db:
        assert discover_processing_work(db, stage=stage) == 1
        work = db.scalar(select(ProcessingWork).where(
            ProcessingWork.item_id == env.item_id, ProcessingWork.stage == stage,
        ))
        assert work.source_version == db.get(Item, env.item_id).classification_required_version
        db.commit()
    with Session(env.engine) as db:
        publications = prepare_processing_publications(db, canary_at=None, stage=stage)
        db.commit()
    assert len(publications) == 1
    assert worker.execute_processing_work(*publications[0])["status"] == "succeeded"


@pytest.mark.parametrize("stage", ["classification", "ioc", "tagging"])
@pytest.mark.parametrize("prior_article", [False, True], ids=["first-fetch", "refresh"])
def test_automatic_derivation_waits_for_the_committed_article_revision(
    processing_env, monkeypatch, stage, prior_article,
):
    env = processing_env
    previous_version = _pending_fetch(env, prior_article=prior_article)
    # Pending work remains visible for explicit operator recovery.
    assert _selection(env, stage)["stage"] == stage
    with Session(env.engine) as db:
        assert discover_processing_work(db, stage=stage) == 0
        assert db.scalar(select(ProcessingWork.id).where(
            ProcessingWork.item_id == env.item_id,
        )) is None
        db.commit()

    assert _mock_article_fetch(env, monkeypatch)["status"] == "ok"
    with Session(env.engine) as db:
        item = db.get(Item, env.item_id)
        assert item.status == "content_fetched"
        assert item.classification_required_version == previous_version + 1
    _discover_and_finish(env, stage)


@pytest.mark.parametrize("stage", ["classification", "ioc", "tagging"])
def test_failed_fetch_still_allows_automatic_title_and_summary_derivation(
    processing_env, monkeypatch, stage,
):
    env = processing_env
    _pending_fetch(env)
    assert _mock_article_fetch(env, monkeypatch, failed=True)["status"] == "error"
    with Session(env.engine) as db:
        assert db.get(Item, env.item_id).status == "error"
        assert db.scalar(select(Article.text).where(Article.item_id == env.item_id)) is None
    _discover_and_finish(env, stage)


@pytest.mark.parametrize("stage", ["classification", "ioc", "tagging"])
def test_explicit_waiting_recovery_can_process_an_item_before_fetch_finishes(
    processing_env, monkeypatch, stage,
):
    env = processing_env
    _pending_fetch(env)
    monkeypatch.setattr(
        "app.tasks.integration_tasks.enqueue_integration_event_routing",
        lambda _ids: True,
    )
    _accept(env, stage)
    _discover_and_finish(env, stage)


def test_automatic_article_repair_keeps_its_fetch_grace_period(
    processing_env, monkeypatch,
):
    env = processing_env
    _pending_fetch(env, prior_article=False)
    monkeypatch.setattr(get_settings(), "dispatch_items_missing_articles_after_seconds", 300)
    with Session(env.engine) as db:
        assert discover_processing_work(db, stage="article") == 0
        db.get(Item, env.item_id).first_seen_at = datetime.now(timezone.utc) - timedelta(seconds=301)
        db.commit()
    with Session(env.engine) as db:
        assert discover_processing_work(db, stage="article") == 1
        db.commit()
