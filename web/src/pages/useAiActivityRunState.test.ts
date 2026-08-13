import { describe, expect, it } from 'vitest'

import { buildRunHistoryMetrics } from './useAiActivityRunState'

describe('buildRunHistoryMetrics', () => {
  it('keeps the previous page range visible while the requested page loads', () => {
    expect(
      buildRunHistoryMetrics({
        runPage: 2,
        runTotal: 75,
        dataOffset: 25,
        runCount: 25,
        hasData: true,
        isFetching: true,
        isLoading: false,
        isPlaceholderData: true,
      }),
    ).toEqual({
      runTotal: 75,
      visibleRunOffset: 25,
      runCount: 25,
      totalPages: 4,
      isLoading: false,
      isPageLoading: true,
      isRefreshing: false,
      statusMessage: 'Loading page 3...',
    })
  })

  it('distinguishes initial loading from a background refresh', () => {
    const initial = buildRunHistoryMetrics({
      runPage: 0,
      runTotal: 0,
      dataOffset: undefined,
      runCount: 0,
      hasData: false,
      isFetching: true,
      isLoading: true,
      isPlaceholderData: false,
    })
    const refresh = buildRunHistoryMetrics({
      ...initial,
      runPage: 0,
      runTotal: 3,
      dataOffset: 0,
      runCount: 3,
      hasData: true,
      isFetching: true,
      isLoading: false,
      isPlaceholderData: false,
    })

    expect(initial.statusMessage).toBe('Loading AI run history...')
    expect(initial.totalPages).toBe(1)
    expect(refresh.isRefreshing).toBe(true)
    expect(refresh.statusMessage).toBe('Refreshing run history...')
  })
})
