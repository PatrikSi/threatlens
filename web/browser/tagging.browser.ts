import { test, expect } from './fixtures'

const rule = {
  id: 'browser-rule', name: 'VPN disclosures', tag_name: 'vpn', pattern: 'vpn', enabled: true,
  match_type: 'contains', case_sensitive: false, applies_to: ['title'], required_categories: [],
  feed_scope: 'all', feed_ids: [], min_classification_confidence: null,
  created_at: '2026-09-08T00:00:00Z', updated_at: '2026-09-08T00:00:00Z',
}
const settings = {
  id: 'settings', enabled_categories: ['vulnerability'], min_auto_tag_confidence: 0.45,
  secondary_tag_limit: 2, created_at: rule.created_at, updated_at: rule.updated_at,
}

test('keeps later rule edits, rejects stale previews, and protects navigation after saving', async ({ page }) => {
  let saved = rule
  let finishSave!: () => void
  let finishPreview!: () => void
  const saving = new Promise<void>((resolve) => { finishSave = resolve })
  const previewing = new Promise<void>((resolve) => { finishPreview = resolve })
  await page.route('**/api/v1/tagging/settings', (route) => route.fulfill({ json: { settings, rules: [saved] } }))
  await page.route('**/api/v1/tagging/rules/browser-rule', async (route) => {
    await saving
    saved = { ...rule, name: 'Submitted rule' }
    await route.fulfill({ json: saved })
  })
  await page.route('**/api/v1/tagging/rules/preview', async (route) => {
    await previewing
    await route.fulfill({ json: { total: 9, items: [] } })
  })
  try {
    await page.goto('/settings/tagging')
    await page.getByRole('button', { name: /VPN disclosures/ }).click()
    const name = page.getByLabel('Rule name', { exact: true })
    await name.fill('Submitted rule')
    const save = page.getByRole('button', { name: 'Save rule', exact: true })
    await save.click()
    await expect(save).toBeDisabled()
    await name.fill('Later unsaved rule')
    finishSave()
    await expect(save).toBeEnabled()
    await expect(name).toHaveValue('Later unsaved rule')
    const preview = page.getByRole('button', { name: 'Preview rule', exact: true })
    await preview.click()
    await expect(preview).toBeDisabled()
    await page.getByLabel('Pattern', { exact: true }).fill('changed after preview')
    finishPreview()
    await expect(preview).toBeEnabled()
    await expect(page.getByText('Run a preview to inspect recent matches and affected items.')).toBeVisible()
    await expect(page.getByText('9 preview matches')).toHaveCount(0)
    await page.getByRole('link', { name: 'Feeds', exact: true }).first().click()
    const discard = page.getByRole('alertdialog', { name: 'Discard unsaved changes?' })
    await expect(discard).toBeVisible()
    await discard.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(name).toHaveValue('Later unsaved rule')
    await page.getByRole('link', { name: 'Feeds', exact: true }).first().click()
    await discard.getByRole('button', { name: 'Discard changes', exact: true }).click()
    await expect(page).toHaveURL('/feeds')
  } finally { finishSave(); finishPreview() }
})
