import { test as base, expect, type Page } from '@playwright/test'

export const user = {
  id: 'browser-analyst', email: 'analyst@example.test', role: 'analyst',
  is_active: true, is_approved: true, approved_at: null, created_at: '2026-09-08T00:00:00Z',
  access: { permissions: ['*:*'], account_eligible: true },
  features: { ai_enabled: false, ai_configured: false, ai_summary_enabled: false, ai_relevance_enabled: false, ai_daily_brief_enabled: false },
}
export const feed = {
  id: 'browser-feed', name: 'Browser fixture feed', url: 'https://source.example.test/rss',
  description: '', site_url: '', language: 'en', enabled: true, has_unreadable_url: false,
  fetch_interval_minutes: 30, fetch_interval_seconds: 1800, fetch_mode: 'interval', schedule_cron: null,
  created_at: '2026-09-08T00:00:00Z', last_fetch_at: null, last_success_at: null,
}

type ApiState = { sessionStatus: number; identity: typeof user; writes: string[] }
const developmentOrigin = 'http://127.0.0.1:4173'
export const test = base.extend<{ api: ApiState; appOrigin: string }>({
  appOrigin: [developmentOrigin, { option: true }],
  api: [async ({ page, appOrigin }, use) => {
    await page.clock.install()
    const state: ApiState = { sessionStatus: 200, identity: { ...user }, writes: [] }
    const unexpected: string[] = []
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url())
      if (url.origin === appOrigin && !url.pathname.startsWith('/api/')) {
        if (appOrigin === developmentOrigin) return route.continue()
        // Serve the real app at a non-localhost HTTP origin without DNS or a live API.
        const response = await route.fetch({
          url: `${developmentOrigin}${url.pathname}${url.search}`,
          headers: { ...route.request().headers(), host: new URL(developmentOrigin).host },
        })
        return route.fulfill({ response })
      }
      if (url.origin !== appOrigin) {
        unexpected.push(url.toString())
        return route.abort()
      }
      const path = url.pathname.replace('/api/v1', '')
      if (route.request().method() !== 'GET') state.writes.push(path)
      if (path === '/auth/me') return route.fulfill({ status: state.sessionStatus, json: state.sessionStatus === 200 ? state.identity : { detail: 'Session verification unavailable' } })
      if (path.startsWith('/workspace/')) return route.fulfill({ status: 503, json: { detail: 'Fixture uses trusted workspace fallback' } })
      if (path === '/feeds') return route.fulfill({ json: [feed] })
      if (path === '/items') return route.fulfill({ json: { items: [], total: 0, limit: 100, offset: 0, has_more: false } })
      if (['/views', '/tags', '/alerts', '/ai/daily-briefs'].includes(path)) return route.fulfill({ json: [] })
      if (path === '/alerts/matches') return route.fulfill({ json: { matches: [], total: 0, has_more: false } })
      if (path === '/auth/registration-settings') return route.fulfill({ json: { registration_enabled: false } })
      if (path === '/auth/oidc/settings') return route.fulfill({ json: { enabled: false } })
      unexpected.push(path)
      return route.fulfill({ status: 501, json: { detail: `Missing browser fixture: ${path}` } })
    })
    await use(state)
    expect(unexpected, 'Browser tests must not reach a live API or external host').toEqual([])
  }, { auto: true }],
})
export { expect }

export async function openFeedEditor(page: Page) {
  await page.goto('/feeds')
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  const editor = page.getByRole('dialog', { name: feed.name })
  await expect(editor).toBeVisible()
  return editor
}

export async function revalidateSession(page: Page) {
  await page.clock.fastForward(31_000)
  await page.clock.runFor(1_500)
}
