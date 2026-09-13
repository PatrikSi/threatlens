import AxeBuilder from '@axe-core/playwright'
import { test, expect, signIn, control } from './fixtures'

test('real statistics combines AI metrics with independently authorized ingestion', async ({ page, identity, request }, info) => {
  test.setTimeout(60_000)
  test.skip(process.env.THREATLENS_BROWSER_AI_PROVIDERS !== 'true', 'Run isolated harness with --ai-providers')
  await signIn(page, identity)
  const response = page.waitForResponse((result) => result.url().includes('/ai/ops/statistics?'))
  await page.goto('/stats?section=ai')
  expect((await response).status()).toBe(200)
  await expect(page.getByRole('heading', { name: 'AI statistics', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'AI reliability and workload', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Current queue by feature', exact: true })).toBeVisible()
  const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  await info.attach('axe-ai-statistics', { body: JSON.stringify(accessibility, null, 2), contentType: 'application/json' })
  expect(accessibility.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([])
  await page.getByRole('button', { name: 'Ingestion statistics', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Statistics', exact: true })).toBeVisible()

  const analyst = await control(request, 'users', { role: 'analyst' })
  await signIn(page, analyst)
  await page.goto('/stats?section=ai')
  await expect(page.getByRole('heading', { name: 'Statistics access required', exact: true })).toBeVisible()
  expect((await page.request.get('/api/v1/ai/ops/statistics')).status()).toBe(403)
  await page.getByRole('button', { name: 'Ingestion statistics', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Statistics', exact: true })).toBeVisible()
})
