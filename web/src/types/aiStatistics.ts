export interface AIFeatureStatistics {
  feature: string
  requests: number
  successful: number
  failed: number
  known_usage_requests: number
  unknown_usage_requests: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  not_sent: number
  ambiguous: number
  deadline_failures: number
  timeout_failures: number
  truncated_outputs: number
  budget_rejections: number
  latency_samples: number
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  p99_latency_ms: number | null
}

export interface AIStatisticsResponse {
  since: string
  until: string
  days: number
  features: AIFeatureStatistics[]
  queues: Array<{ feature: string; queued: number; running: number; oldest_queued_at: string | null; oldest_running_at: string | null }>
  provider_retry_attempts: number
  recovered_pre_io_failures: number
  latency_histogram: Record<string, number>
}
