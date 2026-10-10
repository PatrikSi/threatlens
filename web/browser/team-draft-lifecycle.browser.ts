import AxeBuilder from '@axe-core/playwright'
import { test, expect } from './fixtures'

test('protects team settings with a second clean editor and restores keyboard navigation after cancel', async ({ page, api }) => {
  api.identity.role = 'admin'
  api.identity.access = { ...api.identity.access, permissions: ['*:*', 'read:iam', 'write:iam', 'write:ai'] }
  const team = {
    id: 'team-1', key: 'soc', name: 'SOC', description: 'Original', membership_group_id: 'group-1', manager_group_id: 'group-2',
    active: true, revision: 1, can_manage: true, created_at: '2026-09-27T00:00:00Z', updated_at: '2026-09-27T00:00:00Z',
  }
  const policy = {
    team_id: team.id, version: 1, configured: true, approved_provider_keys: ['legacy'], selected_provider_key: 'legacy',
    label_destinations: {}, destinations: [{ key: 'legacy', name: 'Legacy provider settings', available: true }], can_manage: true, can_approve: true,
  }
  const warnings: string[] = []
  page.on('console', (message) => { if (message.text().includes('blocker')) warnings.push(message.text()) })
  await page.route('**/api/v1/teams/admin?*', (route) => route.fulfill({ json: { items: [team], total: 1, page: 1, page_size: 25 } }))
  await page.route('**/api/v1/teams/admin/team-1', (route) => route.fulfill({ json: team }))
  await page.route('**/api/v1/teams?*', (route) => route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 25 } }))
  await page.route('**/api/v1/ai/team-governance/team-1', (route) => route.fulfill({ json: policy }))
  await page.route('**/api/v1/ai/providers?*', (route) => route.fulfill({ json: { items: [], total: 0 } }))
  await page.route('**/api/v1/iam/groups', (route) => route.fulfill({ json: [
    { id: 'group-1', name: 'SOC members', is_system: false }, { id: 'group-2', name: 'SOC managers', is_system: false },
  ] }))
  await page.route('**/api/v1/iam/data-policies', (route) => route.fulfill({ json: { labels: [] } }))
  await page.goto('/teams?mode=admin&team=team-1')
  const name = page.getByLabel('Name', { exact: true })
  await name.fill('Unsaved SOC')
  await expect(page.getByRole('button', { name: 'Approve destination policy' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Member group', exact: true })).toContainText('SOC members')
  await page.getByRole('link', { name: 'My teams', exact: true }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Discard unsaved changes?' })
  await expect(dialog).toBeVisible()
  await expect(page).toHaveURL(/mode=admin/)
  await expect(page.getByRole('alertdialog')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(name).toHaveValue('Unsaved SOC')
  await name.focus()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('textbox', { name: 'Description', exact: true })).toBeFocused()
  const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  expect(audit.violations).toEqual([])
  await page.getByRole('link', { name: 'My teams', exact: true }).click()
  await dialog.getByRole('button', { name: 'Discard changes', exact: true }).click()
  await expect(page).toHaveURL(/\/teams$/)
  expect(warnings).toEqual([])
})
