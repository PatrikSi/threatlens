import AxeBuilder from '@axe-core/playwright'
import type { StatsActivityHeatmapResponse, StatsOverviewResponse } from '../src/types/api'
import { test, expect } from './fixtures'

test.use({ timezoneId: 'America/Los_Angeles' })

test('inspects activity buckets and exact values with the keyboard on desktop and mobile', async ({ page }, info) => {
  const window = { generated_at: '2026-09-12T12:00:00Z', window_days: 30, window_start_at: '2026-08-14T00:00:00Z', window_end_at: '2026-09-13T00:00:00Z' }
  const overview: StatsOverviewResponse = {
    ...window, totals: { feeds_total: 1, feeds_enabled: 1, feeds_disabled: 0, items_total: 12, items_new: 0, items_content_fetched: 12, items_error: 0, articles_total: 12 },
    activity: { items_last_24h: 1, items_last_7d: 7, items_last_30d: 12 },
    derived: { extraction_success_rate_pct: 100, error_rate_pct: 0, avg_items_per_day_window: 0.4 },
    status_breakdown: [], daily_volume: [], feed_breakdown: [], top_domains: [],
  }
  const hourly: StatsActivityHeatmapResponse = {
    ...window, bucket_unit: 'hour', bucket_labels: Array.from({ length: 24 }, (_, index) => `${String(index).padStart(2, '0')}:00`),
    max_count: 23, rows: Array.from({ length: 12 }, (_, day) => ({ day: `2026-09-${String(day + 1).padStart(2, '0')}`, counts: Array.from({ length: 24 }, (_, hour) => hour) })),
  }
  await page.route('**/api/v1/stats/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/overview')) return route.fulfill({ json: overview })
    if (path.endsWith('/activity-heatmap')) return route.fulfill({ json: hourly })
    if (path.endsWith('/feed-timeseries')) return route.fulfill({ json: { ...window, series: [] } })
    if (path.endsWith('/signal-radar')) return route.fulfill({ json: { ...window, axes: [], total: 0, max_count: 0 } })
    throw new Error(`Unexpected statistics path: ${path}`)
  })
  await page.goto('/stats?section=ingestion')
  const chart = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Activity Heatmap', exact: true }) })
  const slider = chart.getByRole('slider', { name: 'Inspect activity bucket', exact: true })
  await slider.focus()
  await page.keyboard.press('End')
  await expect(slider).toHaveAttribute('aria-valuetext', '2026-09-12 23:00 UTC: 23 posts')
  await page.keyboard.press('ArrowLeft')
  await expect(slider).toHaveAttribute('aria-valuetext', '2026-09-12 22:00 UTC: 22 posts')
  await page.keyboard.press('Home')
  await expect(slider).toHaveAttribute('aria-valuetext', '2026-09-01 00:00 UTC: 0 posts')
  await chart.getByText('Show exact activity counts', { exact: true }).focus()
  await page.keyboard.press('Enter')
  const table = chart.getByRole('table')
  await expect(table.getByRole('row')).toHaveCount(11)
  await chart.getByRole('button', { name: 'Next days', exact: true }).click()
  await expect(table.getByRole('row')).toHaveCount(3)
  await expect(table).toContainText('Days 11–12 of 12')
  const accessibility = await new AxeBuilder({ page }).include('section:has(input[type="range"])').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  await info.attach('axe-ingestion-heatmap', { body: JSON.stringify(accessibility, null, 2), contentType: 'application/json' })
  expect(accessibility.violations).toEqual([])
  await page.setViewportSize({ width: 390, height: 844 })
  const region = chart.getByRole('region', { name: 'Exact activity counts', exact: true })
  await region.focus()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => region.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= globalThis.innerWidth)).toBe(true)
})
