import AxeBuilder from '@axe-core/playwright'
import { test, expect, control, signIn } from './fixtures'

test('real processing recovery resumes after navigation and cancels only remaining work', async ({ page, request, identity }, info) => {
  const sources = [await control(request, 'export-item'), await control(request, 'export-item')]
  await signIn(page, identity)
  await page.goto('/settings/operations?view=processing&work_stage=classification')
  await expect(page.getByRole('heading', { name: 'Incomplete processing', exact: true })).toBeVisible()
  for (const source of sources) {
    const checkbox = page.getByRole('checkbox', { name: `Select Classification for ${source.title}`, exact: true })
    await checkbox.focus()
    await page.keyboard.press('Space')
    await expect(checkbox).toBeChecked()
  }
  await page.getByRole('button', { name: 'Review selected recovery', exact: true }).click()
  const review = page.getByRole('alertdialog', { name: 'Recover selected processing?', exact: true })
  await expect(review).toBeVisible()
  const accepted = page.waitForResponse((response) => response.url().endsWith('/processing/recovery-runs') && response.request().method() === 'POST')
  await review.getByRole('button', { name: 'Queue selected recovery', exact: true }).click()
  const response = await accepted
  expect(response.status()).toBe(202)
  const run = await response.json()
  expect(run.total_count).toBe(2)
  expect(run.access_limited).toBe(false)
  await expect(page).toHaveURL(new RegExp(`work_run=${run.id}`))
  const savedURL = page.url()
  const selectedRun = page.getByRole('region', { name: 'Selected recovery run', exact: true })
  await expect(selectedRun.getByRole('status')).toHaveText('Queued: 0 completed, 0 failed, 0 cancelled of 2 selected records.')

  await page.goto('/feeds')
  await page.goto(savedURL)
  await expect(selectedRun.getByRole('status')).toContainText('0 completed')
  expect(await control(request, `advance-processing/${run.id}`)).toMatchObject({ status: 'succeeded' })
  await page.getByRole('button', { name: 'Refresh processing', exact: true }).click()
  await expect(selectedRun.getByRole('status')).toHaveText('Running: 1 completed, 0 failed, 0 cancelled of 2 selected records.')
  await selectedRun.getByRole('button', { name: 'Cancel remaining work', exact: true }).click()
  const cancel = page.getByRole('alertdialog', { name: 'Cancel remaining recovery work?', exact: true })
  await cancel.getByRole('button', { name: 'Cancel remaining work', exact: true }).click()
  await expect(selectedRun.getByRole('status')).toHaveText('Cancelled: 1 completed, 0 failed, 1 cancelled of 2 selected records.')
  await page.reload()
  await expect(selectedRun.getByRole('status')).toHaveText('Cancelled: 1 completed, 0 failed, 1 cancelled of 2 selected records.')
  expect((await (await page.request.get(`/api/v1/processing/recovery-runs/${run.id}`)).json()).items.map((item: { state: string }) => item.state).sort()).toEqual(['cancelled', 'succeeded'])
  const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  await info.attach('axe-processing-recovery', { body: JSON.stringify(accessibility, null, 2), contentType: 'application/json' })
  expect(accessibility.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([])
  const other = await control(request, 'users')
  await signIn(page, other)
  expect((await page.request.get(`/api/v1/processing/recovery-runs/${run.id}`)).status()).toBe(404)
})
