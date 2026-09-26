import AxeBuilder from '@axe-core/playwright'
import { test, expect, feed } from './fixtures'
import { indicatorFixture, indicatorPageFixture } from '../tests/fixtures/indicatorAutomation'

test('configures a signed automation subscription and previews matching without sending', async ({ page }, info) => {
  const profile = { id: 'profile-1', name: 'SIEM signing', enabled: true, auth_type: 'none', header_name: null, auth_configured: false, signing_configured: true, revision: 1 }
  const hooks: Record<string, unknown>[] = []
  const previewWrites: Record<string, unknown>[] = []
  await page.route('**/api/v1/notifications/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path.endsWith('/credential-profiles')) return route.fulfill({ json: [profile] })
    if (path.endsWith('/template-variables')) return route.fulfill({ json: [] })
    if (path.endsWith('/analytics')) return route.fulfill({ json: { total_deliveries: 0, successful_deliveries: 0, failed_deliveries: 0, success_rate_pct: 0, failures_last_24h: 0, events: [], queue: { status: 'healthy', ok: true, pending_deliveries: 0, sending_deliveries: 0, stale_sending_deliveries: 0, oldest_pending_age_seconds: null, oldest_sending_age_seconds: null, degraded_after_seconds: 900, stale_after_seconds: 1800 }, most_failing_webhook: null } })
    if (path.endsWith('/events')) return route.fulfill({ json: { events: [{ id: 'event-1', event_type: 'intel.indicators.changed', label: 'Indicators updated', created_at: '2026-09-26T00:00:00Z' }] } })
    if (path.endsWith('/preview')) {
      previewWrites.push(route.request().postDataJSON())
      return route.fulfill({ json: { matches: true, checks: [{ field: 'ioc_role', matched: true, reason: 'Matched malicious infrastructure' }], missing_fields: [], automation_payload: { event_id: 'event-1', data: { indicators: [{ type: 'domain', value: 'suspicious.test' }] } } } })
    }
    if (path.endsWith('/deliveries')) return route.fulfill({ json: { deliveries: [], total: 0, page: 1, page_size: 10 } })
    if (path.endsWith('/webhooks')) {
      if (method === 'POST') {
        const saved = { ...route.request().postDataJSON(), id: 'hook-1', user_id: 'browser-analyst', created_at: '2026-09-26T00:00:00Z', updated_at: '2026-09-26T00:00:00Z' }
        hooks.push(saved)
        return route.fulfill({ json: saved })
      }
      return route.fulfill({ json: hooks })
    }
    throw new Error(`Unexpected notification request ${method} ${path}`)
  })
  await page.goto('/settings/integrations/webhooks')
  await page.getByLabel('Name', { exact: true }).fill('SIEM indicator updates')
  await page.getByLabel('Webhook URL', { exact: true }).fill('https://siem.example.test/events')
  await page.getByLabel('Event type', { exact: true }).selectOption('intel.indicators.changed')
  await page.getByText('Automation and event conditions', { exact: true }).click()
  await page.getByRole('combobox', { name: 'Payload format', exact: true }).selectOption('automation_v1')
  await page.getByRole('combobox', { name: 'Credential profile', exact: true }).selectOption('profile-1')
  await page.getByRole('button', { name: 'Add event conditions', exact: true }).click()
  await page.getByRole('combobox', { name: 'Sample event', exact: true }).selectOption('event-1')
  await page.getByRole('button', { name: 'Evaluate event', exact: true }).click()
  await expect(page.getByText('This event matches.', { exact: true })).toBeVisible()
  expect(previewWrites[0]).toMatchObject({ event_id: 'event-1', webhook: { payload_mode: 'automation_v1', credential_profile_id: 'profile-1', conditions: { op: 'all' } } })
  await expect(page.getByRole('button', { name: 'Test webhook', exact: true })).toBeDisabled()
  await expect(page.getByLabel('Body mode', { exact: true })).toBeDisabled()
  const accessibility = await new AxeBuilder({ page }).include('main').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  await info.attach('axe-automation-editor', { body: JSON.stringify(accessibility), contentType: 'application/json' })
  expect(accessibility.violations).toEqual([])
  await page.getByRole('button', { name: 'Create webhook', exact: true }).click()
  await expect(page.getByText('Webhook created.', { exact: true })).toBeVisible()
  expect(hooks[0]).toMatchObject({ payload_mode: 'automation_v1', credential_profile_id: 'profile-1', event_type: 'intel.indicators.changed' })
})

test('reviews source-linked indicators with keyboard-safe nested discard dialogs', async ({ page }, info) => {
  const item = { id: 'item-1', feed_id: feed.id, feed_name: feed.name, title: 'Control infrastructure', url: 'https://publisher.example.test/story', summary: 'Contact suspicious[.]test for control.', published_at: '2026-09-26T00:00:00Z', first_seen_at: '2026-09-26T00:00:00Z', status: 'content_fetched', classification: null, is_read: true, is_starred: false, tags: [], ai_status: null }
  let verdict: string | null = null
  await page.route('**/api/v1/items?*', (route) => route.fulfill({ json: { items: [item], total: 1, page: 1, page_size: 100 } }))
  await page.route('**/api/v1/items/item-1', (route) => route.fulfill({ json: { ...item, source_guid: null, last_error: null, article: null, ai_insight: null, state: { is_read: true, is_starred: false, note: null, updated_at: null } } }))
  await page.route('**/api/v1/teams?*', (route) => route.fulfill({ json: { items: [{ id: 'team-1', name: 'Endpoint team' }], total: 1, page: 1, page_size: 50 } }))
  await page.route('**/api/v1/items/item-1/indicators?*', (route) => route.fulfill({ json: { ...indicatorPageFixture, items: [{ ...indicatorFixture, assessment: verdict ? { verdict, reason: 'Confirmed in current source', version: 1, source_revision: 7, extraction_revision: 4, expires_at: null, expired: false, current: true, updated_at: '2026-09-26T00:00:00Z' } : null }] } }))
  await page.route('**/api/v1/items/item-1/indicators/ioc-1/assessment?*', (route) => {
    const body = route.request().postDataJSON()
    expect(body).toMatchObject({ source_revision: 7, extraction_revision: 4, expected_version: 0 })
    verdict = body.verdict
    return route.fulfill({ json: { version: 1 } })
  })
  await page.goto('/')
  await page.locator('article.tl-dashboard-rss-card button.tl-dashboard-rss-toggle').click()
  await page.getByText('Indicators and team verdicts', { exact: true }).click()
  await page.getByLabel('Indicator assessment team').selectOption('team-1')
  await page.getByRole('button', { name: 'Review indicator', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Review indicator for this team', exact: true })
  await expect(dialog).toContainText('suspicious[.]test')
  await expect(dialog).toContainText('Maliciousness confidence: not scored')
  await dialog.getByLabel('Review reason').fill('Confirmed in current source')
  await dialog.getByLabel('Analyst verdict').selectOption('malicious')
  await page.keyboard.press('Escape')
  const discard = page.getByRole('alertdialog', { name: 'Discard unsaved changes?', exact: true })
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(dialog.getByLabel('Review reason')).toHaveValue('Confirmed in current source')
  const accessibility = await new AxeBuilder({ page }).include('[role="dialog"]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  await info.attach('axe-indicator-review', { body: JSON.stringify(accessibility), contentType: 'application/json' })
  expect(accessibility.violations).toEqual([])
  await dialog.getByRole('button', { name: 'Save team verdict', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(dialog).not.toBeVisible()
  await expect(page.getByText('Team verdict: malicious', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Refresh indicators', exact: true }).click()
})
