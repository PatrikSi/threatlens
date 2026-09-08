import { describe, expect, it } from 'vitest'

import type {
  OperationsHealthHistoryResponse,
  OperationsIssue,
  OperationsOverviewResponse,
} from '../types/operations'
import {
  buildOperationsSignals,
  readOperationsView,
  signalKeyForIssue,
  suppressLegacyRecoveryHealth,
} from './operationsHealthPresentation'

const overview: OperationsOverviewResponse = {
  generated_at: '2026-09-01T12:00:00Z',
  overall_status: 'degraded',
  application: {
    version: '1.10.0',
    schema_revision: '0082_audit_identity_snapshots',
    expected_schema_revision: '0083_system_health_history',
    schema_current: false,
  },
  components: [],
  storage: [
    {
      key: 'database',
      label: 'Database storage',
      status: 'unknown',
      used_bytes: null,
      total_bytes: null,
      available_bytes: null,
      percent_used: null,
    },
    {
      key: 'application_filesystem',
      label: 'Application filesystem',
      status: 'critical',
      used_bytes: 95,
      total_bytes: 100,
      available_bytes: 5,
      percent_used: 95,
    },
  ],
  backlogs: [],
  recovery: {
    latest_backup: null,
    latest_verify: null,
    latest_restore_drill: null,
    latest_restore: null,
  },
  issues: [],
}

describe('operations health presentation', () => {
  it('represents application and schema compatibility as a selectable signal', () => {
    const signal = buildOperationsSignals(overview).find(
      (candidate) => candidate.key === 'application',
    )

    expect(signal).toMatchObject({
      label: 'Application and schema',
      status: 'critical',
    })
    expect(signal?.metrics).toEqual(expect.arrayContaining([
      { label: 'Application version', value: '1.10.0' },
      { label: 'Expected schema', value: '0083_system_health_history' },
    ]))
  })

  it.each([
    ['database_storage_unavailable', 'storage:database'],
    ['filesystem_capacity_low', 'storage:application_filesystem'],
    ['packaged_schema_revision_unavailable', 'application'],
  ])('routes %s findings to the relevant detail', (code, expectedKey) => {
    const issue: OperationsIssue = {
      code,
      severity: 'warning',
      component: code.startsWith('packaged_') ? 'application' : 'storage',
      summary: 'Finding',
      effect: 'Impact',
      recommended_action: 'Action',
    }

    expect(signalKeyForIssue(issue, buildOperationsSignals(overview))).toBe(expectedKey)
  })

  it('uses Activity as the canonical view while accepting legacy recovery URLs', () => {
    expect(readOperationsView('activity')).toBe('activity')
    expect(readOperationsView('recovery')).toBe('activity')
  })

  it('removes recovery-only legacy findings and their derived degraded status', () => {
    const history = healthHistoryFixture()
    const result = suppressLegacyRecoveryHealth(history)

    expect(result.samples[0]).toMatchObject({
      overall_status: 'healthy',
      issue_codes: [],
      critical_issue_count: 0,
      warning_issue_count: 0,
    })
    expect(result.samples[1]).toMatchObject({
      overall_status: 'degraded',
      issue_codes: ['reports_stale'],
      critical_issue_count: 0,
      warning_issue_count: 1,
    })
    expect(result.samples[2]).toMatchObject({
      overall_status: 'degraded',
      issue_codes: ['reports_stale'],
      critical_issue_count: 0,
      warning_issue_count: 1,
    })
  })
})

function healthHistoryFixture(): OperationsHealthHistoryResponse {
  const sample = {
    sampled_at: '2026-09-01T12:00:00Z',
    overall_status: 'degraded' as const,
    component_statuses: { database: 'healthy' as const },
    worker_status: 'healthy' as const,
    worker_reason: 'healthy' as const,
    responding_worker_count: 1,
    observed_worker_count: 1,
    worker_inventory_truncated: false,
    total_capacity: 1,
    active_count: 0,
    reserved_count: 0,
    scheduled_count: 0,
    missing_queues: [],
    stale_execution_queues: [],
    backlog_pending_count: 0,
    backlog_stale_count: 0,
    critical_issue_count: 0,
    warning_issue_count: 1,
    issue_codes: ['backup_not_recorded'],
  }
  return {
    generated_at: '2026-09-01T12:10:00Z',
    window: '24h',
    sample_interval_seconds: 300,
    effective_resolution_seconds: 300,
    downsampling_strategy: 'none',
    retention_days: 30,
    coverage: {
      requested_start: '2026-08-31T12:00:00Z',
      requested_end: '2026-09-01T12:10:00Z',
      first_sample_at: sample.sampled_at,
      last_sample_at: '2026-09-01T12:10:00Z',
      expected_sample_count: 3,
      actual_sample_count: 3,
      returned_sample_count: 3,
      missing_sample_count: 0,
      coverage_percent: 100,
      gap_count: 0,
      largest_gap_seconds: null,
      collection_stale: false,
      source_anomaly_sample_count: 3,
      returned_anomaly_sample_count: 3,
      source_transition_count: 2,
      returned_transition_count: 2,
      anomaly_evidence_truncated: false,
      transition_evidence_truncated: false,
      gap_intervals: [],
      returned_gap_interval_count: 0,
      gap_intervals_truncated: false,
    },
    samples: [
      sample,
      {
        ...sample,
        sampled_at: '2026-09-01T12:05:00Z',
        issue_codes: ['restore_drill_not_recorded', 'reports_stale'],
        warning_issue_count: 2,
      },
      {
        ...sample,
        sampled_at: '2026-09-01T12:10:00Z',
        overall_status: 'critical',
        issue_codes: ['latest_backup_failed', 'reports_stale'],
        critical_issue_count: 1,
        warning_issue_count: 1,
      },
    ],
  }
}
