import { processingRunFixture, processingRunId, processingWorkFixture } from '../src/testing/processingFixtures'
import type { ArticleExportJob, ArticleExportJobRequest } from '../src/types/exports'
import type { ProcessingRecoveryRequest } from '../src/types/processing'
import { test, expect } from './fixtures'

const appOrigin = 'http://threatlens-browser.invalid'
test.use({ appOrigin, baseURL: appOrigin })

test.beforeEach(async ({ page }) => {
  await page.routeWebSocket('**/*', (socket) => socket.close())
})

test('queues and retries background exports on a native nonsecure HTTP origin', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const submissions: ArticleExportJobRequest[] = []
  const job: ArticleExportJob = {
    id: '10000000-0000-4000-8000-000000000099', format: 'csv', status: 'queued',
    created_at: '2026-09-13T10:00:00Z', expires_at: '2026-09-14T10:00:00Z', started_at: null,
    completed_at: null, attempts: 0, completed_items: 0, item_count: null, file_size: null,
    filename: null, error_code: null, message: null, download_available: false,
  }
  await page.route('**/api/v1/exports/capabilities', (route) => route.fulfill({ json: {
    formats: [{ id: 'csv', label: 'CSV', extension: '.csv', media_type: 'text/csv', description: 'Article inventory',
      supports_article_text: true, supports_iocs: true, supports_user_state: true }],
    feeds: [], tags: [], classifications: [], max_items: 10000, max_pdf_items: 500,
    max_uncompressed_bytes: 262144000, preview_limit: 25,
  } }))
  await page.route('**/api/v1/exports/preview', (route) => route.fulfill({ json: {
    total_matches: 1, articles_with_text: 1, items_with_iocs: 0, preview_limit: 25,
    exceeds_export_limit: false, exceeds_pdf_limit: false, items: [],
  } }))
  await page.route('**/api/v1/exports/jobs?*', (route) => route.fulfill({ json: {
    items: submissions.length ? [job] : [], has_more: false,
  } }))
  await page.route('**/api/v1/exports/jobs', (route) => {
    submissions.push(route.request().postDataJSON())
    return submissions.length === 1
      ? route.fulfill({ status: 503, json: { detail: 'Export acceptance response unavailable' } })
      : route.fulfill({ status: 202, json: job })
  })
  await page.goto('/export')
  expect(await page.evaluate(() => ({ secure: isSecureContext, uuid: typeof crypto.randomUUID, random: typeof crypto.getRandomValues })))
    .toEqual({ secure: false, uuid: 'undefined', random: 'function' })
  const queue = page.getByRole('button', { name: 'Generate in background', exact: true })
  await queue.click()
  await expect(page.getByRole('alert').filter({ hasText: 'Export acceptance response unavailable' })).toBeVisible()
  await queue.click()
  await expect(page.getByRole('status').filter({ hasText: 'Background export accepted' })).toBeVisible()
  expect(submissions).toHaveLength(2)
  expect(submissions[1]).toEqual(submissions[0])
  expect(submissions[0].idempotency_key).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/)
  await queue.click()
  await expect.poll(() => submissions.length).toBe(3)
  expect(submissions[2].idempotency_key).not.toBe(submissions[0].idempotency_key)
  expect(errors).toEqual([])
})

test('reviews and retries selected recovery on a native nonsecure HTTP origin', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const submissions: ProcessingRecoveryRequest[] = []
  const run = processingRunFixture()
  await page.route('**/api/v1/operations/overview', (route) => route.fulfill({ status: 503, json: { detail: 'Snapshot unavailable' } }))
  await page.route('**/api/v1/processing/work?*', (route) => route.fulfill({ json: {
    items: [processingWorkFixture()], next_cursor: null, has_more: false,
  } }))
  await page.route('**/api/v1/processing/recovery-runs?*', (route) => route.fulfill({ json: {
    items: submissions.length ? [run] : [], next_cursor: null, has_more: false,
  } }))
  await page.route(`**/api/v1/processing/recovery-runs/${processingRunId}`, (route) => route.fulfill({ json: run }))
  await page.route('**/api/v1/processing/recovery-runs', (route) => {
    submissions.push(route.request().postDataJSON())
    return submissions.length === 1
      ? route.fulfill({ status: 503, json: { detail: 'Recovery acceptance response unavailable' } })
      : route.fulfill({ status: 202, json: run })
  })
  await page.goto('/settings/operations?view=processing')
  expect(await page.evaluate(() => ({ secure: isSecureContext, uuid: typeof crypto.randomUUID, random: typeof crypto.getRandomValues })))
    .toEqual({ secure: false, uuid: 'undefined', random: 'function' })
  const selected = page.getByRole('checkbox', { name: 'Select Tagging for Gateway advisory' })
  await selected.focus()
  await page.keyboard.press('Space')
  const openReview = page.getByRole('button', { name: 'Review selected recovery' })
  await openReview.click()
  const review = page.getByRole('alertdialog', { name: 'Recover selected processing?' })
  await expect(review.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused()
  await review.getByRole('button', { name: 'Queue selected recovery' }).click()
  await expect(review.getByRole('alert')).toContainText('reuses the same request key')
  await review.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(openReview).toBeFocused()
  await openReview.click()
  await review.getByRole('button', { name: 'Queue selected recovery' }).click()
  await expect(review).toBeHidden()
  expect(submissions).toHaveLength(2)
  expect(submissions[1]).toEqual(submissions[0])
  expect(submissions[0].idempotency_key).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/)
  await expect(page).toHaveURL(new RegExp(`work_run=${processingRunId}`))
  await expect(page.getByRole('heading', { name: 'Selected recovery run' })).toBeFocused()
  await selected.check()
  await openReview.click()
  await review.getByRole('button', { name: 'Queue selected recovery' }).click()
  await expect.poll(() => submissions.length).toBe(3)
  expect(submissions[2].idempotency_key).not.toBe(submissions[0].idempotency_key)
  expect(errors).toEqual([])
})
