import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import ModuleType
from typing import Any, get_type_hints

from app.tasks import feed_task_dependencies

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



def test_article_facade_passes_frozen_callback_and_configuration_snapshot(monkeypatch):
    observed = {}
    queued = []

    def enqueue(item_id):
        queued.append(item_id)
        return True

    def runner(task, item_id, force, *, dependencies):
        observed.update(item_id=item_id, force=force, dependencies=dependencies)
        return {"status": "delegated"}

    monkeypatch.setattr(feed_tasks, "_enqueue_classification_task", enqueue)
    monkeypatch.setattr(feed_tasks, "_run_fetch_article", runner)
    result = feed_tasks.fetch_article.run("item-id", force=True)
    assert result == {"status": "delegated"}
    assert observed["item_id"] == "item-id" and observed["force"] is True
    dependencies = observed["dependencies"]
    assert isinstance(dependencies, feed_task_dependencies.ArticleFetchDependencies)
    monkeypatch.setattr(feed_tasks, "_enqueue_classification_task", lambda _id: pytest.fail("late replacement"))
    assert dependencies.enqueue_classification("item-id")
    assert queued == ["item-id"]
    with pytest.raises(FrozenInstanceError):
        dependencies.enqueue_classification = lambda _id: False
    original = dependencies.settings.article_max_bytes
    monkeypatch.setattr(feed_tasks.settings, "article_max_bytes", original + 1)
    assert dependencies.settings.article_max_bytes == original
    assert not hasattr(dependencies.settings, "jwt_secret")
    assert not hasattr(dependencies.settings, "database_url")


def test_reprocess_facade_preserves_positional_contract(monkeypatch):
    observed = {}

    def runner(task, *args, dependencies):
        observed.update(args=args, dependencies=dependencies)
        return {"queued": 0}

    monkeypatch.setattr(feed_tasks, "_run_reprocess_recent_ai_items", runner)
    expected = (7, 50, "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00",
                ["feed-id"], ["item-id"], "run-id", "actor-id")
    assert feed_tasks.reprocess_recent_ai_items.run(*expected) == {"queued": 0}
    assert observed["args"] == expected
    assert isinstance(observed["dependencies"], feed_task_dependencies.ItemAIDependencies)


@pytest.mark.parametrize("runner,contract", [
    ("feed_fetch_tasks", "FeedFetchDependencies"), ("article_fetch_tasks", "ArticleFetchDependencies"),
    ("item_processing_tasks", "ItemProcessingDependencies"), ("item_ai_tasks", "ItemAIDependencies"),
])
def test_runner_boundaries_have_explicit_typed_capabilities(runner, contract):
    root = Path(__file__).resolve().parents[2] / "app/tasks"
    tree = ast.parse((root / f"{runner}.py").read_text())
    declared = get_type_hints(getattr(feed_task_dependencies, contract))
    assert 1 <= len(declared) <= 5
    assert all(annotation not in (Any, object, ModuleType) for annotation in declared.values())
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id not in {"ModuleType", "feed_tasks"}
        if isinstance(node, ast.ImportFrom):
            assert node.module != "app.tasks.feed_tasks"
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert (node.value.id, node.attr) != ("sys", "modules")
            if node.value.id == "dependencies":
                assert node.attr in declared
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"getattr", "vars", "__import__"}:
                assert not any(isinstance(arg, ast.Name) and arg.id == "dependencies" for arg in node.args)
    instance = getattr(feed_tasks, {
        "feed_fetch_tasks": "_feed_fetch_dependencies", "article_fetch_tasks": "_article_fetch_dependencies",
        "item_processing_tasks": "_item_processing_dependencies", "item_ai_tasks": "_item_ai_dependencies",
    }[runner])()
    assert not any(isinstance(getattr(instance, field.name), ModuleType) for field in fields(instance))


def test_tagging_repair_retains_a_durable_named_celery_entry_point():
    task = feed_tasks.repair_pending_item_tags
    assert task.name == "app.tasks.feed_tasks.repair_pending_item_tags"
    assert task.acks_late and task.reject_on_worker_lost
