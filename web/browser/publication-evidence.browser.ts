import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import type { IndicatorPage } from '../src/types/indicators'
import { indicatorFixture, indicatorPageFixture } from '../tests/fixtures/indicatorAutomation'
import { test, expect } from './fixtures'

const quote = 'Contact suspicious[.]test for control.'
const reason = 'Analyst verified campaign infrastructure.'

async function publicationRoutes(page: Page) {
  const state = { changed: false, denied: false, previews: 0, evidenceRequests: [] as URL[] }
  const evidence: IndicatorPage = {
    ...indicatorPageFixture,
    page_size: 1,
    items: [{ ...indicatorFixture, assessment: {
      version: 2, verdict: 'malicious', reason, source_revision: 7, extraction_revision: 4,
      expires_at: null, expired: false, current: true, updated_at: '2026-09-27T00:00:00Z',
    } }],
  }
  await page.route('**/api/v1/exports/capabilities', (route) => route.fulfill({ json: {
    formats: [{ id: 'csv', label: 'CSV', extension: '.csv', media_type: 'text/csv', description: 'Articles',
      supports_article_text: true, supports_iocs: true, supports_user_state: true }],
    feeds: [], tags: [], classifications: [], max_items: 10000, max_pdf_items: 500,
    max_uncompressed_bytes: 262144000, preview_limit: 25,
  } }))
  await page.route('**/api/v1/exports/preview', (route) => route.fulfill({ json: {
    total_matches: 1, articles_with_text: 1, items_with_iocs: 1, preview_limit: 25,
    exceeds_export_limit: false, exceeds_pdf_limit: false, items: [],
  } }))
  await page.route('**/api/v1/exports/jobs?*', (route) => route.fulfill({ json: { items: [], has_more: false } }))
  await page.route('**/api/v1/teams?*', (route) => route.fulfill({ json: {
    items: [{ id: 'team-1', name: 'Endpoint team' }], total: 1, page: 1, page_size: 50,
  } }))
  await page.route('**/api/v1/teams/team-1/indicator-publications?*', (route) => route.fulfill({ json: {
    items: [], has_more: false, next_cursor: null,
  } }))
  await page.route('**/api/v1/teams/team-1/indicator-publications/preview', (route) => {
    state.previews += 1
    return route.fulfill({ json: {
      fingerprint: `reviewed-evidence-${state.previews}`, matched_articles: 1, excluded_or_unreviewed: 0,
      max_articles: 100, max_indicators: 250, indicators: [{
        assessment_id: 'review-1', item_id: 'item-1', ioc_id: indicatorFixture.id,
        type: indicatorFixture.type, value: indicatorFixture.value, title: 'Reviewed article',
        assessment_version: 2, evidence_count: 1, expires_at: null, source_revision: 7, extraction_revision: 4,
      }],
    } })
  })
  await page.route('**/api/v1/items/item-1/indicators?*', (route) => {
    state.evidenceRequests.push(new URL(route.request().url()))
    return state.denied
      ? route.fulfill({ status: 403, json: { detail: 'Team evidence access removed' } })
      : route.fulfill({ json: { ...evidence, source_revision: state.changed ? 8 : 7 } })
  })
  await page.goto('/export')
  await page.getByRole('button', { name: 'Reviewed team publications' }).click()
  await page.getByRole('combobox', { name: 'Publication team' }).selectOption('team-1')
  await page.getByRole('button', { name: 'Preview reviewed indicators', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Review evidence for suspicious.test' })).toBeVisible()
  return state
}

test('reviews exact evidence with a bounded request, keyboard containment and restored focus', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await publicationRoutes(page)
  const trigger = page.getByRole('button', { name: 'Review evidence for suspicious.test' })
  await trigger.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'Reviewed indicator evidence' })
  await expect(dialog.getByText(quote, { exact: true })).toBeVisible()
  await expect(dialog.getByText(`Analyst review: ${reason}`, { exact: true })).toBeVisible()
  expect(state.evidenceRequests.length).toBeGreaterThan(0)
  for (const request of state.evidenceRequests) {
    expect(Object.fromEntries(request.searchParams)).toEqual({ team_id: 'team-1', ioc_id: 'ioc-1', page_size: '1' })
  }
  const close = dialog.getByRole('button', { name: 'Close dialog', exact: true })
  await expect(close).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(dialog.getByRole('button', { name: 'Close evidence', exact: true })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(close).toBeFocused()
  expect(await page.locator('button[aria-label="Review evidence for suspicious.test"]')
    .evaluate((button) => Boolean(button.closest('[inert]')))).toBe(true)
  const accessibility = await new AxeBuilder({ page }).include('[role="dialog"]').analyze()
  expect(accessibility.violations.map(({ id }) => id)).toEqual([])
  await info.attach('reviewed-evidence-accessibility', { body: JSON.stringify(accessibility), contentType: 'application/json' })
  await page.screenshot({ path: info.outputPath('reviewed-evidence-mobile.png') })
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()
  expect(await trigger.evaluate((button) => Boolean(button.closest('[inert]')))).toBe(false)
  await trigger.click()
  await expect(dialog.getByText(quote, { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: 'Close evidence', exact: true }).click()
  await expect(trigger).toBeFocused()
  await page.getByRole('checkbox', { name: /I reviewed this exact selection/ }).check()
  await expect(page.getByRole('button', { name: 'Approve reviewed publication', exact: true })).toBeEnabled()
})

test('withholds changed and denied evidence and recovers through an explicit preview refresh', async ({ page }) => {
  const state = await publicationRoutes(page)
  const trigger = page.getByRole('button', { name: 'Review evidence for suspicious.test' })
  const approve = page.getByRole('checkbox', { name: /I reviewed this exact selection/ })
  await approve.check()
  state.changed = true
  await trigger.click()
  const dialog = page.getByRole('dialog', { name: 'Reviewed indicator evidence' })
  await expect(dialog.getByText(/The evidence or analyst review changed/)).toBeVisible()
  await expect(dialog.getByText(quote, { exact: true })).toHaveCount(0)
  await expect(dialog.getByText(`Analyst review: ${reason}`, { exact: true })).toHaveCount(0)
  state.changed = false
  await dialog.getByRole('button', { name: 'Refresh publication preview', exact: true }).click()
  await expect(dialog).toBeHidden()
  await expect.poll(() => state.previews).toBe(2)
  await expect(approve).not.toBeChecked()
  state.denied = true
  await trigger.click()
  await expect(dialog.getByRole('alert')).toContainText('Team evidence access removed')
  await expect(dialog.getByText(quote, { exact: true })).toHaveCount(0)
  state.denied = false
  await dialog.getByRole('button', { name: 'Retry evidence', exact: true }).click()
  await expect(dialog.getByText(quote, { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(trigger).toBeFocused()
})
