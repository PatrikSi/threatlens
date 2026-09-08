import { test, expect, openFeedEditor, revalidateSession } from './fixtures'

test('retains feed draft through session outage and returns keyboard focus after recovery', async ({ page, api }) => {
  const editor = await openFeedEditor(page)
  const name = editor.getByLabel('Name', { exact: true })
  await name.fill('Draft preserved during outage')
  api.sessionStatus = 503
  await revalidateSession(page)
  const verification = page.getByRole('dialog', { name: 'Session check unavailable' })
  await expect(verification).toBeVisible()
  await expect(page.locator('#feed-edit-name')).toHaveValue('Draft preserved during outage')
  await expect(page.locator('#feed-edit-name')).toHaveJSProperty('isConnected', true)
  await expect(page.locator('#root')).toHaveAttribute('inert', '')
  await page.keyboard.press('Escape')
  await expect(verification).toBeVisible()
  expect(api.writes).toEqual([])
  api.sessionStatus = 200
  await verification.getByRole('button', { name: 'Retry session check' }).click()
  await expect(verification).toBeHidden()
  await expect(name).toHaveValue('Draft preserved during outage')
  await expect(name).toBeFocused()
  await name.fill('Editing works after recovery')
})

test('browser Back discards nested feed dialogs and leaves destination usable', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Feeds', exact: true }).first().click()
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Unsaved feed')
  await page.goBack()
  const discard = page.getByRole('alertdialog', { name: 'Discard unsaved changes?' })
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: 'Discard changes', exact: true }).focus()
  await page.keyboard.press('Tab')
  await expect(discard.getByRole('button', { name: 'Close dialog' })).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(discard.getByRole('button', { name: 'Discard changes', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(discard).toBeHidden()
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Unsaved feed')
  await page.goBack()
  await discard.getByRole('button', { name: 'Discard changes', exact: true }).click()
  await expect(page).toHaveURL('/')
  await expect(page.locator('#root')).not.toHaveAttribute('inert', '')
  await page.getByRole('link', { name: 'Feeds', exact: true }).first().click()
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(page.getByLabel('Name', { exact: true })).toBeEditable()
})

test('confirmed expiry removes the draft and opens sign in', async ({ page, api }) => {
  await openFeedEditor(page)
  await page.getByLabel('Name', { exact: true }).fill('Private old-session draft')
  api.sessionStatus = 401
  await revalidateSession(page)
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.locator('#feed-edit-name')).toHaveCount(0)
  await expect(page.locator('#root')).not.toHaveAttribute('inert', '')
})

test('cross-tab identity changes retire the old editor and cached feed', async ({ page, api }) => {
  await openFeedEditor(page)
  await page.getByLabel('Name', { exact: true }).fill('Private account A draft')
  api.identity = { ...api.identity, id: 'second-analyst', email: 'second@example.test' }
  await page.evaluate(() => {
    const event = JSON.stringify({ id: 'test-other-tab-login', at: Date.now() })
    window.dispatchEvent(new StorageEvent('storage', { key: 'threatlens.auth.sync', newValue: event }))
  })
  await expect(page.locator('#feed-edit-name')).toHaveCount(0)
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(page.getByLabel('Name', { exact: true })).not.toHaveValue('Private account A draft')
})
