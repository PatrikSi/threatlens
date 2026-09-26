import { test, expect, control, signIn } from './fixtures'

test('article preview consent persists per account and supports temporary overrides', async ({
  page,
  request,
  identity,
}) => {
  const item = await control(request, 'export-item')
  // Only the third-party HTML fetch is replaced. Identity, preference writes,
  // reloads, article inventory and dashboard behavior use the real server.
  await page.route(`**/items/${item.id}/article-preview*`, (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><html lang="en"><title>Preview fixture</title><body><p>Isolated article preview.</p></body></html>',
    }),
  )
  await signIn(page, identity)
  await page.goto('/settings/account')
  const preference = page.getByRole('switch', {
    name: 'Always load external resources in original article previews',
  })
  await expect(preference).toBeEnabled()
  await expect(preference).not.toBeChecked()
  await preference.check()
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith('/workspace/preferences') &&
      response.request().method() === 'PUT',
  )
  await page
    .getByRole('button', { name: 'Save preview preference', exact: true })
    .click()
  expect((await saved).status()).toBe(200)
  await expect(
    page
      .getByRole('status')
      .filter({ hasText: 'will load external resources by default' }),
  ).toBeVisible()
  await page.reload()
  await expect(preference).toBeChecked()

  await page.goto('/')
  const row = page
    .locator('article.tl-dashboard-rss-card')
    .filter({ hasText: item.title })
    .first()
  await row
    .getByRole('button', { name: 'Preview Original', exact: true })
    .click()
  const frame = page.getByTitle(`Original article preview: ${item.title}`, {
    exact: true,
  })
  await expect(frame).toHaveAttribute('src', /external_resources=true$/)
  await expect(frame).toHaveAttribute(
    'sandbox',
    'allow-popups allow-popups-to-escape-sandbox',
  )
  const temporary = page.getByRole('checkbox', {
    name: 'Load external resources for this preview',
    exact: true,
  })
  await temporary.uncheck()
  await expect(frame).toHaveAttribute('src', /\/article-preview$/)
  await page
    .getByRole('button', {
      name: 'Close original article preview',
      exact: true,
    })
    .click()
  await row
    .getByRole('button', { name: 'Preview Original', exact: true })
    .click()
  await expect(temporary).toBeChecked()
  await expect(frame).toHaveAttribute('src', /external_resources=true$/)
  await page
    .getByRole('button', {
      name: 'Close original article preview',
      exact: true,
    })
    .click()

  await page.goto('/settings/account')
  await preference.uncheck()
  await page
    .getByRole('button', { name: 'Save preview preference', exact: true })
    .click()
  await expect(
    page
      .getByRole('status')
      .filter({ hasText: 'will block external resources by default' }),
  ).toBeVisible()
  await page.reload()
  await expect(preference).not.toBeChecked()
  await page.goto('/')
  await row
    .getByRole('button', { name: 'Preview Original', exact: true })
    .click()
  await expect(frame).toHaveAttribute('src', /\/article-preview$/)
})
