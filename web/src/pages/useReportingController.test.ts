import { afterEach, describe, expect, it } from 'vitest'

import {
  clearPendingReportingKey,
  getOrCreatePendingReportingKey,
  reportMutationRequestKey,
  reportingRequestScope,
  resetPendingReportingKeys,
  serializeCoalescedRequest,
} from './reportingRequestCoordinator'


afterEach(() => resetPendingReportingKeys())


describe('reportMutationRequestKey', () => {
  it('distinguishes concurrent schedule updates with different payloads', () => {
    const enabled = JSON.stringify({ id: 'schedule-1', enabled: true })
    const paused = JSON.stringify({ id: 'schedule-1', enabled: false })

    expect(reportMutationRequestKey('schedule-1', enabled)).not.toBe(
      reportMutationRequestKey('schedule-1', paused),
    )
    expect(reportMutationRequestKey('schedule-1', enabled)).toBe(
      reportMutationRequestKey('schedule-1', enabled),
    )
  })
})

describe('reporting request identities', () => {
  it('keeps an unresolved key across request-coordinator consumers', () => {
    const scope = reportingRequestScope('analyst-1', 'report:retry', 'report-1')

    const first = getOrCreatePendingReportingKey(scope)
    const afterRemount = getOrCreatePendingReportingKey(scope)

    expect(afterRemount).toBe(first)
    clearPendingReportingKey(scope, first)
    expect(getOrCreatePendingReportingKey(scope)).not.toBe(first)
  })

  it('does not clear a newer key from a stale completion', () => {
    const scope = reportingRequestScope('analyst-1', 'report:create', '{}')
    const first = getOrCreatePendingReportingKey(scope)
    clearPendingReportingKey(scope, first)
    const second = getOrCreatePendingReportingKey(scope)

    clearPendingReportingKey(scope, first)

    expect(getOrCreatePendingReportingKey(scope)).toBe(second)
  })
})

describe('serialized reporting writes', () => {
  it('coalesces exact duplicates and runs changed payloads in submission order', async () => {
    const requests = new Map<string, Promise<string>>()
    const tails = new Map<string, Promise<void>>()
    const started: string[] = []
    let releaseFirst: ((value: string) => void) | undefined
    const first = serializeCoalescedRequest(
      requests,
      tails,
      'schedule-1',
      'schedule-1-enabled',
      () => {
        started.push('enabled')
        return new Promise((resolve) => { releaseFirst = resolve })
      },
    )
    const duplicate = serializeCoalescedRequest(
      requests,
      tails,
      'schedule-1',
      'schedule-1-enabled',
      () => Promise.resolve('duplicate'),
    )
    const second = serializeCoalescedRequest(
      requests,
      tails,
      'schedule-1',
      'schedule-1-paused',
      () => {
        started.push('paused')
        return Promise.resolve('second')
      },
    )

    expect(duplicate).toBe(first)
    await Promise.resolve()
    expect(started).toEqual(['enabled'])

    releaseFirst?.('first')
    await expect(first).resolves.toBe('first')
    await expect(second).resolves.toBe('second')
    expect(started).toEqual(['enabled', 'paused'])
  })

  it('continues the ordered write stream after a failed request', async () => {
    const requests = new Map<string, Promise<string>>()
    const tails = new Map<string, Promise<void>>()
    const first = serializeCoalescedRequest(
      requests,
      tails,
      'template-1',
      'first',
      () => Promise.reject(new Error('first failed')),
    )
    const second = serializeCoalescedRequest(
      requests,
      tails,
      'template-1',
      'second',
      () => Promise.resolve('saved'),
    )

    await expect(first).rejects.toThrow('first failed')
    await expect(second).resolves.toBe('saved')
  })
})
