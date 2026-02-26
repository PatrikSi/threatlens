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

export interface Feed {
  id: string
  name: string
  url: string
  enabled: boolean
  fetch_interval_seconds: number
  etag: string | null
  last_modified: string | null
  last_fetch_at: string | null
  last_success_at: string | null
  error_count: number
  last_error: string | null
  created_at: string
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
  last_error: string | null
  tags: string[]
  article: Article | null
  state: ItemState
}

export interface Tag {
  id: string
  name: string
}
