import AxeBuilder from '@axe-core/playwright'
import type { WorkspaceRolePolicyResponse } from '../../src/types/workspace'
import { test, expect, control, signIn } from './fixtures'
import { writeApi } from './enterprise-helpers'

test.use({ actionTimeout: 10_000 })

test('real organization policies enforce navigation and preserve personal choices', async ({ page, request, identity }, info) => {
  test.setTimeout(90_000)
  const analyst = await control(request, 'users', { role: 'analyst' })
  await signIn(page, identity)
  const policyPath = '/workspace/role-policies/analyst'
  const baseline = await (await page.request.get(`/api/v1${policyPath}`)).json() as WorkspaceRolePolicyResponse
  try {
    await page.goto('/settings/workspace')
    await page.getByRole('tab', { name: /^Role defaults/ }).click()
    await page.getByRole('group', { name: 'Built-in role', exact: true }).getByRole('button', { name: 'Analyst', exact: true }).click()
    await page.getByRole('combobox', { name: /^Default start page/ }).selectOption('primary.stats')
    await page.getByRole('combobox', { name: 'Start page policy', exact: true }).selectOption('enforced')
    await page.getByRole('combobox', { name: 'Dashboard arrangement policy', exact: true }).selectOption('enforced')
    await page.getByRole('checkbox', { name: 'Allow users to customize Stats', exact: true }).uncheck()
    const saved = page.waitForResponse((response) => response.url().endsWith(policyPath) && response.request().method() === 'PUT')
    await page.getByRole('button', { name: 'Save navigation defaults', exact: true }).click()
    expect((await saved).status()).toBe(200)
    await signIn(page, analyst)
    await page.goto('/start')
    await expect(page).toHaveURL(/\/stats$/)
    await expect(page.getByRole('heading', { name: 'Statistics', exact: true })).toBeVisible()
    await page.goto('/')
    await expect(page.getByRole('status').filter({ hasText: 'Your organization enforces this dashboard arrangement.' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Edit Layout', exact: true })).toBeDisabled()
    await page.goto('/settings/workspace')
    await expect(page.getByLabel(/^Start page/).first()).toBeDisabled()
    await expect(page.getByText('The organization enforces this start page.', { exact: false })).toBeVisible()
    const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
    await info.attach('axe-enforced-workspace', { body: JSON.stringify(accessibility, null, 2), contentType: 'application/json' })
    expect(accessibility.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([])
  } finally {
    // Role policies are global in the disposable installation; restore the exact
    // baseline so later browser cases cannot inherit this scenario's settings.
    await signIn(page, identity)
    const current = await (await page.request.get(`/api/v1${policyPath}`)).json() as WorkspaceRolePolicyResponse
    await writeApi(page, policyPath, 'PUT', {
      expected_revision: current.revision,
      landing_module_id: baseline.landing_module_id,
      landing_mode: baseline.landing_mode,
      dashboard_mode: baseline.dashboard_mode,
      dashboard_view_json: baseline.dashboard_view_json,
      modules: baseline.modules,
      dashboard_panel_ids: baseline.dashboard_panel_ids,
    })
  }
  await signIn(page, analyst)
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Edit Layout', exact: true })).toBeEnabled()
})
