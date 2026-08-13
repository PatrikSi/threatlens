export interface AIPromptPreview {
  label: string
  system_prompt: string
  notes: string[]
}

export interface AIPromptPreviews {
  item_enrichment: AIPromptPreview
  daily_brief: AIPromptPreview
}

export interface AISettings {
  id: string
  ai_enabled: boolean
  ai_configured: boolean
  api_key_configured: boolean
  provider_type: 'openai_compatible'
  base_url: string | null
  model: string | null
  temperature: number
  max_completion_tokens: number
  request_timeout_seconds: number
  request_max_retries: number
  summary_enabled: boolean
  relevance_enabled: boolean
  daily_brief_enabled: boolean
  reporting_enabled?: boolean
  auto_enrich_new_items: boolean
  daily_brief_window_hours: number
  daily_brief_max_items: number
  daily_brief_history_limit: number
  daily_brief_schedule_hour_utc: number
  daily_brief_schedule_minute_utc: number
  report_context_window_tokens?: number
  report_reserved_output_tokens?: number
  report_source_token_cap?: number
  report_max_sources?: number
  report_max_model_calls?: number
  report_context_safety_percent?: number
  relevance_medium_threshold: number
  relevance_high_threshold: number
  company_name: string | null
  company_industry: string | null
  company_regions: string[]
  company_stack: string[]
  company_priority_topics: string[]
  company_keywords: string[]
  company_exclusions: string[]
  company_profile_text: string | null
  item_enrichment_system_prompt: string | null
  daily_brief_system_prompt: string | null
  global_instructions: string | null
  item_summary_instructions: string | null
  relevance_instructions: string | null
  daily_brief_instructions: string | null
  created_at: string
  updated_at: string
  prompt_previews: AIPromptPreviews
}

export interface AISettingsUpdateRequest {
  provider_type: 'openai_compatible'
  base_url: string | null
  model: string | null
  temperature: number
  max_completion_tokens: number
  request_timeout_seconds: number
  request_max_retries: number
  summary_enabled: boolean
  relevance_enabled: boolean
  daily_brief_enabled: boolean
  reporting_enabled: boolean
  auto_enrich_new_items: boolean
  daily_brief_window_hours: number
  daily_brief_max_items: number
  daily_brief_history_limit: number
  daily_brief_schedule_hour_utc: number
  daily_brief_schedule_minute_utc: number
  report_context_window_tokens: number
  report_reserved_output_tokens: number
  report_source_token_cap: number
  report_max_sources: number
  report_max_model_calls: number
  report_context_safety_percent: number
  relevance_medium_threshold: number
  relevance_high_threshold: number
  company_name: string | null
  company_industry: string | null
  company_regions: string[]
  company_stack: string[]
  company_priority_topics: string[]
  company_keywords: string[]
  company_exclusions: string[]
  company_profile_text: string | null
  item_enrichment_system_prompt: string | null
  daily_brief_system_prompt: string | null
  global_instructions: string | null
  item_summary_instructions: string | null
  relevance_instructions: string | null
  daily_brief_instructions: string | null
}

export interface AITestConnectionResponse {
  success: boolean
  latency_ms: number | null
  provider: 'openai_compatible'
  model: string | null
  error: string | null
  skipped?: boolean
  skip_reason?: string | null
  running_task_count?: number
  queued_task_count?: number
}

export interface AIUsageFeatureSummary {
  feature_type: 'item_enrichment' | 'daily_brief' | 'report' | 'connection_test'
  total_requests: number
  successful_requests: number
  failed_requests: number
  total_tokens: number
  average_latency_ms: number
  last_request_at: string | null
}

export interface AIUsageSummary {
  total_requests: number
  successful_requests: number
  failed_requests: number
  success_rate_pct: number
  requests_last_24h: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  average_latency_ms: number
  last_request_at: string | null
  features: AIUsageFeatureSummary[]
}

export interface AIDailyBriefItem {
  id: string
  title: string
  feed_name: string
  url: string
  published_at: string | null
  relevance_score: number | null
  relevance_label: 'low' | 'medium' | 'high' | null
}

export interface AIDailyBrief {
  id: string
  brief_date: string
  status: string
  window_start: string
  window_end: string
  title: string | null
  brief_text: string | null
  key_points: string[]
  recommended_actions: string[]
  item_count: number
  items: AIDailyBriefItem[]
  model: string | null
  generated_at: string | null
  error: string | null
}

export interface AIReprocessResponse {
  task_id: string
  queued: boolean
  run_id: string | null
  celery_task_id: string | null
}

export interface AIQueuedTaskResponse {
  task_id: string
  queued: boolean
  run_id: string | null
  celery_task_id: string | null
}

export interface AIDailyBriefBackfillResponse extends AIQueuedTaskResponse {
  days: number
}

export interface AITaskRunResponse {
  id: string
  task_type: 'item_enrichment' | 'daily_brief' | 'report' | 'connection_test' | 'reprocess'
  trigger_source: 'auto' | 'manual' | 'scheduled'
  status: 'queued' | 'running' | 'ready' | 'error' | 'skipped'
  reason: string | null
  celery_task_id: string | null
  worker_name: string | null
  actor_user_id: string | null
  actor_email: string | null
  item_id: string | null
  item_title: string | null
  item_url: string | null
  feed_name: string | null
  item_first_seen_at: string | null
  item_published_at: string | null
  daily_brief_id: string | null
  parent_run_id: string | null
  model: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  latency_ms: number | null
  duration_ms: number | null
  prompt_char_count: number | null
  response_char_count: number | null
  input_text_chars: number | null
  error: string | null
  metadata: Record<string, unknown>
  target_count: number | null
  processed_count: number
  success_count: number
  error_count: number
  skipped_count: number
  skipped_unchanged_count: number
  skipped_ineligible_count: number
  queued_at: string
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export interface AITaskRunListResponse {
  total: number
  limit: number
  offset: number
  items: AITaskRunResponse[]
}

export interface AITaskEventResponse {
  id: string
  task_run_id: string
  event_type: string
  message: string | null
  payload: Record<string, unknown>
  created_at: string
}

export interface AITaskRunDetailResponse {
  run: AITaskRunResponse
  events: AITaskEventResponse[]
}

export interface AILiveTaskResponse {
  worker_name: string
  celery_task_id: string | null
  task_name: 'item_enrichment' | 'daily_brief' | 'report' | 'connection_test' | 'reprocess'
  state: 'active' | 'reserved' | 'scheduled'
  run_id: string | null
  item_id: string | null
  parent_run_id: string | null
  eta: string | null
  received_at: string | null
  raw_name: string | null
}

export interface AILiveStatusResponse {
  worker_count: number
  workers: string[]
  active_tasks: AILiveTaskResponse[]
  reserved_tasks: AILiveTaskResponse[]
  scheduled_tasks: AILiveTaskResponse[]
  active_count: number
  reserved_count: number
  scheduled_count: number
  queued_count: number
  oldest_queued_age_seconds: number | null
}

export interface AIOverviewKpiResponse {
  total_requests: number
  success_rate_pct: number
  total_tokens: number
  average_latency_ms: number
  p95_latency_ms: number
  active_runs: number
  queued_runs: number
  last_successful_run_at: string | null
}

export interface AIOverviewPerModelResponse {
  model: string
  total_requests: number
  successful_requests: number
  failed_requests: number
  success_rate_pct: number
  total_tokens: number
  average_latency_ms: number
  last_request_at: string | null
}

export interface AITimeSeriesPointResponse {
  bucket: string
  requests: number
  failures: number
  total_tokens: number
  average_latency_ms: number
  p95_latency_ms: number
  daily_brief_successes: number
  daily_brief_failures: number
  daily_brief_skips: number
}

export interface AITokenEfficiencyResponse {
  average_prompt_tokens: number
  average_completion_tokens: number
  average_total_tokens: number
  prompt_to_completion_ratio: number
  top_expensive_feature: string | null
  top_expensive_feature_avg_tokens: number
}

export interface AIRelevanceFeedResponse {
  feed_name: string
  total_items: number
  high_count: number
  medium_count: number
  low_count: number
  average_score: number
}

export interface AIRelevanceDistributionResponse {
  high_count: number
  medium_count: number
  low_count: number
  average_score: number
  by_feed: AIRelevanceFeedResponse[]
}

export interface AICoverageStatsResponse {
  eligible_items: number
  enriched_items: number
  pending_items: number
  failed_items: number
  skipped_no_article_count: number
  skipped_ai_disabled_count: number
  skipped_not_configured_count: number
  skipped_auto_enrich_disabled_count: number
  skipped_unchanged_count: number
  oldest_pending_at: string | null
  last_successful_enrichment_at: string | null
  last_successful_daily_brief_at: string | null
  last_ai_run_at: string | null
}

export interface AIFailureGroupResponse {
  task_type: 'item_enrichment' | 'daily_brief' | 'connection_test' | 'reprocess' | null
  feature_type: string | null
  model: string | null
  error: string
  count: number
  last_seen_at: string | null
}

export interface AIEndpointHealthResponse {
  last_success_at: string | null
  last_error_at: string | null
  rolling_failure_rate_pct: number
  median_latency_ms: number
  timeout_failures: number
  last_auth_error: string | null
  last_provider_error: string | null
}

export interface AIFeatureHealthRowResponse {
  feature_key: string
  enabled: boolean
  last_run_at: string | null
  last_success_at: string | null
  last_failure_at: string | null
  last_status: string | null
}

export interface AIStorageStatsResponse {
  retained_daily_briefs: number
  daily_brief_history_limit: number
  enrichment_rows: number
  usage_event_rows: number
  task_history_rows: number
  growth_last_7d: number
  growth_last_30d: number
}

export interface AICacheStatsResponse {
  reused_count: number
  recomputed_count: number
  no_op_rate_pct: number
}

export interface AIOpsOverviewResponse {
  kpis: AIOverviewKpiResponse
  live: AILiveStatusResponse
  per_model: AIOverviewPerModelResponse[]
  time_series: AITimeSeriesPointResponse[]
  token_efficiency: AITokenEfficiencyResponse
  relevance_distribution: AIRelevanceDistributionResponse
  coverage: AICoverageStatsResponse
  failures: AIFailureGroupResponse[]
  endpoint_health: AIEndpointHealthResponse
  feature_health: AIFeatureHealthRowResponse[]
  storage: AIStorageStatsResponse
  cache: AICacheStatsResponse
}

export interface AIDailyBriefSourceItemResponse {
  id: string
  daily_brief_id: string
  item_id: string | null
  included: boolean
  rank: number
  exclusion_reason: string | null
  title_snapshot: string
  feed_name_snapshot: string | null
  url_snapshot: string | null
  classification_snapshot: string | null
  relevance_score_snapshot: number | null
  relevance_label_snapshot: 'low' | 'medium' | 'high' | null
  published_at_snapshot: string | null
  first_seen_at_snapshot: string | null
  created_at: string
}

export interface AIAuditEntryResponse {
  id: string
  actor_user_id: string | null
  actor_email: string | null
  action: string
  resource_type: string
  resource_id: string | null
  success: boolean
  metadata: Record<string, unknown>
  created_at: string
}
