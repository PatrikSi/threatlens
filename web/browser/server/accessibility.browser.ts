import AxeBuilder from '@axe-core/playwright'
import type { Page, TestInfo } from '@playwright/test'
import { test, expect, signIn, openEditor } from './fixtures'

async function checkAccessibility(page: Page, info: TestInfo, state: string) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  await info.attach(`axe-${state}`, { body: JSON.stringify(results, null, 2), contentType: 'application/json' })
  expect(results.violations.map(({ id, help, nodes }) => ({ id, help, targets: nodes.map((node) => node.target) }))).toEqual([])
}

test('login and rejected credentials expose semantic labels and announced errors', async ({ page, identity }, info) => {
  await page.goto('/login')
  await expect(page.getByRole('button', { name: /Continue with Browser SSO/ })).toBeVisible()
  await checkAccessibility(page, info, 'login')
  await page.getByLabel('Email', { exact: true }).fill(identity.email)
  await page.getByLabel('Password', { exact: true }).fill('Deliberately incorrect password')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText(/sign in|credentials|password/i)
  await expect(page.getByLabel('Email', { exact: true })).toHaveValue(identity.email)
  await checkAccessibility(page, info, 'login-error')
})

test('feed editor and account settings support keyboard focus and accessible forms', async ({ page, identity }, info) => {
  await signIn(page, identity)
  await page.goto('/feeds')
  const edit = page.getByRole('button', { name: 'Edit', exact: true })
  await expect(edit).toBeVisible()
  await checkAccessibility(page, info, 'feeds')
  await edit.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'Real-server fixture feed' })
  await expect(dialog).toBeVisible()
  await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true)
  const name = dialog.getByLabel('Name', { exact: true })
  for (let step = 0; step < 12 && !await name.evaluate((element) => element === document.activeElement); step += 1) {
    await page.keyboard.press('Tab')
  }
  await expect(name).toBeFocused()
  await page.keyboard.press('Tab')
  await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true)
  await checkAccessibility(page, info, 'feed-editor')
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(edit).toBeFocused()
  await page.goto('/settings/account')
  await expect(page.getByRole('heading', { name: /Account/i }).first()).toBeVisible()
  await checkAccessibility(page, info, 'account')
})

test('dark mode retains accessible contrast and names in the feed editor', async ({ page, identity }, info) => {
  await page.addInitScript(() => localStorage.setItem('threatlens.theme', 'dark'))
  await signIn(page, identity)
  const dialog = await openEditor(page)
  await expect(dialog).toBeVisible()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await checkAccessibility(page, info, 'dark-feed-editor')
})
