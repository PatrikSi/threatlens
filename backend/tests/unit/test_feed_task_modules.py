import sys

import pytest

from app.tasks import feed_tasks


@pytest.mark.parametrize(
    ("task", "expected_name"),
    [
        (
            feed_tasks.backfill_feed_metadata,
            "app.tasks.feed_tasks.backfill_feed_metadata",
        ),
        (feed_tasks.fetch_feed, "app.tasks.feed_tasks.fetch_feed"),
        (feed_tasks.fetch_article, "app.tasks.feed_tasks.fetch_article"),
        (feed_tasks.classify_item, "app.tasks.feed_tasks.classify_item"),
        (
            feed_tasks.generate_item_ai_enrichment_task,
            "app.tasks.feed_tasks.generate_item_ai_enrichment",
        ),
        (
            feed_tasks.reprocess_recent_ai_items,
            "app.tasks.feed_tasks.reprocess_recent_ai_items",
        ),
        (feed_tasks.extract_item_iocs, "app.tasks.feed_tasks.extract_item_iocs"),
        (
            feed_tasks.reapply_recent_item_tags,
            "app.tasks.feed_tasks.reapply_recent_item_tags",
        ),
    ],
)
def test_extracted_task_names_remain_compatible(task, expected_name):
    assert task.name == expected_name
    assert task.acks_late is True
    assert task.reject_on_worker_lost is True


def test_fetch_article_facade_passes_live_module(monkeypatch):
    observed = {}

    def _runner(task, item_id, force, *, runtime):
        observed.update(task=task, item_id=item_id, force=force, runtime=runtime)
        return {"status": "delegated"}

    monkeypatch.setattr(feed_tasks, "_run_fetch_article", _runner)

    result = feed_tasks.fetch_article.run("item-id", force=True)

    assert result == {"status": "delegated"}
    assert observed["item_id"] == "item-id"
    assert observed["force"] is True
    assert observed["runtime"] is sys.modules["app.tasks.feed_tasks"]


def test_reprocess_facade_preserves_positional_contract(monkeypatch):
    observed = {}

    def _runner(task, *args, runtime):
        observed.update(task=task, args=args, runtime=runtime)
        return {"queued": 0}

    monkeypatch.setattr(feed_tasks, "_run_reprocess_recent_ai_items", _runner)

    result = feed_tasks.reprocess_recent_ai_items.run(
        7,
        50,
        "2026-01-01T00:00:00+00:00",
        "2026-01-02T00:00:00+00:00",
        ["feed-id"],
        ["item-id"],
        "run-id",
        "actor-id",
    )

    assert result == {"queued": 0}
    assert observed["args"] == (
        7,
        50,
        "2026-01-01T00:00:00+00:00",
        "2026-01-02T00:00:00+00:00",
        ["feed-id"],
        ["item-id"],
        "run-id",
        "actor-id",
    )
    assert observed["runtime"] is feed_tasks


def test_legacy_runtime_dependencies_remain_patchable(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(feed_tasks, "build_safe_http_client", sentinel)

    assert sys.modules["app.tasks.feed_tasks"].build_safe_http_client is sentinel
