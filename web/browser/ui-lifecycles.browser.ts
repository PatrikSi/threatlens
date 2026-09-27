import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { assessmentFixture } from '../tests/fixtures/articleIntelligence'
import { operationsHistoryFixture, operationsSampleFixture } from '../src/testing/operationsHistoryFixtures'
import { test, expect } from './fixtures'

const team = { id: 'team-1', key: 'soc', name: 'SOC', description: 'Security operations team', membership_group_id: 'group-1', manager_group_id: 'group-2', active: true, revision: 1, can_manage: true, created_at: '2026-09-27T00:00:00Z', updated_at: '2026-09-27T00:00:00Z' }
const entry = { assessment_id: 'assessment-1', assessment_version: 1, item_id: 'item-1', item_title: 'Endpoint service persistence', team_id: 'team-1', status: 'pending', generated_at: null, evidence_age_seconds: 100, hunt: assessmentFixture.assessment!.result!.hunts[0], claim: { version: 0, owner_user_id: null }, owner_name: null, reviewer_name: null, reviewed_at: null, can_claim: true, can_release: false, can_schedule: true, review_schedule: { version: 1, priority: 'normal', due_at: null, overdue: false, reminded_at: null, reminder_acknowledged_at: null }, investigation: null }
async function installTeams(page: Page, visible: () => boolean) {
  await page.route('**/api/v1/iam/groups', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/v1/teams?*', (route) => route.fulfill({ json: { items: [team], total: 1, page: 1, page_size: 25 } }))
  await page.route('**/api/v1/teams/team-1', (route) => route.fulfill({ json: team }))
  await page.route('**/api/v1/teams/team-1/members?*', (route) => route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 50 } }))
  await page.route('**/api/v1/teams/team-1/hunts?*', (route) => route.fulfill({ json: { items: visible() && new URL(route.request().url()).searchParams.get('status') !== 'accepted' ? [entry] : [], next_cursor: null, has_more: false, limit: 25 } }))
  await page.route('**/api/v1/teams/team-1/hunts/views', (route) => route.fulfill({ json: { items: [], can_manage: true } }))
}

test('guards hunt filters and navigation, and preserves a schedule when polling removes its row', async ({ page }) => {
  let visible = true
  await installTeams(page, () => visible)
  await page.goto('/teams?team=team-1&panel=hunts')
  await expect(page.getByRole('heading', { name: 'Shared monitoring views', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Team hunt queue', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('link', { name: 'Team details', exact: true })).not.toHaveAttribute('aria-current', 'page')
  await page.getByText('Set review priority and deadline', { exact: true }).click()
  const priority = page.getByRole('combobox', { name: 'Review priority', exact: true })
  const deadline = page.getByLabel('Review deadline (local time)')
  await priority.selectOption('urgent')
  await deadline.fill('2026-09-29T10:00')
  await page.getByRole('combobox', { name: 'Hunt status', exact: true }).selectOption('accepted')
  const discard = page.getByRole('alertdialog', { name: 'Discard unsaved changes?' })
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(priority).toHaveValue('urgent')
  await expect(deadline).toHaveValue('2026-09-29T10:00')
  visible = false
  await page.clock.fastForward(31_000)
  await expect(page.getByText('A hunt with an unsaved schedule left this page', { exact: false })).toBeVisible()
  await expect(priority).toHaveValue('urgent')
  await expect(page.getByRole('button', { name: 'Save review schedule', exact: true })).toBeDisabled()
  await page.getByRole('link', { name: 'Feeds', exact: true }).first().click()
  await expect(discard).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(deadline).toHaveValue('2026-09-29T10:00')
  await page.getByRole('button', { name: 'Reload review schedule', exact: true }).click()
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: 'Discard changes', exact: true }).click()
  await expect(priority).toHaveCount(0)
  visible = true
  await page.clock.fastForward(31_000)
  await page.getByText('Set review priority and deadline', { exact: true }).click()
  await priority.selectOption('high')
  await page.getByRole('combobox', { name: 'Hunt status', exact: true }).selectOption('accepted')
  await discard.getByRole('button', { name: 'Discard changes', exact: true }).click()
  await expect(priority).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Hunt status', exact: true }).selectOption('pending')
  await page.getByText('Set review priority and deadline', { exact: true }).click()
  await expect(priority).toHaveValue('normal')
})

const overview = {
  generated_at: new Date().toISOString(), overall_status: 'healthy',
  application: { version: '2.0.1', schema_revision: 'review-fixture', expected_schema_revision: 'review-fixture', schema_current: true },
  components: [{ key: 'database', label: 'PostgreSQL', status: 'healthy', summary: 'Database queries are responding.', checked_at: new Date().toISOString(), metrics: {} }],
  storage: [], backlogs: [], issues: [], recovery: { latest_backup: null, latest_verify: null, latest_restore_drill: null, latest_restore: null },
}
test('retains health during a transient outage but hides snapshots and actions on explicit denial', async ({ page }) => {
  let status = 200
  await page.route('**/api/v1/operations/overview', (route) => route.fulfill({ status, json: status === 200 ? overview : { detail: status === 403 ? 'Operations permission revoked' : 'Snapshot temporarily unavailable' } }))
  await page.goto('/settings/operations?signal=database')
  await expect(page.getByText('Database queries are responding.', { exact: true })).toBeVisible()
  status = 503
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(page.getByText('This is the last successful snapshot', { exact: false })).toBeVisible()
  await expect(page.getByText('Database queries are responding.', { exact: true })).toBeVisible()
  status = 403
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(page.getByText('Operations permission revoked', { exact: false })).toBeVisible()
  await expect(page.getByText('Database queries are responding.', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Download diagnostics', exact: true })).toBeDisabled()
  status = 503
  const deniedRecovery = page.waitForResponse((response) => response.url().endsWith('/operations/overview') && response.status() === 503)
  await page.getByRole('button', { name: 'Retry health', exact: true }).click()
  await deniedRecovery
  await expect(page.getByRole('button', { name: 'Retry health', exact: true })).toBeEnabled()
  await expect(page.getByText('Database queries are responding.', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Download diagnostics', exact: true })).toBeDisabled()
  status = 200
  await page.getByRole('button', { name: 'Retry health', exact: true }).click()
  await expect(page.getByText('Database queries are responding.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download diagnostics', exact: true })).toBeEnabled()
})

test('keeps mobile operation controls in the viewport and scrolls chart data within its wrapper', async ({ page }) => {
  const history = operationsHistoryFixture([operationsSampleFixture({ runtime_metrics: { container_memory_percent: 88 }, backlogs: [{ key: 'classification', label: 'Classification', status: 'degraded', pending_count: 4, active_count: 1, stale_count: 0, failed_count: 0, oldest_pending_age_seconds: 600, degraded_after_seconds: 300 }] })])
  await page.route('**/api/v1/operations/overview', (route) => route.fulfill({ json: overview }))
  await page.route('**/api/v1/operations/health-history?*', (route) => route.fulfill({ json: history }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/settings/operations?view=trends')
  const selector = page.getByRole('combobox', { name: 'Capacity trend', exact: true })
  await expect(selector).toBeVisible()
  for (const element of [selector, page.getByRole('region', { name: 'Freshness and runtime pressure' }), page.getByRole('group', { name: 'Worker capacity and load chart', exact: true })]) {
    const box = (await element.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(391)
  }
  await selector.selectOption('memory')
  const chart = page.getByRole('group', { name: 'Worker capacity and load chart', exact: true })
  await chart.focus()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => chart.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0)
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(result.violations).toEqual([])
})

test('opens badge-free compact article details as a named mobile dialog and restores focus', async ({ page }) => {
  const item = { id: 'item-1', feed_id: 'browser-feed', feed_name: 'Browser fixture feed', title: 'Service behavior article', url: 'https://publisher.example.test/article', summary: 'A new service was observed.', published_at: '2026-09-16T10:00:00Z', first_seen_at: '2026-09-16T10:00:00Z', status: 'content_fetched', classification: null, is_read: true, is_starred: false, tags: [], ai_status: null }
  await page.route('**/api/v1/items?*', (route) => route.fulfill({ json: { items: [item], total: 1, page: 1, page_size: 100 } }))
  await page.route('**/api/v1/items/item-1', (route) => route.fulfill({ json: { ...item, source_guid: null, last_error: null, state: { is_read: true, is_starred: false, note: null, updated_at: null }, article: null, ai_insight: null } }))
  await page.route('**/api/v1/teams?*', (route) => route.fulfill({ json: { items: [team], total: 1, page: 1, page_size: 50 } }))
  await page.route('**/api/v1/items/item-1/team-assessment**', (route) => route.fulfill({ json: assessmentFixture }))
  await page.route('**/api/v1/intelligence/items/item-1/indicators?*', (route) => route.fulfill({ json: { items: [], next_cursor: null, has_more: false } }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/?assessment_team=team-1')
  const opener = page.getByRole('button', { name: 'Open article details: Service behavior article', exact: true })
  await expect(opener).toBeVisible()
  expect((await opener.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await opener.click()
  const dialog = page.getByRole('dialog', { name: item.title, exact: true })
  const close = dialog.getByRole('button', { name: 'Back to articles', exact: true })
  await expect(close).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true)
  await page.keyboard.press('Tab')
  await expect(close).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(opener).toBeFocused()
  await opener.click()
  const note = page.getByLabel('Review note for Review unusual services')
  await note.fill('Keep this review through nested dialogs and resizing.')
  const reload = dialog.getByRole('button', { name: 'Reload saved reviews', exact: true })
  await reload.click()
  const discard = page.getByRole('alertdialog', { name: 'Discard unsaved hunt review notes?' })
  await expect(discard).toBeVisible()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect(discard).toBeVisible()
  await expect(discard.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(discard).toHaveCount(0)
  await expect(reload).toBeFocused()
  await expect(note).toHaveValue('Keep this review through nested dialogs and resizing.')
  await expect(dialog).toBeVisible()
  await expect(note).toHaveValue('Keep this review through nested dialogs and resizing.')
  expect(await page.locator('main').evaluate((element) => Boolean(element.closest('[inert]')))).toBe(true)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(reload).toBeFocused()
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(result.violations).toEqual([])
  await close.click()
  await expect(page.getByRole('button', { name: 'Preview', exact: true })).toBeEnabled()
})

test('labels mobile audit records with valid group semantics', async ({ page, api }) => {
  api.identity = { ...api.identity, role: 'admin' }
  await page.route('**/api/v1/audit-logs?*', (route) => route.fulfill({ json: { logs: [{
    id: 'audit-1', action: 'auth.login', resource_type: 'user', resource_id: null,
    actor_user_id: null, actor_principal_type: 'user', actor_principal_id: null,
    actor_label_snapshot: 'Review analyst', resource_label_snapshot: null,
    credential_kind: null, credential_id: null, request_id: null, source_ip: null,
    authorization_elevation_ids: [], authorization_approval_id: null, execution_receipt_id: null,
    success: true, metadata_json: {}, data_access_redacted: false, created_at: '2026-09-27T10:00:00Z',
  }], total: 1, page: 1, page_size: 50 } }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/settings/audit-logs')
  await expect(page.getByRole('group', { name: 'Audit events', exact: true })).toBeVisible()
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(result.violations).toEqual([])
})
