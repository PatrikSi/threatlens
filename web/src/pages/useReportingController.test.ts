import { describe, expect, it } from 'vitest'

import { reportMutationRequestKey } from './useReportingController'


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
