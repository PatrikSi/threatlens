import type { OperationsHealthHistoryResponse, OperationsHealthHistorySample } from '../types/operations'

export function operationsSampleFixture(patch: Partial<OperationsHealthHistorySample> = {}): OperationsHealthHistorySample {
  return {
    sampled_at: '2026-09-10T10:00:00Z', overall_status: 'healthy', component_statuses: {}, worker_status: 'healthy', worker_reason: 'healthy',
    responding_worker_count: 1, observed_worker_count: 1, worker_inventory_truncated: false,
    total_capacity: 4, active_count: 0, reserved_count: 0, scheduled_count: 0, missing_queues: [], stale_execution_queues: [],
    backlog_pending_count: 0, backlog_stale_count: 0, critical_issue_count: 0, warning_issue_count: 0, issue_codes: [],
    ...patch,
  }
}

export function operationsHistoryFixture(samples: OperationsHealthHistorySample[]): OperationsHealthHistoryResponse {
  return {
    generated_at: '2026-09-10T10:15:00Z', window: '1h', sample_interval_seconds: 300, effective_resolution_seconds: 300,
    downsampling_strategy: 'none', retention_days: 30, samples,
    coverage: {
      requested_start: '2026-09-10T09:15:00Z', requested_end: '2026-09-10T10:15:00Z',
      first_sample_at: samples[0]?.sampled_at ?? null, last_sample_at: samples.at(-1)?.sampled_at ?? null,
      expected_sample_count: 12, actual_sample_count: samples.length, returned_sample_count: samples.length,
      missing_sample_count: 12 - samples.length, coverage_percent: samples.length / 12 * 100,
      gap_count: 0, largest_gap_seconds: null, collection_stale: false,
      source_anomaly_sample_count: 0, returned_anomaly_sample_count: 0, source_transition_count: 0, returned_transition_count: 0,
      anomaly_evidence_truncated: false, transition_evidence_truncated: false,
      gap_intervals: [], returned_gap_interval_count: 0, gap_intervals_truncated: false,
    },
  }
}
