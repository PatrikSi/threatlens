export interface User {
  id: string
  email: string
  role: 'admin' | 'analyst' | 'viewer'
  is_active: boolean
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface UserCreateRequest {
  email: string
  password: string
  role: 'admin' | 'analyst' | 'viewer'
  is_active: boolean
}

export interface UserUpdateRequest {
  email?: string
  password?: string
  role?: 'admin' | 'analyst' | 'viewer'
  is_active?: boolean
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
