import type { AIOpsOverviewResponse } from '../src/types/ai'

export const emptyOverview = {
  "kpis": {
    "total_requests": 0,
    "success_rate_pct": 0,
    "total_tokens": 0,
    "average_latency_ms": 0,
    "p95_latency_ms": 0,
    "active_runs": 0,
    "queued_runs": 0,
    "last_successful_run_at": null
  },
  "live": {
    "worker_count": 0,
    "workers": [],
    "active_tasks": [],
    "reserved_tasks": [],
    "scheduled_tasks": [],
    "active_count": 0,
    "reserved_count": 0,
    "scheduled_count": 0,
    "queued_count": 0,
    "oldest_queued_age_seconds": null
  },
  "per_model": [],
  "time_series": [],
  "token_efficiency": {
    "average_prompt_tokens": 0,
    "average_completion_tokens": 0,
    "average_total_tokens": 0,
    "prompt_to_completion_ratio": 0,
    "top_expensive_feature": null,
    "top_expensive_feature_avg_tokens": 0
  },
  "relevance_distribution": {
    "high_count": 0,
    "medium_count": 0,
    "low_count": 0,
    "average_score": 0,
    "by_feed": []
  },
  "coverage": {
    "eligible_items": 0,
    "enriched_items": 0,
    "pending_items": 0,
    "failed_items": 0,
    "skipped_no_article_count": 0,
    "skipped_ai_disabled_count": 0,
    "skipped_not_configured_count": 0,
    "skipped_auto_enrich_disabled_count": 0,
    "skipped_unchanged_count": 0,
    "oldest_pending_at": null,
    "last_successful_enrichment_at": null,
    "last_successful_daily_brief_at": null,
    "last_ai_run_at": null
  },
  "failures": [],
  "endpoint_health": {
    "last_success_at": null,
    "last_error_at": null,
    "rolling_failure_rate_pct": 0,
    "median_latency_ms": 0,
    "timeout_failures": 0,
    "last_auth_error": null,
    "last_provider_error": null
  },
  "feature_health": [],
  "storage": {
    "retained_daily_briefs": 0,
    "daily_brief_history_limit": 0,
    "enrichment_rows": 0,
    "usage_event_rows": 0,
    "task_history_rows": 0,
    "growth_last_7d": 0,
    "growth_last_30d": 0
  },
  "cache": {
    "reused_count": 0,
    "recomputed_count": 0,
    "no_op_rate_pct": 0
  }
} satisfies AIOpsOverviewResponse
