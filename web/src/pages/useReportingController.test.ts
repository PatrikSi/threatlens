import { afterEach, describe, expect, it } from 'vitest'

import {
  beginPendingReportingRequest,
  reportMutationRequestKey,
  reportingRequestScope,
  resetPendingReportingKeys,
  serializeReportingWrite,
  settlePendingReportingRequest,
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

    const first = beginPendingReportingRequest(scope)
    settlePendingReportingRequest(scope, first, 'ambiguous')
    const afterRemount = beginPendingReportingRequest(scope)

    expect(afterRemount).toBe(first)
    settlePendingReportingRequest(scope, afterRemount, 'confirmed')
    expect(beginPendingReportingRequest(scope)).not.toBe(first)
  })

  it('does not clear a newer key from a stale completion', () => {
    const scope = reportingRequestScope('analyst-1', 'report:create', '{}')
    const first = beginPendingReportingRequest(scope)
    settlePendingReportingRequest(scope, first, 'rejected')
    const second = beginPendingReportingRequest(scope)

    settlePendingReportingRequest(scope, first, 'confirmed')

    expect(beginPendingReportingRequest(scope)).toBe(second)
  })

  it('retains a shared key when overlapping callers settle asymmetrically', () => {
    const scope = reportingRequestScope('analyst-1', 'report:retry', 'report-2')
    const first = beginPendingReportingRequest(scope)
    const overlapping = beginPendingReportingRequest(scope)

    settlePendingReportingRequest(scope, first, 'confirmed')
    settlePendingReportingRequest(scope, overlapping, 'ambiguous')
    const retry = beginPendingReportingRequest(scope)

    expect(overlapping).toBe(first)
    expect(retry).toBe(first)
    settlePendingReportingRequest(scope, retry, 'confirmed')
    expect(beginPendingReportingRequest(scope)).not.toBe(first)
  })
})

describe('serialized reporting writes', () => {
  it('coalesces exact duplicates and runs changed payloads in submission order', async () => {
    const started: string[] = []
    let releaseFirst: ((value: string) => void) | undefined
    const first = serializeReportingWrite(
      'schedule-1',
      'schedule-1-enabled',
      () => {
        started.push('enabled')
        return new Promise((resolve) => { releaseFirst = resolve })
      },
    )
    const duplicate = serializeReportingWrite(
      'schedule-1',
      'schedule-1-enabled',
      () => Promise.resolve('duplicate'),
    )
    const second = serializeReportingWrite(
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
    const first = serializeReportingWrite(
      'template-1',
      'first',
      () => Promise.reject(new Error('first failed')),
    )
    const second = serializeReportingWrite(
      'template-1',
      'second',
      () => Promise.resolve('saved'),
    )

    await expect(first).rejects.toThrow('first failed')
    await expect(second).resolves.toBe('saved')
  })

  it('preserves the final intent for an A then B then A submission sequence', async () => {
    const started: string[] = []
    let releaseFirst: (() => void) | undefined
    let releaseSecond: (() => void) | undefined
    const first = serializeReportingWrite('template-2', 'A', () => {
      started.push('A1')
      return new Promise<string>((resolve) => {
        releaseFirst = () => resolve('A1')
      })
    })
    const second = serializeReportingWrite('template-2', 'B', () => {
      started.push('B')
      return new Promise<string>((resolve) => {
        releaseSecond = () => resolve('B')
      })
    })
    const final = serializeReportingWrite('template-2', 'A', () => {
      started.push('A2')
      return Promise.resolve('A2')
    })

    await Promise.resolve()
    expect(started).toEqual(['A1'])
    releaseFirst?.()
    await expect(first).resolves.toBe('A1')
    await Promise.resolve()
    expect(started).toEqual(['A1', 'B'])
    releaseSecond?.()
    await expect(second).resolves.toBe('B')
    await expect(final).resolves.toBe('A2')
    expect(started).toEqual(['A1', 'B', 'A2'])
  })
})
