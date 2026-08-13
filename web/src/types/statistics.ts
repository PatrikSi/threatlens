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
  window_start_at: string
  window_end_at: string
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
  window_start_at: string
  window_end_at: string
  series: StatsFeedTimeSeriesSeries[]
}

export interface StatsActivityHeatmapDayRow {
  day: string
  counts: number[]
}

export interface StatsActivityHeatmapResponse {
  generated_at: string
  window_days: number
  window_start_at: string
  window_end_at: string
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
  window_start_at: string
  window_end_at: string
  total: number
  max_count: number
  axes: StatsSignalRadarAxisPoint[]
}
