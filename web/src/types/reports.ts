import type { ArticleExportFilters, ArticleExportOptionEntry, ArticleExportPreviewItem } from './exports'

export type ReportStatus = 'queued' | 'running' | 'ready' | 'error' | 'skipped'
export type ReportTone = 'analytical' | 'concise' | 'executive' | 'technical'
export type ReportDetailLevel = 'brief' | 'standard' | 'detailed'
export type ReportDeliveryMode = 'link' | 'summary' | 'full'

export interface ReportPromptConfig {
  audience: string
  objective: string
  tone: ReportTone
  detail_level: ReportDetailLevel
  use_company_context: boolean
  custom_instructions: string | null
  focus_topics: string[]
  excluded_topics: string[]
}

export interface ReportSectionConfig {
  key: string
  title: string
  enabled: boolean
  instructions?: string | null
}

export interface ReportCapabilities {
  reporting_enabled: boolean
  ai_configured: boolean
  feeds: ArticleExportOptionEntry[]
  tags: ArticleExportOptionEntry[]
  classifications: string[]
  max_sources: number
  preview_limit: number
  context_window_tokens: number
  reserved_output_tokens: number
  source_token_cap: number
  max_model_calls: number
  safety_percent: number
}

export interface ReportContextEstimate {
  context_window_tokens: number
  reserved_output_tokens: number
  safety_margin_tokens: number
  usable_input_tokens: number
  estimated_source_tokens: number
  estimated_fixed_prompt_tokens: number
  estimated_peak_input_tokens?: number
  estimated_batches: number
  estimated_model_calls: number
  selected_source_count: number
  omitted_source_count: number
  coverage_percent: number
  warnings: string[]
}

export interface ReportPreviewItem extends ArticleExportPreviewItem {
  estimated_tokens: number
  selected: boolean
  exclusion_reason: string | null
}

export interface ReportPreview {
  total_matches: number
  articles_with_text: number
  items_with_iocs: number
  items: ReportPreviewItem[]
  estimate: ReportContextEstimate
}

export interface ReportTemplate {
  id: string
  owner_user_id: string | null
  builtin_key: string | null
  name: string
  description: string
  report_type: string
  visibility: 'private' | 'shared'
  prompt: ReportPromptConfig
  sections: ReportSectionConfig[]
  default_filters: ArticleExportFilters
  created_at: string
  updated_at: string
}

export interface ReportListItem {
  id: string
  template_id: string | null
  schedule_id: string | null
  owner_user_id: string | null
  title: string
  report_type: string
  status: ReportStatus
  trigger_source: 'manual' | 'scheduled' | 'retry'
  generation_stage: string
  period_start: string
  period_end: string
  source_count: number
  included_source_count: number
  model_calls: number
  provider: string | null
  model: string | null
  error_code: string | null
  error: string | null
  generated_at: string | null
  created_at: string
}

export interface ReportSection {
  key: string
  title: string
  position: number
  status: string
  body_markdown: string
  key_points: string[]
  citations: string[]
  error: string | null
}

export interface ReportSource {
  citation_key: string
  item_id: string | null
  included: boolean
  rank: number
  exclusion_reason: string | null
  title: string
  feed_name: string
  url: string
  classification: string | null
  relevance_score: number | null
  relevance_label: string | null
  published_at: string | null
  first_seen_at: string
  tags: string[]
  iocs: Array<Record<string, unknown>>
  estimated_tokens: number
}

export interface ReportDetail extends ReportListItem {
  filters: ArticleExportFilters
  prompt: ReportPromptConfig
  sections_config: ReportSectionConfig[]
  metrics: Record<string, unknown>
  coverage: Record<string, unknown>
  summary_text: string | null
  estimated_input_tokens: number
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  context_window_tokens: number
  generation_batches: number
  delivery_requested: boolean
  delivery_mode: ReportDeliveryMode
  sections: ReportSection[]
  sources: ReportSource[]
}

export interface ReportSchedule {
  id: string
  owner_user_id: string | null
  template_id: string
  name: string
  enabled: boolean
  cadence: 'weekly' | 'monthly'
  day_of_week: number
  day_of_month: number
  hour: number
  minute: number
  timezone: string
  window_type: 'previous_complete_week' | 'rolling_days' | 'previous_complete_month'
  rolling_days: number
  filters: ArticleExportFilters
  custom_instructions: string | null
  delivery_enabled: boolean
  delivery_mode: ReportDeliveryMode
  skip_empty: boolean
  missed_run_policy: 'latest' | 'skip' | 'all'
  next_run_at: string | null
  last_run_at: string | null
  failure_state?: 'healthy' | 'retrying' | 'exhausted' | 'quarantined'
  failure_count?: number
  consecutive_failure_count?: number
  last_error_code?: string | null
  last_error?: string | null
  last_error_at?: string | null
  retry_at?: string | null
  created_at: string
  updated_at: string
}

export type ReportScheduleWrite = Omit<
  ReportSchedule,
  | 'id'
  | 'owner_user_id'
  | 'next_run_at'
  | 'last_run_at'
  | 'failure_state'
  | 'failure_count'
  | 'consecutive_failure_count'
  | 'last_error_code'
  | 'last_error'
  | 'last_error_at'
  | 'retry_at'
  | 'created_at'
  | 'updated_at'
>

export interface ReportQueueResponse {
  report_id: string
  task_run_id: string
  celery_task_id: string | null
  status: string
}
