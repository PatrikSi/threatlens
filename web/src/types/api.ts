export interface User {
  id: string
  email: string
  role: 'admin' | 'analyst' | 'viewer'
  is_active: boolean
  is_approved: boolean
  approved_at: string | null
  created_at: string
}

export interface AppFeatures {
  ai_enabled: boolean
  ai_configured: boolean
  ai_summary_enabled: boolean
  ai_relevance_enabled: boolean
  ai_daily_brief_enabled: boolean
}

export interface CurrentUser extends User {
  features: AppFeatures
}

export interface TokenResponse {
  access_token: string
  token_type: string
  csrf_token: string | null
}

export interface UserCreateRequest {
  email: string
  password: string
  role: 'admin' | 'analyst' | 'viewer'
  is_active: boolean
  is_approved: boolean
}

export interface UserUpdateRequest {
  email?: string
  password?: string
  role?: 'admin' | 'analyst' | 'viewer'
  is_active?: boolean
  is_approved?: boolean
}

export interface RegistrationSettingsResponse {
  allow_self_registration: boolean
  ai_enabled: boolean
}

export interface ApiToken {
  id: string
  user_id: string
  name: string
  token_prefix: string
  scopes: string[]
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface ApiTokenCreateResponse {
  token: string
  token_prefix: string
  expires_at: string | null
}

export interface AuditLog {
  id: string
  actor_user_id: string | null
  action: string
  resource_type: string
  resource_id: string | null
  success: boolean
  metadata_json: Record<string, unknown>
  created_at: string
}

export interface AuditLogListResponse {
  logs: AuditLog[]
  total: number
  page: number
  page_size: number
}

export interface AuditLogExportResponse {
  exported_at: string
  total: number
  truncated: boolean
  logs: AuditLog[]
}

export interface SavedView {
  id: string
  user_id: string
  name: string
  query_json: Record<string, unknown>
  created_at: string
}

export interface StatsTotalsSummary {
  feeds_total: number
  feeds_enabled: number
  feeds_disabled: number
  items_total: number
  items_new: number
  items_content_fetched: number
  items_error: number
  articles_total: number
}

export interface StatsActivitySummary {
  items_last_24h: number
  items_last_7d: number
  items_last_30d: number
}

export interface StatsDerivedSummary {
  extraction_success_rate_pct: number
  error_rate_pct: number
  avg_items_per_day_window: number
}

export interface StatsStatusPoint {
  status: string
  count: number
}

export interface StatsDailyPoint {
  date: string
  count: number
}

export interface StatsFeedPoint {
  feed_id: string
  feed_name: string
  total_items: number
  items_in_window: number
  error_items: number
  content_fetched_items: number
  last_published_at: string | null
  last_seen_at: string | null
}

export interface StatsDomainPoint {
  domain: string
  count: number
}

export interface StatsOverviewResponse {
  generated_at: string
  window_days: number
  totals: StatsTotalsSummary
  activity: StatsActivitySummary
  derived: StatsDerivedSummary
  status_breakdown: StatsStatusPoint[]
  daily_volume: StatsDailyPoint[]
  feed_breakdown: StatsFeedPoint[]
  top_domains: StatsDomainPoint[]
}

export interface StatsFeedTimeSeriesPoint {
  date: string
  count: number
}

export interface StatsFeedTimeSeriesSeries {
  feed_id: string
  feed_name: string
  points: StatsFeedTimeSeriesPoint[]
}

export interface StatsFeedTimeSeriesResponse {
  generated_at: string
  window_days: number
  series: StatsFeedTimeSeriesSeries[]
}

export interface StatsActivityHeatmapDayRow {
  day: string
  counts: number[]
}

export interface StatsActivityHeatmapResponse {
  generated_at: string
  window_days: number
  bucket_unit: 'hour' | 'day'
  bucket_labels: string[]
  rows: StatsActivityHeatmapDayRow[]
  max_count: number
}

export interface StatsSignalRadarAxisPoint {
  category: string
  count: number
  pct: number
}

export interface StatsSignalRadarResponse {
  generated_at: string
  window_days: number
  total: number
  max_count: number
  axes: StatsSignalRadarAxisPoint[]
}

export interface Feed {
  id: string
  name: string
  url: string
  description: string | null
  site_url: string | null
  language: string | null
  enabled: boolean
  fetch_mode: 'interval' | 'schedule'
  fetch_interval_seconds: number
  schedule_cron: string | null
  etag: string | null
  last_modified: string | null
  last_fetch_at: string | null
  last_success_at: string | null
  error_count: number
  last_error: string | null
  created_at: string
}

export interface FeedMetadataResponse {
  name: string | null
  description: string | null
  site_url: string | null
  language: string | null
  etag: string | null
  last_modified: string | null
  resolved_url: string | null
  feed_type: string | null
}

export interface FeedImportEntry {
  name: string | null
  url: string
  description: string | null
  site_url: string | null
  language: string | null
  enabled: boolean
  fetch_mode: 'interval' | 'schedule'
  fetch_interval_seconds: number | null
  schedule_cron: string | null
}

export interface FeedExportResponse {
  exported_at: string
  feeds: FeedImportEntry[]
}

export interface FeedImportResponse {
  created: number
  updated: number
  skipped: number
  errors: string[]
}

export interface ItemListEntry {
  id: string
  feed_id: string
  feed_name: string
  url: string
  canonical_url: string | null
  title: string
  summary: string | null
  published_at: string | null
  first_seen_at: string
  status: string
  classification: string | null
  is_read: boolean
  is_starred: boolean
  tags: string[]
  ai_relevance_score: number | null
  ai_relevance_label: 'low' | 'medium' | 'high' | null
  ai_status: string | null
}

export interface ItemListResponse {
  items: ItemListEntry[]
  total: number
  page: number
  page_size: number
}

export interface Article {
  final_url: string
  retrieved_at: string
  http_status: number
  content_type: string | null
  title_extracted: string | null
  text: string | null
  extraction_method: string | null
  language: string | null
  word_count: number | null
  fetch_ms: number | null
  error: string | null
}

export interface ItemState {
  is_read: boolean
  is_starred: boolean
  note: string | null
  updated_at: string | null
}

export interface ItemDetail {
  id: string
  feed_id: string
  feed_name: string
  source_guid: string | null
  url: string
  canonical_url: string | null
  title: string
  summary: string | null
  published_at: string | null
  first_seen_at: string
  status: string
  classification: {
    primary_category: string
    secondary_categories: string[]
    confidence: number
    scores: Record<string, number>
    rules_version: string
    classified_at: string
  } | null
  last_error: string | null
  tags: string[]
  ai_insight: {
    status: string
    summary_text: string | null
    relevance_score: number | null
    relevance_label: 'low' | 'medium' | 'high' | null
    relevance_reasons: string[]
    model: string | null
    generated_at: string | null
    error: string | null
  } | null
  article: Article | null
  state: ItemState
}

export interface ItemGraphNode {
  id: string
  type: string
  label: string
  metadata: Record<string, unknown>
}

export interface ItemGraphEdge {
  source: string
  target: string
  relation: string
  weight: number
}

export interface ItemGraphResponse {
  nodes: ItemGraphNode[]
  edges: ItemGraphEdge[]
  focus_node_id: string | null
  root_item_id: string | null
}

export interface Tag {
  id: string
  name: string
}

export interface AlertInterest {
  id: string
  user_id: string
  name: string
  category: string
  keywords: string[]
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface AlertMatchReference {
  alert_id: string
  alert_name: string
  category: string
  matched_keywords: string[]
}

export interface AlertMatchEntry extends ItemListEntry {
  matches: AlertMatchReference[]
}

export interface AlertMatchListResponse {
  items: AlertMatchEntry[]
  total: number
  page: number
  page_size: number
}

export interface NotificationWebhookField {
  key: string
  value: string
}

export interface NotificationTemplateVariable {
  key: string
  description: string
  example: string
}

export type NotificationEventType = 'rss_item_new' | 'alert_match' | 'feed_failing' | 'webhook_failed' | 'daily_digest'

export interface NotificationWebhook {
  id: string
  user_id: string
  name: string
  enabled: boolean
  event_type: NotificationEventType
  url_template: string
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  query_params: NotificationWebhookField[]
  headers: NotificationWebhookField[]
  body_mode: 'none' | 'json' | 'form' | 'raw'
  body_fields: NotificationWebhookField[]
  body_template: string | null
  timeout_seconds: number
  created_at: string
  updated_at: string
}

export interface NotificationWebhookWriteRequest {
  name: string
  enabled: boolean
  event_type: NotificationEventType
  url_template: string
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  query_params: NotificationWebhookField[]
  headers: NotificationWebhookField[]
  body_mode: 'none' | 'json' | 'form' | 'raw'
  body_fields: NotificationWebhookField[]
  body_template: string | null
  timeout_seconds: number
}

export interface NotificationWebhookTestResponse {
  success: boolean
  status_code: number | null
  duration_ms: number | null
  rendered_url: string
  rendered_method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  rendered_headers: NotificationWebhookField[]
  rendered_query_params: NotificationWebhookField[]
  rendered_body: string | null
  response_body_preview: string | null
  error: string | null
}

export interface NotificationWebhookDelivery {
  id: string
  webhook_id: string
  user_id: string
  event_type: NotificationEventType
  item_id: string | null
  feed_id: string | null
  item_title: string | null
  feed_name: string | null
  delivery_kind: 'live' | 'retry'
  success: boolean
  status_code: number | null
  duration_ms: number | null
  timeout_seconds: number
  rendered_url: string
  rendered_method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  rendered_headers: NotificationWebhookField[]
  rendered_query_params: NotificationWebhookField[]
  rendered_body: string | null
  response_body_preview: string | null
  error: string | null
  attempted_at: string
}

export interface NotificationWebhookDeliveryListResponse {
  deliveries: NotificationWebhookDelivery[]
  total: number
  page: number
  page_size: number
}

export interface NotificationAnalyticsEventSummary {
  event_type: NotificationEventType
  total_deliveries: number
  failed_deliveries: number
}

export interface NotificationAnalyticsWebhookSummary {
  webhook_id: string
  webhook_name: string
  failed_deliveries: number
  last_failure_at: string | null
}

export interface NotificationAnalyticsResponse {
  total_deliveries: number
  successful_deliveries: number
  failed_deliveries: number
  success_rate_pct: number
  failures_last_24h: number
  most_failing_webhook: NotificationAnalyticsWebhookSummary | null
  events: NotificationAnalyticsEventSummary[]
}

export interface TaggingSettings {
  id: string
  enabled_categories: string[]
  min_auto_tag_confidence: number
  secondary_tag_limit: number
  created_at: string
  updated_at: string
}

export interface TaggingRule {
  id: string
  name: string
  tag_name: string
  enabled: boolean
  match_type: 'contains' | 'regex'
  pattern: string
  case_sensitive: boolean
  applies_to: Array<'title' | 'summary' | 'article_text' | 'feed_name'>
  required_categories: string[]
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  min_classification_confidence: number | null
  created_at: string
  updated_at: string
}

export interface TaggingSettingsBundleResponse {
  settings: TaggingSettings
  rules: TaggingRule[]
}

export interface TaggingRuleWriteRequest {
  name: string
  tag_name: string
  enabled: boolean
  match_type: 'contains' | 'regex'
  pattern: string
  case_sensitive: boolean
  applies_to: Array<'title' | 'summary' | 'article_text' | 'feed_name'>
  required_categories: string[]
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  min_classification_confidence: number | null
}

export interface TaggingRulePreviewItem {
  id: string
  title: string
  feed_name: string
  classification: string | null
  first_seen_at: string
  current_tags: string[]
  matched_sections: string[]
}

export interface TaggingRulePreviewResponse {
  total: number
  items: TaggingRulePreviewItem[]
}

export interface TaggingReapplyResponse {
  task_id: string
  queued: boolean
}

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
  summary_enabled: boolean
  relevance_enabled: boolean
  daily_brief_enabled: boolean
  auto_enrich_new_items: boolean
  daily_brief_window_hours: number
  daily_brief_max_items: number
  daily_brief_history_limit: number
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
  summary_enabled: boolean
  relevance_enabled: boolean
  daily_brief_enabled: boolean
  auto_enrich_new_items: boolean
  daily_brief_window_hours: number
  daily_brief_max_items: number
  daily_brief_history_limit: number
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
}

export interface AIUsageFeatureSummary {
  feature_type: 'item_enrichment' | 'daily_brief' | 'connection_test'
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
}
