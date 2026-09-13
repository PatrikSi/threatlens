import AxeBuilder from '@axe-core/playwright'
import type { AIProviderUsageResponse, AIProviderUsageRow } from '../src/types/aiProviderUsage'
import type { AIStatisticsResponse } from '../src/types/aiStatistics'
import { createRequestFromDraft, DEFAULT_DRAFT } from '../src/pages/aiSettingsDraft'
import { emptyOverview } from './ai-overview-fixture'
import { test, expect } from './fixtures'

const usageRow = (index: number): AIProviderUsageRow => ({
  provider_id: `00000000-0000-0000-0000-${String(index + 1).padStart(12, '0')}`, provider_version: 2,
  provider_name: `Historical provider ${index + 1}`, model: 'shared-model', total_requests: 4,
  successful_requests: 2, failed_requests: 2, success_rate_pct: 50,
  prompt_tokens: 20, completion_tokens: 10, total_tokens: 30, unknown_token_requests: 2,
  average_latency_ms: 100, p95_latency_ms: 150, not_sent_requests: 1, ambiguous_requests: 1,
  deadline_failures: 1, failure_categories: { dns_deadline: 1, provider_hourly_token_budget: 1 },
  last_request_at: '2026-09-12T12:00:00Z',
})

test('paginates provider snapshots with the keyboard, resets the window, and keeps usage failures local', async ({ page, api }) => {
  api.identity = { ...api.identity, role: 'admin', features: { ...api.identity.features, ai_enabled: true, ai_configured: true } }
  const paths: string[] = []
  let failUsage = false
  await page.route('**/api/v1/ai/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    if (path === '/ai/settings') return route.fulfill({ json: {
      ...createRequestFromDraft(DEFAULT_DRAFT), id: 'settings-1', ai_enabled: true, ai_configured: true,
      api_key_configured: false, provider_routing_supported: true,
      prompt_previews: { item_enrichment: { label: 'Enrichment', system_prompt: '', notes: [] }, daily_brief: { label: 'Brief', system_prompt: '', notes: [] } },
      created_at: '2026-09-12T00:00:00Z', updated_at: '2026-09-12T00:00:00Z',
    } })
    if (path === '/ai/ops/overview') return route.fulfill({ json: emptyOverview })
    if (path === '/ai/ops/statistics') {
      const days = Number(url.searchParams.get('days'))
      const until = '2026-09-13T12:00:00Z'
      const response: AIStatisticsResponse = {
        since: new Date(Date.parse(until) - days * 86_400_000).toISOString(), until, days,
        features: [], queues: [], provider_retry_attempts: 0, recovered_pre_io_failures: 0, latency_histogram: {},
      }
      return route.fulfill({ json: response })
    }
    if (path === '/ai/ops/live') return route.fulfill({ json: emptyOverview.live })
    if (path === '/ai/ops/runs') return route.fulfill({ json: { items: [], total: 0, limit: 20, offset: 0 } })
    if (path === '/ai/ops/prompt-history' || path === '/ai/ops/manual-actions') return route.fulfill({ json: [] })
    if (path === '/ai/ops/providers') {
      paths.push(url.search)
      if (failUsage) return route.fulfill({ status: 503, json: { detail: 'Provider usage unavailable in synthetic fixture' } })
      const days = Number(url.searchParams.get('days'))
      const offset = Number(url.searchParams.get('offset'))
      const all = days === 7 ? [usageRow(90)] : Array.from({ length: 26 }, (_, index) => usageRow(index))
      const response: AIProviderUsageResponse = { items: all.slice(offset, offset + 25), days, offset, limit: 25, total: all.length }
      return route.fulfill({ json: response })
    }
    throw new Error(`Unexpected provider usage fixture request: ${path}`)
  })
  await page.goto('/settings/ai')
  await page.getByRole('link', { name: 'Open AI statistics', exact: true }).click()
  await expect(page).toHaveURL('/stats?section=ai')
  await expect(page.getByRole('heading', { name: 'AI statistics', exact: true })).toBeVisible()
  const records = page.getByRole('region', { name: 'Provider usage records' })
  await expect(records.getByRole('row')).toHaveCount(26)
  await expect(records.getByText('Historical provider 1', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Next provider page', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(records.getByText('Historical provider 26', { exact: true })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: '26–26 of 26 provider/model records' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Next provider page', exact: true })).toBeDisabled()
  await page.getByLabel('Overview time window').selectOption('7')
  await expect(records.getByText('Historical provider 91', { exact: true })).toBeVisible()
  expect(paths).toContain('?days=7&limit=25&offset=0')
  await expect(page.getByRole('button', { name: 'Previous provider page', exact: true })).toBeDisabled()
  const a11y = await new AxeBuilder({ page }).include('section:has(>div>[aria-busy])').analyze()
  expect(a11y.violations).toEqual([])
  failUsage = true
  await page.getByRole('button', { name: 'Refresh provider usage', exact: true }).click()
  // The application retries read failures; advance the synthetic clock without
  // slowing the suite or reaching a real API.
  await page.clock.runFor(8_000)
  await expect(page.getByRole('alert').filter({ hasText: 'Showing previously loaded records' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'AI status', exact: true })).toBeVisible()
  await expect(records.getByText('Historical provider 91', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await records.focus()
  await expect(records).toBeFocused()
})
