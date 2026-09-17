// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import type { StatsActivityHeatmapResponse } from '../types/api'
import { ActivityHeatmapPanel } from './ActivityHeatmapPanel'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root | undefined
let host: HTMLDivElement
afterEach(() => { act(() => root?.unmount()); root = undefined; host?.remove() })
function render(data: StatsActivityHeatmapResponse) {
  if (!root) { host = document.createElement('div'); document.body.append(host); root = createRoot(host) }
  act(() => root!.render(<ActivityHeatmapPanel data={data} />))
  return host
}
const daily: StatsActivityHeatmapResponse = {
  generated_at: '2026-09-12T00:00:00Z', window_start_at: '2026-08-13T00:00:00Z', window_end_at: '2026-09-12T00:00:00Z',
  window_days: 30, bucket_unit: 'day', bucket_labels: ['Day'], max_count: 11,
  rows: Array.from({ length: 12 }, (_, index) => ({ day: `2026-09-${String(index + 1).padStart(2, '0')}`, counts: [index] })),
}

describe('activity heatmap accessible values', () => {
  it('offers one keyboard selector and a bounded exact-values table including zero counts', () => {
    const view = render(daily)
    const slider = view.querySelector('input')!
    expect(view.querySelector(`label[for="${slider.id}"]`)?.textContent).toBe('Inspect activity bucket')
    expect(slider.getAttribute('aria-valuetext')).toBe('2026-09-01 UTC: 0 posts')
    expect(view.querySelectorAll('input')).toHaveLength(1)
    expect(view.querySelectorAll('tbody tr')).toHaveLength(10)
    expect(view.querySelectorAll('[tabindex]')).toHaveLength(1)
    const next = [...view.querySelectorAll('button')].find((button) => button.textContent === 'Next days')!
    act(() => next.click())
    expect(view.querySelectorAll('tbody tr')).toHaveLength(2)
    expect(view.querySelector('caption')?.textContent).toContain('Days 11–12 of 12')
    expect(view.querySelector('tbody')?.textContent).toContain('2026-09-12')
  })

  it('exposes UTC hour labels and clamps selection and table pages after the scope shrinks', () => {
    const hourly = { ...daily, bucket_unit: 'hour' as const, bucket_labels: ['00:00', '01:00'], rows: daily.rows.map((row) => ({ ...row, counts: [2, 7] })) }
    const view = render(hourly)
    act(() => [...view.querySelectorAll('button')].find((button) => button.textContent === 'Next days')!.click())
    const slider = view.querySelector('input')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(slider, '23')
      slider.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(slider.getAttribute('aria-valuetext')).toBe('2026-09-12 01:00 UTC: 7 posts')
    render({ ...hourly, rows: hourly.rows.slice(0, 1) })
    expect(slider.value).toBe('1')
    expect(slider.getAttribute('aria-valuetext')).toBe('2026-09-01 01:00 UTC: 7 posts')
    expect(view.querySelector('caption')?.textContent).toContain('Days 1–1 of 1')
  })

  it('shows an explicit empty state without an unusable selector', () => {
    const view = render({ ...daily, rows: [] })
    expect(view.textContent).toContain('No activity buckets')
    expect(view.querySelector('input')).toBeNull()
  })
})
