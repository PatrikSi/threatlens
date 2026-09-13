import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'

import { createRequestFromDraft, DEFAULT_DRAFT } from '../src/pages/aiSettingsDraft'
import type { AIOpsOverviewResponse, AITimeSeriesPointResponse } from '../src/types/ai'
import type { AIStatisticsResponse } from '../src/types/aiStatistics'
import { emptyOverview } from './ai-overview-fixture'
import { test, expect } from './fixtures'

test.use({ timezoneId: 'America/Los_Angeles' })

const chartNames = ['Request outcomes', 'Recorded token usage', 'Request latency', 'Success rate']
const until = '2026-09-13T12:00:00Z'

function overviewFixture(days: number): AIOpsOverviewResponse {
  const since = new Date(Date.parse(until) - days * 86_400_000).toISOString()
  const firstDay = Date.parse(`${since.slice(0, 10)}T00:00:00Z`)
  const timeSeries: AITimeSeriesPointResponse[] = Array.from({ length: days + 1 }, (_, index) => {
    const bucket = new Date(firstDay + index * 86_400_000).toISOString().slice(0, 10)
    const point: AITimeSeriesPointResponse = {
      bucket, requests: 0, failures: 0, total_tokens: 0, average_latency_ms: 0,
      p95_latency_ms: 0, latency_samples: 0, known_usage_requests: 0,
      daily_brief_successes: 0, daily_brief_failures: 0, daily_brief_skips: 0,
    }
    if (bucket === '2026-09-10') Object.assign(point, {
      requests: 5, total_tokens: 4_000, average_latency_ms: 400, p95_latency_ms: 600,
      latency_samples: 5, known_usage_requests: 5,
    })
    if (bucket === '2026-09-12') Object.assign(point, { requests: 2, failures: 1 })
    if (bucket === '2026-09-13') Object.assign(point, {
      requests: 8, failures: 2, total_tokens: 12_000, average_latency_ms: 800,
      p95_latency_ms: 1_500, latency_samples: 6, known_usage_requests: 6,
    })
    return point
  })
  return {
    ...emptyOverview, since, until, bucket_unit: 'day', bucket_timezone: 'UTC',
    kpis: { ...emptyOverview.kpis, total_requests: 15, success_rate_pct: 80, total_tokens: 16_000 },
    time_series: timeSeries,
  }
}

async function statisticsRoutes(page: Page) {
  const state = { overviewStatus: 200, requestedWindows: [] as number[] }
  await page.route('**/api/v1/ai/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    const days = Number(url.searchParams.get('days') ?? 30)
    if (path === '/ai/settings') return route.fulfill({ json: {
      ...createRequestFromDraft(DEFAULT_DRAFT), id: 'settings-1', ai_enabled: true,
      ai_configured: true, api_key_configured: false, provider_routing_supported: true,
      effective_feature_configured: { item_enrichment: true, daily_brief: true, report: true },
      prompt_previews: {
        item_enrichment: { label: 'Enrichment', system_prompt: '', notes: [] },
        daily_brief: { label: 'Brief', system_prompt: '', notes: [] },
      },
      created_at: until, updated_at: until,
    } })
    if (path === '/ai/ops/overview') {
      state.requestedWindows.push(days)
      return route.fulfill({
        status: state.overviewStatus,
        json: state.overviewStatus === 200 ? overviewFixture(days) : {
          detail: state.overviewStatus === 403 ? 'AI statistics access was revoked' : 'AI trends temporarily unavailable',
        },
      })
    }
    if (path === '/ai/ops/providers') return route.fulfill({
      json: { items: [], days, offset: 0, limit: 25, total: 0 },
    })
    if (path === '/ai/ops/statistics') {
      const response: AIStatisticsResponse = {
        since: overviewFixture(days).since!, until, days, features: [], queues: [],
        provider_retry_attempts: 0, recovered_pre_io_failures: 0, latency_histogram: {},
      }
      return route.fulfill({ json: response })
    }
    throw new Error(`Unexpected AI statistics fixture request: ${path}`)
  })
  return state
}

test('inspects AI trends with the keyboard, preserves measurement gaps, and fits mobile screens', async ({ page, api }, info) => {
  api.identity = { ...api.identity, role: 'admin', features: { ...api.identity.features, ai_enabled: true, ai_configured: true } }
  const state = await statisticsRoutes(page)
  await page.goto('/stats?section=ai')
  const trends = page.getByRole('region', { name: 'AI trends', exact: true })
  await expect(trends).toBeVisible()
  for (const name of chartNames) await expect(trends.getByRole('img', { name, exact: true })).toBeVisible()

  const date = trends.getByRole('slider', { name: 'Trend date', exact: true })
  await date.focus()
  await page.keyboard.press('End')
  await expect(date).toHaveValue('30')
  await expect(date).toHaveAttribute('aria-valuetext', /13.*UTC/)
  await expect(trends.getByRole('figure', { name: 'Success rate', exact: true })).toContainText('75')
  await page.keyboard.press('ArrowLeft')
  await expect(date).toHaveValue('29')
  await expect(date).toHaveAttribute('aria-valuetext', /12.*UTC/)
  await expect(trends.getByRole('figure', { name: 'Request latency', exact: true })).toContainText(/unavailable|unmeasured/i)
  await page.keyboard.press('ArrowLeft')
  await expect(date).toHaveValue('28')
  await expect(trends.getByRole('figure', { name: 'Success rate', exact: true })).toContainText(/unavailable|no requests/i)
  await page.keyboard.press('Home')
  await expect(date).toHaveValue('0')
  await page.keyboard.press('End')
  await expect(date).toHaveValue('30')
  await expect(date).toBeFocused()

  await page.getByLabel('Overview time window', { exact: true }).selectOption('7')
  await expect(date).toHaveAttribute('max', '7')
  expect(state.requestedWindows).toContain(7)
  await trends.getByText('View exact trend data', { exact: true }).focus()
  await page.keyboard.press('Enter')
  const data = trends.getByRole('region', { name: 'AI trend data', exact: true })
  await expect(data).toBeVisible()
  await expect(data.getByRole('row')).toHaveCount(9)
  await expect(data).toContainText('UTC')
  await expect(data.getByRole('cell', { name: '12,000', exact: true })).toBeVisible()
  expect(await data.locator('th').evaluateAll((headers) => headers.every((header) => ['col', 'row'].includes(header.getAttribute('scope') ?? '')))).toBe(true)
  expect(await trends.locator('svg').evaluateAll((charts) => charts.every((chart) => !/NaN|Infinity/.test(chart.outerHTML)))).toBe(true)

  const accessibility = await new AxeBuilder({ page }).include('[aria-label="AI trends"]')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  await info.attach('axe-ai-trends', { body: JSON.stringify(accessibility, null, 2), contentType: 'application/json' })
  expect(accessibility.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([])
  await trends.screenshot({ path: info.outputPath('ai-statistics-trends-desktop.png') })

  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await data.focus()
  await expect(data).toBeFocused()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => data.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0)
  await trends.screenshot({ path: info.outputPath('ai-statistics-trends-mobile.png') })
})

test('keeps AI graphs through transient refresh failures and hides them after access is revoked', async ({ page, api }) => {
  api.identity = { ...api.identity, role: 'admin', features: { ...api.identity.features, ai_enabled: true, ai_configured: true } }
  const state = await statisticsRoutes(page)
  await page.goto('/stats?section=ai')
  const trends = page.getByRole('region', { name: 'AI trends', exact: true })
  await expect(trends).toBeVisible()
  state.overviewStatus = 503
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await page.clock.runFor(8_000)
  await expect(page.getByRole('alert').filter({ hasText: 'Showing previously loaded statistics' })).toBeVisible()
  for (const name of chartNames) await expect(trends.getByRole('img', { name, exact: true })).toBeVisible()

  state.overviewStatus = 403
  await page.getByRole('button', { name: 'Retry refresh', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'AI statistics access was revoked' })).toBeVisible()
  await expect(trends).toBeHidden()
  await expect(page.getByRole('button', { name: 'Retry AI statistics', exact: true })).toBeVisible()

  state.overviewStatus = 200
  await page.getByRole('button', { name: 'Retry AI statistics', exact: true }).click()
  await expect(trends).toBeVisible()
})
