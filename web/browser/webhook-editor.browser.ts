import AxeBuilder from '@axe-core/playwright'
import { test, expect } from './fixtures'

test('builds AI relevance routing and includes extracted article text without replacing custom fields', async ({ page }, info) => {
  const hooks: Record<string, unknown>[] = []
  await page.route('**/api/v1/notifications/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/template-variables')) return route.fulfill({ json: [
      { key: 'item.full_text', description: 'Full extracted article plain text', example: 'Evidence text' },
      { key: 'item.full_text_status', description: 'Article text availability or truncation status', example: 'available' },
      { key: 'brief.text', description: 'Brief narrative', example: 'Brief text' },
    ] })
    if (path.endsWith('/credential-profiles')) return route.fulfill({ json: [] })
    if (path.endsWith('/events')) return route.fulfill({ json: { events: [] } })
    if (path.endsWith('/analytics')) return route.fulfill({ json: {
      total_deliveries: 0, successful_deliveries: 0, failed_deliveries: 0, success_rate_pct: 0, failures_last_24h: 0, events: [], most_failing_webhook: null,
      queue: { status: 'healthy', ok: true, pending_deliveries: 0, sending_deliveries: 0, stale_sending_deliveries: 0, oldest_pending_age_seconds: null, oldest_sending_age_seconds: null, degraded_after_seconds: 900, stale_after_seconds: 1800 },
    } })
    if (path.endsWith('/deliveries')) return route.fulfill({ json: { deliveries: [], total: 0, page: 1, page_size: 10 } })
    if (path.endsWith('/webhooks')) {
      if (route.request().method() === 'POST') {
        const saved = { ...route.request().postDataJSON(), id: 'soc-webhook', user_id: 'browser-analyst', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z' }
        hooks.push(saved)
        return route.fulfill({ json: saved })
      }
      return route.fulfill({ json: hooks })
    }
    throw new Error(`Unexpected webhook API ${path}`)
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/settings/integrations/webhooks')
  await page.getByLabel('Name', { exact: true }).fill('SOC high relevance')
  await page.getByLabel('Webhook URL', { exact: true }).fill('https://soc.example.test/intelligence')
  await page.getByLabel('Event type', { exact: true }).selectOption('article.ai.ready')
  await page.getByLabel('JSON body fields row 1 key').fill('routing')
  await page.getByLabel('JSON body fields row 1 value').fill('endpoint-triage')
  await page.getByLabel('Search payload fields', { exact: true }).fill('full text')
  await page.getByRole('combobox', { name: 'Payload field', exact: true }).selectOption('item.full_text')
  await page.getByRole('button', { name: 'Add payload field', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('JSON body fields row 4 value')).toHaveValue('{{ item.full_text }}')
  await expect(page.getByRole('button', { name: 'Add payload field', exact: true })).toBeDisabled()
  await expect(page.getByLabel('JSON body fields row 1 value')).toHaveValue('endpoint-triage')
  await page.getByText('Automation and event conditions', { exact: true }).click()
  await page.getByRole('button', { name: 'Add event conditions', exact: true }).click()
  await page.getByRole('combobox', { name: 'Field', exact: true }).selectOption('ai_relevance_label')
  await page.getByLabel('Search values', { exact: true }).fill('high')
  await page.getByRole('combobox', { name: 'Add a value', exact: true }).selectOption('high')
  await expect(page.getByRole('list', { name: 'Selected condition values' })).toContainText('high')
  await expect(page.getByText('this is not team-specific relevance', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Add condition', exact: true }).click()
  await page.getByRole('combobox', { name: 'Field', exact: true }).nth(1).selectOption('ai_relevance_score')
  await page.getByLabel('Threshold', { exact: true }).fill('0.85')
  await expect(page.getByRole('button', { name: 'Test webhook', exact: true })).toBeDisabled()
  await page.getByRole('combobox', { name: 'Payload format', exact: true }).selectOption('automation_v1')
  await page.getByLabel('Include extracted article text', { exact: true }).check()
  await page.getByRole('combobox', { name: 'Payload format', exact: true }).selectOption('template')
  await expect(page.getByLabel('JSON body fields row 4 value')).toHaveValue('{{ item.full_text }}')
  const accessibility = await new AxeBuilder({ page }).include('main').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  await info.attach('axe-soc-webhook-editor', { body: JSON.stringify(accessibility), contentType: 'application/json' })
  expect(accessibility.violations).toEqual([])
  const overflow = await page.evaluate(() => ({
    width: window.innerWidth, documentWidth: document.documentElement.scrollWidth,
    elements: Array.from(document.querySelectorAll('main *')).filter((element) => element.getBoundingClientRect().right > window.innerWidth + 1).map((element) => ({ tag: element.tagName, className: element.className, right: element.getBoundingClientRect().right, text: element.textContent?.slice(0, 80) })).slice(-20),
  }))
  await page.screenshot({ path: info.outputPath('soc-webhook-mobile.png'), fullPage: true })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.screenshot({ path: info.outputPath('soc-webhook-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(overflow.documentWidth, JSON.stringify(overflow)).toBeLessThanOrEqual(overflow.width + 1)
  await page.getByRole('button', { name: 'Create webhook', exact: true }).click()
  await expect(page.getByText('Webhook created.', { exact: true })).toBeVisible()
  expect(hooks[0]).toMatchObject({
    event_type: 'article.ai.ready', include_article_text: true,
    conditions: { op: 'all', conditions: [
      { field: 'ai_relevance_label', operator: 'in', value: ['high'] },
      { field: 'ai_relevance_score', operator: 'gte', value: 0.85 },
    ] },
  })
})
