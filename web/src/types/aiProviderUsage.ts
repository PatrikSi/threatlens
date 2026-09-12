export interface AIProviderUsageRow {
  provider_id: string | null
  provider_version: number | null
  provider_name: string
  model: string
  total_requests: number
  successful_requests: number
  failed_requests: number
  success_rate_pct: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  unknown_token_requests: number
  average_latency_ms: number | null
  p95_latency_ms: number | null
  not_sent_requests: number
  ambiguous_requests: number
  deadline_failures: number
  failure_categories: Record<string, number>
  last_request_at: string
}

export interface AIProviderUsageResponse {
  items: AIProviderUsageRow[]
  total: number
  days: number
  limit: number
  offset: number
}
