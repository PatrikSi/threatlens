// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import type { AITimeSeriesPointResponse } from '../types/ai'
import { emptyOverview } from '../../browser/ai-overview-fixture'
import { AiStatisticsTrends } from './AiStatisticsTrends'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root | undefined
let host: HTMLDivElement | undefined
const point: AITimeSeriesPointResponse = {
  bucket: '2026-09-13', requests: 2, failures: 1, total_tokens: 100,
  average_latency_ms: 5, p95_latency_ms: 8, latency_samples: 1, known_usage_requests: 1,
  daily_brief_successes: 0, daily_brief_failures: 0, daily_brief_skips: 0,
}
function render(points: AITimeSeriesPointResponse[]) {
  if (!host) { host = document.createElement('div'); document.body.append(host); root = createRoot(host) }
  act(() => root!.render(<AiStatisticsTrends overview={{ ...emptyOverview, time_series: points }} />))
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = undefined; root = undefined })

describe('AI trend charts', () => {
  it('shows an explicit empty window and no fabricated graph activity', () => {
    render([])
    expect(host!.textContent).toContain('No provider requests recorded')
    expect(host!.querySelectorAll('svg[role="img"]')).toHaveLength(0)
    render([{ ...point, requests: 0, failures: 0, latency_samples: 0, known_usage_requests: 0, total_tokens: 0 }])
    expect(host!.querySelector('[role="status"]')).not.toBeNull()
    expect(host!.querySelectorAll('svg[role="img"]')).toHaveLength(0)
  })

  it('renders isolated points and accessible exact values for one date', () => {
    render([point])
    expect(host!.querySelectorAll('svg[role="img"]')).toHaveLength(4)
    expect(host!.querySelectorAll('[data-trend-point]')).toHaveLength(6)
    expect(host!.querySelector<HTMLInputElement>('input[type="range"]')!.disabled).toBe(true)
    expect(host!.textContent).toContain('1 with unreported token usage')
    expect(host!.textContent).toContain('50%')
    expect([...host!.querySelectorAll('th')].every((cell) => cell.hasAttribute('scope'))).toBe(true)
    const svg = host!.querySelector('svg[role="img"]')!
    const titleId = svg.getAttribute('aria-labelledby')!
    expect(host!.querySelector(`[id="${titleId}"]`)!.textContent).toBe('Request outcomes')
  })

  it('keeps missing measurements as gaps, without masking zero measurements', () => {
    render([
      { ...point, bucket: '2026-09-10', average_latency_ms: 0, p95_latency_ms: 0, total_tokens: 0 },
      { ...point, bucket: '2026-09-11', latency_samples: 0, known_usage_requests: 0 },
      { ...point, bucket: '2026-09-12' },
    ])
    const path = host!.querySelector('[data-trend-series="average"]')!.getAttribute('d')!
    expect(path.match(/M/g)).toHaveLength(2)
    expect(path).not.toContain('L')
    expect(path).not.toMatch(/NaN|Infinity/)
    expect(host!.querySelector('tbody')!.textContent).toContain('0 ms')
    expect(host!.querySelector('tbody')!.textContent).toContain('Unavailable')
  })

  it('does not infer latency measurement counts from an older response', () => {
    render([{ ...point, latency_samples: undefined, known_usage_requests: undefined }])
    expect(host!.textContent).toContain('Latency sample count unavailable')
    expect(host!.textContent).toContain('No latency measurements available')
    expect(host!.textContent).toContain('Token usage completeness unavailable')
    expect(host!.querySelector('[data-trend-series="tokens"]')).not.toBeNull()
  })

  it('bounds chart geometry across 91 days and updates the most recent date on refresh', () => {
    const points = Array.from({ length: 91 }, (_, index) => ({ ...point,
      bucket: new Date(Date.UTC(2026, 5, 15 + index)).toISOString().slice(0, 10),
    }))
    render(points)
    const slider = host!.querySelector<HTMLInputElement>('input[type="range"]')!
    expect(slider.max).toBe('90')
    expect(slider.value).toBe('90')
    for (const path of host!.querySelectorAll('[data-trend-series]')) expect(path.getAttribute('d')).not.toMatch(/NaN|Infinity/)
    render([...points, { ...point, bucket: '2026-09-14' }])
    expect(slider.value).toBe('91')
  })
})
