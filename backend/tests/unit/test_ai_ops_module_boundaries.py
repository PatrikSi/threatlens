from pathlib import Path

from app.services import (
    ai_ops,
    ai_ops_common,
    ai_ops_metrics,
    ai_task_projection,
    ai_task_runtime,
)


def test_ai_ops_preserves_extracted_import_paths():
    assert ai_ops.AIConnectionTestWorkload is ai_ops_common.AIConnectionTestWorkload
    assert ai_ops._coerce_utc is ai_ops_common._coerce_utc
    assert ai_ops._extract_uuid is ai_ops_common._extract_uuid
    assert ai_ops._merge_metadata is ai_ops_common._merge_metadata
    assert ai_ops._percentile is ai_ops_common._percentile

    assert ai_ops._load_live_task_snapshot is ai_task_runtime._load_live_task_snapshot
    assert (
        ai_ops._normalize_live_task_snapshot
        is ai_task_runtime._normalize_live_task_snapshot
    )
    assert ai_ops._flatten_live_tasks is ai_task_runtime._flatten_live_tasks
    assert ai_ops._coerce_live_args is ai_task_runtime._coerce_live_args
    assert (
        ai_ops._extract_positional_task_run_id
        is ai_task_runtime._extract_positional_task_run_id
    )
    assert (
        ai_ops._extract_positional_item_id
        is ai_task_runtime._extract_positional_item_id
    )
    assert ai_ops.get_ai_db_live_status is ai_task_runtime.get_ai_db_live_status

    assert ai_ops._map_run_responses is ai_task_projection._map_run_responses
    assert ai_ops._map_audit_entries is ai_task_projection._map_audit_entries
    assert ai_ops._load_run_item_context is ai_task_projection._load_run_item_context
    assert ai_ops._load_user_emails is ai_task_projection._load_user_emails
    assert ai_ops.list_ai_manual_actions is ai_task_projection.list_ai_manual_actions
    assert ai_ops.list_ai_prompt_history is ai_task_projection.list_ai_prompt_history
    assert (
        ai_ops.list_daily_brief_source_items
        is ai_task_projection.list_daily_brief_source_items
    )

    assert ai_ops.list_ai_failures is ai_ops_metrics.list_ai_failures
    assert ai_ops._build_per_model_usage is ai_ops_metrics._build_per_model_usage
    assert ai_ops._build_time_series is ai_ops_metrics._build_time_series
    assert ai_ops._build_token_efficiency is ai_ops_metrics._build_token_efficiency
    assert (
        ai_ops._build_relevance_distribution
        is ai_ops_metrics._build_relevance_distribution
    )
    assert ai_ops._build_coverage_stats is ai_ops_metrics._build_coverage_stats
    assert ai_ops._load_skip_counts is ai_ops_metrics._load_skip_counts
    assert ai_ops._build_endpoint_health is ai_ops_metrics._build_endpoint_health
    assert ai_ops._build_feature_health is ai_ops_metrics._build_feature_health
    assert ai_ops._build_storage_stats is ai_ops_metrics._build_storage_stats
    assert ai_ops._build_cache_stats is ai_ops_metrics._build_cache_stats
    assert ai_ops._normalize_error_text is ai_ops_metrics._normalize_error_text
    assert ai_ops._looks_like_auth_error is ai_ops_metrics._looks_like_auth_error


def test_ai_ops_overview_delegates_without_a_reverse_import(monkeypatch):
    database = object()
    sentinel = object()
    captured = {}

    def fake_builder(db, *, days, live_status_loader):
        captured.update(db=db, days=days, live_status_loader=live_status_loader)
        return sentinel

    monkeypatch.setattr(ai_ops, "build_ai_ops_overview", fake_builder)

    assert ai_ops.get_ai_ops_overview(database, days=14) is sentinel
    assert captured == {
        "db": database,
        "days": 14,
        "live_status_loader": ai_ops.get_ai_db_live_status,
    }


def test_ai_ops_facade_stays_below_modularity_limit():
    source_path = Path(ai_ops.__file__)

    assert len(source_path.read_text().splitlines()) < 1_200
