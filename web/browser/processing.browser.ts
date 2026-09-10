import AxeBuilder from '@axe-core/playwright'
import { processingRunFixture, processingRunId, processingWorkFixture } from '../src/testing/processingFixtures'
import type { ProcessingRecoveryRequest } from '../src/types/processing'
import { test, expect, revalidateSession } from './fixtures'

test('reviews bounded processing, reuses acceptance keys, and resumes and cancels a run with the keyboard', async ({ page }, info) => {
  let run = processingRunFixture()
  const submissions: ProcessingRecoveryRequest[] = []
  await page.route('**/api/v1/operations/overview', (route) => route.fulfill({ status: 503, json: { detail: 'Health snapshot unavailable' } }))
  await page.route('**/api/v1/processing/work?*', (route) => route.fulfill({ json: { items: [processingWorkFixture()], next_cursor: null, has_more: false } }))
  await page.route('**/api/v1/processing/recovery-runs?*', (route) => route.fulfill({ json: { items: submissions.length ? [run] : [], next_cursor: null, has_more: false } }))
  await page.route('**/api/v1/processing/recovery-runs', (route) => {
    submissions.push(route.request().postDataJSON())
    return submissions.length === 1
      ? route.fulfill({ status: 503, json: { detail: 'Acceptance response unavailable' } })
      : route.fulfill({ status: 202, json: run })
  })
  await page.route(`**/api/v1/processing/recovery-runs/${processingRunId}`, (route) => route.fulfill({ json: run }))
  await page.route(`**/api/v1/processing/recovery-runs/${processingRunId}/cancel`, (route) => {
    expect(route.request().postDataJSON()).toEqual({ expected_version: 1 })
    run = { ...run, version: 2, status: 'cancelled', cancelled_count: 1, can_cancel: false }
    return route.fulfill({ json: run })
  })
  await page.goto('/settings/operations?view=processing&work_stage=tagging&work_state=attention')
  const checkbox = page.getByRole('checkbox', { name: 'Select Tagging for Gateway advisory' })
  await checkbox.focus()
  await page.keyboard.press('Space')
  await expect(page.getByText('1 selected on this page')).toBeVisible()
  const reviewButton = page.getByRole('button', { name: 'Review selected recovery' })
  await reviewButton.focus()
  await page.keyboard.press('Enter')
  const review = page.getByRole('alertdialog', { name: 'Recover selected processing?' })
  await expect(review).toBeVisible()
  await expect(review.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(review.getByRole('button', { name: 'Queue selected recovery' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(review.getByRole('alert')).toContainText('reuses the same request key')
  await review.getByRole('button', { name: 'Queue selected recovery' }).click()
  await expect(review).toBeHidden()
  expect(submissions).toHaveLength(2)
  expect(submissions[1]).toEqual(submissions[0])
  expect(submissions[0].items).toEqual([{ item_id: processingWorkFixture().item_id, stage: 'tagging', revision: 'work-revision-1' }])
  await expect(page).toHaveURL(new RegExp(`work_run=${processingRunId}`))
  await expect(page.getByRole('heading', { name: 'Selected recovery run' })).toBeFocused()
  const url = page.url()
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  await info.attach('axe-processing', { body: JSON.stringify(results, null, 2), contentType: 'application/json' })
  expect(results.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([])
  await page.getByRole('link', { name: 'Feeds', exact: true }).first().click()
  await page.goBack()
  await expect(page).toHaveURL(url)
  await expect(page.getByText('Queued: 0 completed, 0 failed, 0 cancelled of 1 selected records.')).toBeVisible()
  await page.getByRole('button', { name: 'Cancel remaining work', exact: true }).click()
  const cancellation = page.getByRole('alertdialog', { name: 'Cancel remaining recovery work?' })
  await expect(cancellation).toContainText('Already committed processing results are preserved')
  await cancellation.getByRole('button', { name: 'Cancel remaining work', exact: true }).click()
  await expect(page.getByText('Cancelled: 0 completed, 0 failed, 1 cancelled of 1 selected records.')).toBeVisible()
})

test('preserves reviewed processing through session verification and clears it on account change', async ({ page, api }) => {
  let writes = 0
  await page.route('**/api/v1/operations/overview', (route) => route.fulfill({ status: 503, json: { detail: 'Health snapshot unavailable' } }))
  await page.route('**/api/v1/processing/work?*', (route) => route.fulfill({ json: { items: [processingWorkFixture()], next_cursor: null, has_more: false } }))
  await page.route('**/api/v1/processing/recovery-runs?*', (route) => route.fulfill({ json: { items: [], next_cursor: null, has_more: false } }))
  await page.route('**/api/v1/processing/recovery-runs', (route) => { writes += 1; return route.fulfill({ json: processingRunFixture() }) })
  await page.goto('/settings/operations?view=processing')
  await page.getByRole('checkbox', { name: 'Select Tagging for Gateway advisory' }).check()
  await page.getByRole('button', { name: 'Review selected recovery' }).click()
  const review = page.getByRole('alertdialog', { name: 'Recover selected processing?' })
  await expect(review).toBeVisible()
  api.sessionStatus = 503
  await revalidateSession(page)
  const verification = page.getByRole('dialog', { name: 'Session check unavailable' })
  await expect(verification).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(verification).toBeVisible()
  expect(writes).toBe(0)
  api.sessionStatus = 200
  await verification.getByRole('button', { name: 'Retry session check' }).click()
  await expect(verification).toBeHidden()
  await expect(review).toBeVisible()
  await expect(review.getByRole('button', { name: 'Queue selected recovery' })).toBeEnabled()
  api.identity = { ...api.identity, id: 'replacement-operator' }
  await page.evaluate(() => window.dispatchEvent(new StorageEvent('storage', {
    key: 'threatlens.auth.sync', newValue: JSON.stringify({ id: 'processing-account-change', at: Date.now() }),
  })))
  await expect(review).toBeHidden()
  await expect(page.getByText('0 selected on this page')).toBeVisible()
  expect(writes).toBe(0)
})
