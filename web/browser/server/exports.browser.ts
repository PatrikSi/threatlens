import type { APIRequestContext, Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { test, expect, control, signIn } from './fixtures'

async function queueExport(page: Page, request: APIRequestContext) {
  const item = await control(request, 'export-item')
  await page.goto('/export')
  await page.getByLabel('Search', { exact: true }).fill(item.title)
  await page.getByLabel('Full article text', { exact: true }).check()
  await expect(page.getByRole('button', { name: 'Generate in background', exact: true })).toBeEnabled()
  const accepted = page.waitForResponse((response) => response.url().endsWith('/api/v1/exports/jobs') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Generate in background', exact: true }).click()
  const response = await accepted
  expect(response.status()).toBe(202)
  const job = await response.json()
  expect(job.status).toBe('queued')
  await expect(page.getByRole('region', { name: 'Background exports' })).toContainText('Waiting for a worker')
  return { item, job }
}

test('real background export survives navigation, downloads content and rejects expired accepting credentials', async ({ page, request, identity }, info) => {
  await signIn(page, identity)
  const { item, job } = await queueExport(page, request)
  await page.goto('/feeds')
  await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeVisible()
  expect(await control(request, `run-export/${job.id}`)).toMatchObject({ status: 'ready', job_id: job.id })
  await page.goto('/export')
  const jobs = page.getByRole('region', { name: 'Background exports' })
  await expect(jobs.getByRole('button', { name: 'Download export', exact: true })).toBeVisible()
  const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  await info.attach('axe-ready-background-export', { body: JSON.stringify(accessibility, null, 2), contentType: 'application/json' })
  expect(accessibility.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([])
  const downloading = page.waitForEvent('download')
  await jobs.getByRole('button', { name: 'Download export', exact: true }).click()
  const download = await downloading
  expect(download.suggestedFilename()).toMatch(/\.csv$/)
  const stream = await download.createReadStream()
  expect(stream).not.toBeNull()
  const chunks: Buffer[] = []
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk))
  const csv = Buffer.concat(chunks).toString('utf8')
  expect(csv).toContain(item.id)
  expect(csv).toContain(item.title)
  expect(csv).toContain('Synthetic complete article for a real background export.')
  expect((await (await page.request.get(`/api/v1/exports/jobs/${job.id}`)).json()).item_count).toBe(1)

  await control(request, 'expire', { userId: identity.id })
  expect((await page.request.get(`/api/v1/exports/jobs/${job.id}/download`)).status()).toBe(401)
  await signIn(page, identity)
  await page.goto('/export')
  await expect(jobs).toContainText('accepting credential expired')
  await expect(jobs.getByRole('button', { name: 'Download export', exact: true })).toHaveCount(0)
  expect((await page.request.get(`/api/v1/exports/jobs/${job.id}/download`)).status()).toBe(409)

  // An authenticated different owner gets no job metadata or artifact.
  const other = await control(request, 'users')
  await signIn(page, other)
  expect((await page.request.get(`/api/v1/exports/jobs/${job.id}`)).status()).toBe(404)
  expect((await page.request.get(`/api/v1/exports/jobs/${job.id}/download`)).status()).toBe(404)
})

test('real background export cancellation persists and prevents generation or download', async ({ page, request, identity }) => {
  await signIn(page, identity)
  const { job } = await queueExport(page, request)
  const jobs = page.getByRole('region', { name: 'Background exports' })
  await jobs.getByRole('button', { name: 'Cancel export', exact: true }).click()
  await expect(jobs).toContainText('cancelled')
  await page.goto('/feeds')
  expect(await control(request, `run-export/${job.id}`)).toMatchObject({ status: 'skipped' })
  await page.goto('/export')
  await expect(jobs).toContainText('cancelled')
  await expect(jobs.getByRole('button', { name: /Download export|Cancel export/ })).toHaveCount(0)
  expect((await page.request.get(`/api/v1/exports/jobs/${job.id}/download`)).status()).toBe(409)
})
