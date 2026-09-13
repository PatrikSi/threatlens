import { test as base, expect, type Page, type APIRequestContext } from '@playwright/test'

type Identity = { id: string; email: string; password: string }
const apiOrigin = process.env.THREATLENS_BROWSER_API_ORIGIN!
const webOrigin = process.env.THREATLENS_BROWSER_BASE_URL!
export async function control(request: APIRequestContext, path: string, data?: unknown) {
  const response = await request.post(`${apiOrigin}/__browser__/${path}`, {
    headers: { 'x-browser-control': process.env.THREATLENS_BROWSER_CONTROL_TOKEN! }, data,
  })
  expect(response.ok(), `Isolated control ${path}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

export const test = base.extend<{ identity: Identity }>({
  identity: async ({ request }, use) => {
    const identity = await control(request, 'users')
    try { await use(identity) } finally { await control(request, 'outage', { unavailable: false }) }
  },
  context: async ({ context }, use) => {
    const external: string[] = []
    await context.route('**/*', async (route) => {
      const origin = new URL(route.request().url()).origin
      if (origin === webOrigin || origin === apiOrigin) return route.continue()
      external.push(route.request().url())
      return route.abort()
    })
    await use(context)
    expect(external, 'Real-server cases must use only the disposable application and IdP').toEqual([])
  },
})
export { expect }

export async function signIn(page: Page, identity: Identity) {
  await page.goto('/login')
  await page.getByLabel('Email', { exact: true }).fill(identity.email)
  await page.getByLabel('Password', { exact: true }).fill(identity.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login(?:\?|$)/)
}

export async function openEditor(page: Page) {
  await page.goto('/feeds')
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  return page.getByRole('dialog', { name: 'Real-server fixture feed' })
}

export async function pollSession(page: Page) {
  await page.clock.fastForward(31_000)
  await page.clock.runFor(1_500)
}
