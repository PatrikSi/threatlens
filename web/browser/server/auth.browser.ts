import { test, expect, control, signIn, openEditor, pollSession } from './fixtures'

test('real cookies protect session credentials and reject missing or incorrect CSRF', async ({ page, context, identity }) => {
  await signIn(page, identity)
  const cookies = await context.cookies()
  const session = cookies.find((cookie) => cookie.name === 'threatlens_session')!
  const csrf = cookies.find((cookie) => cookie.name === 'threatlens_csrf')!
  expect(session.httpOnly).toBe(true)
  expect(session.sameSite).toBe('Lax')
  expect(session.secure).toBe(false) // This isolated harness explicitly uses loopback HTTP.
  expect(csrf.httpOnly).toBe(false)
  expect(await page.evaluate(() => document.cookie)).not.toContain('threatlens_session=')
  const feed = (await (await page.request.get('/api/v1/feeds')).json())[0]
  const savedDescription = `Saved by ${identity.id}`
  const writeStatus = await page.evaluate(async ({ csrfToken, feedId, description }) => {
    const response = await fetch(`/api/v1/feeds/${feedId}`, {
      method: 'PATCH', headers: { 'x-csrf-token': csrfToken, 'content-type': 'application/json' },
      body: JSON.stringify({ description }),
    })
    return response.status
  }, { csrfToken: csrf.value, feedId: feed.id, description: savedDescription })
  expect(writeStatus).toBe(200)
  expect((await (await page.request.get('/api/v1/feeds')).json())[0].description).toBe(savedDescription)
  for (const token of [null, 'invalid-token']) {
    const status = await page.evaluate(async (csrfToken) => {
      const response = await fetch('/api/v1/auth/logout', { method: 'POST', headers: csrfToken ? { 'x-csrf-token': csrfToken } : {} })
      return response.status
    }, token)
    expect(status).toBe(403)
    expect((await page.request.get('/api/v1/auth/me')).status()).toBe(200)
  }
  const accepted = await page.evaluate(async (csrfToken) => {
    const response = await fetch('/api/v1/auth/logout', { method: 'POST', headers: { 'x-csrf-token': csrfToken } })
    return response.status
  }, csrf.value)
  expect(accepted).toBe(200)
  expect((await page.request.get('/api/v1/auth/me')).status()).toBe(401)
})

test('real verification outage preserves a dirty editor and database expiry removes it', async ({ page, request, identity }) => {
  await page.clock.install()
  await signIn(page, identity)
  const editor = await openEditor(page)
  const name = editor.getByLabel('Name', { exact: true })
  await name.fill('Draft through real API outage')
  await control(request, 'outage', { unavailable: true })
  await pollSession(page)
  const outage = page.getByRole('dialog', { name: 'Session check unavailable' })
  await expect(outage).toBeVisible()
  await expect(page.locator('#feed-edit-name')).toHaveValue('Draft through real API outage')
  await expect(page.locator('#root')).toHaveAttribute('inert', '')
  await control(request, 'outage', { unavailable: false })
  await outage.getByRole('button', { name: 'Retry session check' }).click()
  await expect(name).toBeFocused()
  await expect(name).toHaveValue('Draft through real API outage')
  expect((await control(request, 'expire', { userId: identity.id })).expired).toBe(1)
  await pollSession(page)
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.locator('#feed-edit-name')).toHaveCount(0)
  expect((await page.request.get('/api/v1/auth/me')).status()).toBe(401)
})

test('a real second-tab login rotates cookies and retires the first account editor', async ({ page, context, request, identity }) => {
  const second = await control(request, 'users')
  await signIn(page, identity)
  await openEditor(page)
  await page.getByLabel('Name', { exact: true }).fill('Private first-account draft')
  const before = (await context.cookies()).find((cookie) => cookie.name === 'threatlens_session')!.value
  const secondTab = await context.newPage()
  await signIn(secondTab, second)
  await expect(page.locator('#feed-edit-name')).toHaveCount(0)
  const current = await page.request.get('/api/v1/auth/me')
  expect((await current.json()).id).toBe(second.id)
  expect((await context.cookies()).find((cookie) => cookie.name === 'threatlens_session')!.value).not.toBe(before)
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(page.getByLabel('Name', { exact: true })).not.toHaveValue('Private first-account draft')
  await secondTab.close()
})

test('real OIDC code, PKCE, JWKS and UserInfo establish the browser session', async ({ page, request }) => {
  const expected = await control(request, 'idp', { mode: 'valid' })
  await page.goto('/login')
  await page.getByRole('button', { name: /Continue with Browser SSO/ }).click()
  await expect(page.getByRole('heading', { name: 'Isolated identity provider' })).toBeVisible()
  expect(new URL(page.url()).searchParams.get('code_challenge_method')).toBe('S256')
  await page.getByRole('link', { name: 'Continue to ThreatLens' }).click()
  await expect(page).not.toHaveURL(/__idp__|\/login/)
  const user = await (await page.request.get('/api/v1/auth/me')).json()
  expect(user.email).toBe(expected.email)
  expect(user.authentication.session_auth_method).toBe('oidc')
  expect(user.role).toBe('viewer')
  const state = await request.get(`${process.env.THREATLENS_BROWSER_API_ORIGIN}/__browser__/idp`, { headers: { 'x-browser-control': process.env.THREATLENS_BROWSER_CONTROL_TOKEN! } })
  expect(await state.json()).toMatchObject({ token_calls: 1, jwks_calls: 1, userinfo_calls: 1, pkce_validated: 1, unconsumed_codes: 0 })
})

for (const mode of ['bad_nonce', 'bad_signature', 'token_unavailable']) {
  test(`real OIDC rejects ${mode} without creating a browser session`, async ({ page, request }) => {
    await control(request, 'idp', { mode })
    await page.goto('/login')
    await page.getByRole('button', { name: /Continue with Browser SSO/ }).click()
    await page.getByRole('link', { name: 'Continue to ThreatLens' }).click()
    await expect(page).toHaveURL(/\/login\?oidc_error=/)
    await expect(page.getByRole('alert')).toBeVisible()
    expect((await page.request.get('/api/v1/auth/me')).status()).toBe(401)
  })
}
