import { describe, expect, it } from 'vitest'
import type { AITimeSeriesPointResponse } from '../types/ai'
import { measuredLatency, recordedTokens, successRate, trendDate, trendPath } from './aiTrendModel'

const point: AITimeSeriesPointResponse = {
  bucket: '2026-09-13', requests: 2, failures: 1, total_tokens: 0,
  average_latency_ms: 0, p95_latency_ms: 0, daily_brief_successes: 0,
  daily_brief_failures: 0, daily_brief_skips: 0,
}

describe('AI trend measurement semantics', () => {
  it('distinguishes unreported and measured zero tokens, including older API responses', () => {
    expect(recordedTokens(point)).toBeNull()
    expect(recordedTokens({ ...point, known_usage_requests: 0 })).toBeNull()
    expect(recordedTokens({ ...point, known_usage_requests: 1 })).toBe(0)
    expect(recordedTokens({ ...point, total_tokens: 123 })).toBe(123)
    expect(recordedTokens({ ...point, requests: 0 })).toBe(0)
  })

  it('does not invent latency samples or perfect success on idle days', () => {
    expect(measuredLatency(point, 'average_latency_ms')).toBeNull()
    expect(measuredLatency({ ...point, latency_samples: 0 }, 'p95_latency_ms')).toBeNull()
    expect(measuredLatency({ ...point, latency_samples: 1 }, 'average_latency_ms')).toBe(0)
    expect(successRate(point)).toBe(50)
    expect(successRate({ ...point, requests: 0, failures: 0 })).toBeNull()
    expect(successRate({ ...point, failures: 0 })).toBe(100)
    expect(successRate({ ...point, failures: 2 })).toBe(0)
  })

  it('breaks lines at missing or nonfinite values and preserves recorded zeroes', () => {
    expect(trendPath([0, 5, null, 3, NaN, Infinity, -1, 0], (index) => index, (value) => value))
      .toBe('M0,0 L1,5 M3,3 M7,0')
    expect(trendPath([null, null], (index) => index, (value) => value)).toBe('')
  })

  it('labels UTC calendar dates without shifting them into the previous local day', () => {
    const value = trendDate('2026-01-01', true)
    expect(value).toContain('2026')
    expect(value).not.toContain('2025')
    expect(trendDate('not-a-date')).toBe('not-a-date')
  })
})
