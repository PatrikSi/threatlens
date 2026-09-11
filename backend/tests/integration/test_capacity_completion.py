"""An empty broker must not hide incomplete application work in capacity gates."""

import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.item import Item
from app.models.item_classification import ItemClassification
from tests.capacity import test_concurrent_workload as workload
from tests.integration.test_export_jobs import export_env as export_env


@pytest.mark.parametrize("state", [
    "complete", "tagging_pending", "missing_body", "empty_body", "blank_body",
    "fetch_incomplete", "classification_stale", "classification_missing", "ioc_pending",
])
def test_empty_broker_only_recovers_complete_pipeline_work(export_env, monkeypatch, state):
    env = export_env
    private_body = "Synthetic article text excluded from diagnostic output"
    with Session(env.engine) as db:
        item = db.get(Item, env.item_id)
        item.status = "content_fetched"
        item.classification_completed_version = item.classification_required_version
        item.ioc_extraction_state = "completed_empty"
        item.tagging_pending = state == "tagging_pending"
        article = db.scalar(select(Article).where(Article.item_id == env.item_id))
        article.text = {
            "missing_body": None, "empty_body": "", "blank_body": "   ",
        }.get(state, private_body)
        if state != "classification_missing":
            db.add(ItemClassification(
                item_id=item.id, primary_category="threat", source_hash=item.content_hash,
            ))
        if state == "fetch_incomplete":
            item.status = "new"
        if state == "classification_stale":
            item.classification_completed_version = 0
        if state == "ioc_pending":
            item.ioc_extraction_state = None
        db.commit()

    publications = []
    outcomes = []
    monkeypatch.setattr(
        workload.processing_tasks.dispatch_processing_work, "delay",
        lambda: publications.append("all_stages"),
    )
    broker = SimpleNamespace(llen=lambda _: 0, hlen=lambda _: 0)
    metrics = SimpleNamespace(outcome=outcomes.append)

    def wait():
        return workload._wait_for_pipeline(
            env.engine, broker, [env.feed_id], lambda: 1, metrics,
            time.monotonic(), 0.01, [],
        )

    if state == "complete":
        assert wait() >= 0
        assert outcomes == ["ingestion_recovered"]
        assert publications == []
    else:
        with pytest.raises(AssertionError, match="0/1 fully processed articles") as failure:
            wait()
        assert "ingestion_recovered" not in outcomes
        assert publications == ["all_stages"]
        assert str(env.item_id) in str(failure.value)
        assert private_body not in str(failure.value)
