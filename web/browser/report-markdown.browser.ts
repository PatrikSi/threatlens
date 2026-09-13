import AxeBuilder from '@axe-core/playwright'
import { test, expect } from './fixtures'
import { markdownReport } from './report-markdown-fixture'

test('renders safe report Markdown and follows citations by keyboard without loading publisher resources', async ({ page }) => {
  const report = markdownReport()
  await page.route('**/api/v1/reports/capabilities', (route) => route.fulfill({ json: {
    reporting_enabled: false, ai_configured: true, feeds: [], tags: [], classifications: [], max_sources: 100,
    preview_limit: 25, context_window_tokens: 8192, reserved_output_tokens: 1200, source_token_cap: 700,
    max_model_calls: 20, safety_percent: 15,
  } }))
  await page.route('**/api/v1/reports/templates', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/v1/reports/library?*', (route) => route.fulfill({ json: {
    items: [], current_cursor: 'first', next_cursor: null, as_of: report.created_at,
  } }))
  await page.route('**/api/v1/reports/markdown-report', (route) => route.fulfill({ json: report }))
  await page.goto('/reporting/markdown-report')
  await expect(page.getByRole('heading', { name: 'Operational assessment', level: 3 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Priorities', level: 4 })).toBeVisible()
  await expect(page.locator('article ol > li > ul > li')).toHaveCount(2)
  await expect(page.getByRole('columnheader')).toHaveCount(3)
  await expect(page.getByRole('region', { name: 'Report table' })).toBeVisible()
  await expect(page.locator('article img, article script, article iframe')).toHaveCount(0)
  await expect(page.getByText('[Image omitted: Untrusted chart]', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => 'reportInjected' in window)).toBe(false)
  for (const text of ['Literal advisory:', 'Encoded advisory:']) {
    const paragraph = page.locator('article p').filter({ hasText: text })
    await expect(paragraph.getByRole('link', { name: 'Source S1', exact: true })).toHaveCount(1)
    await expect(paragraph.locator('a[href^="https:"]')).toHaveCount(0)
  }
  const numericRow = page.getByRole('row').filter({ has: page.getByRole('cell', { name: '97%', exact: true }) })
  await expect(numericRow.getByRole('link', { name: 'Source S1' })).toBeVisible()
  await page.getByRole('link', { name: 'Source S1', exact: true }).first().focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('#report-markdown-report-source-S1')).toBeFocused()
  const results = await new AxeBuilder({ page }).include('article').analyze()
  expect(results.violations).toEqual([])
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
