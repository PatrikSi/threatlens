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
  has_unreadable_url: boolean
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
  export_type: 'sanitized' | 'backup'
  includes_sensitive_urls: boolean
  feeds: FeedImportEntry[]
  warnings: string[]
}

export interface FeedImportResponse {
  created: number
  updated: number
  skipped: number
  errors: string[]
}
