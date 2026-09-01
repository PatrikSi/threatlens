import { describe, expect, it } from 'vitest'

import type {
  OperationsIssue,
  OperationsOverviewResponse,
} from '../types/operations'
import {
  buildOperationsSignals,
  signalKeyForIssue,
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
})
