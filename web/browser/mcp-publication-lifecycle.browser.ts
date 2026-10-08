import type { Locator, Page } from '@playwright/test'
import { test, expect } from './fixtures'

const request = new URLSearchParams({
  client_id: 'client-a', redirect_uri: 'https://client.example/callback',
  resource: 'https://threatlens.example/api/v1/mcp', state: 'state-1234567890123',
  response_type: 'code', code_challenge_method: 'S256', code_challenge: 'a'.repeat(43),
  scope: 'read:mcp read:items',
})
const preview = { client_name: 'Known MCP client', scopes: ['read:mcp', 'read:items'],
  resource: 'https://threatlens.example/api/v1/mcp', redirect_uri: 'https://client.example/callback', expires_in: 900 }
function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((done) => { resolve = done })
  return { promise, resolve }
}
const consumer = { id: 'consumer-1', name: 'SIEM receiver', expires_at: '2026-12-26T10:00:00Z',
  retired_at: null, revoked_at: null, sequence: 0, replay_floor: 0, generation: 1, last_poll_at: null }

async function openConsumers(page: Page) {
  await page.routeWebSocket('**/*', (socket) => socket.close())
  await page.route('**/api/v1/exports/capabilities', (route) => route.fulfill({ json: {
    formats: [{ id: 'csv', label: 'CSV', extension: '.csv', media_type: 'text/csv', description: 'Articles',
      supports_article_text: true, supports_iocs: true, supports_user_state: true }],
    feeds: [], tags: [], classifications: [], max_items: 10000, max_pdf_items: 500,
    max_uncompressed_bytes: 262144000, preview_limit: 25,
  } }))
  await page.route('**/api/v1/exports/preview', (route) => route.fulfill({ json: {
    total_matches: 1, articles_with_text: 1, items_with_iocs: 1, preview_limit: 25,
    exceeds_export_limit: false, exceeds_pdf_limit: false, items: [],
  } }))
  await page.route('**/api/v1/exports/jobs?*', (route) => route.fulfill({ json: { items: [], has_more: false } }))
  await page.route('**/api/v1/teams?*', (route) => route.fulfill({ json: {
    items: [{ id: 'team-1', name: 'Endpoint team' }], total: 1, page: 1, page_size: 50,
  } }))
  await page.route('**/api/v1/teams/team-1/indicator-publications?*', (route) => route.fulfill({ json: {
    items: [{ id: 'publication-1', team_id: 'team-1', format: 'misp', marking: 'TLP:AMBER',
      status: 'active', revision: 1, indicator_count: 1, withdrawn_count: 0,
      created_at: '2026-09-26T10:00:00Z', updated_at: '2026-09-26T10:00:00Z' }], has_more: false, next_cursor: null,
  } }))
  await page.goto('/export')
  await page.getByRole('button', { name: 'Reviewed team publications' }).click()
  await page.getByRole('combobox', { name: 'Publication team' }).selectOption('team-1')
  await page.getByRole('button', { name: 'Publication consumers', exact: true }).click()
}

async function expectReadableControl(control: Locator) {
  const colors = await control.evaluate((element) => {
    const context = document.createElement('canvas').getContext('2d')!
    const pixel = (value: string) => {
      context.clearRect(0, 0, 1, 1)
      context.fillStyle = value
      context.fillRect(0, 0, 1, 1)
      return [...context.getImageData(0, 0, 1, 1).data]
    }
    const style = getComputedStyle(element)
    const foreground = pixel(style.color)
    const background = pixel(style.backgroundColor)
    // Compositing a translucent dark control over white gives the weakest
    // possible contrast for its light text, independent of panel placement.
    const effectiveBackground = background.slice(0, 3).map((value) => value * background[3] / 255 + 255 * (1 - background[3] / 255))
    const luminance = (channels: number[]) => channels.slice(0, 3).reduce((total, value, index) => {
      const channel = value / 255
      return total + (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][index]
    }, 0)
    const text = luminance(foreground)
    const fill = luminance(effectiveBackground)
    return { foreground, background, ratio: (Math.max(text, fill) + 0.05) / (Math.min(text, fill) + 0.05) }
  })
  expect(colors.ratio, `Control text/background contrast: ${JSON.stringify(colors)}`).toBeGreaterThanOrEqual(4.5)
}

test('dark publication consumer selects retain readable text', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('threatlens.theme', 'dark'))
  await page.route('**/api/v1/teams/team-1/publication-consumers', (route) => route.fulfill({ json: [consumer] }))
  await openConsumers(page)
  await expect(page.locator('html')).toHaveClass(/dark/)
  await page.getByRole('combobox', { name: 'Consumer', exact: true }).selectOption('consumer-1')
  await page.getByRole('combobox', { name: 'Publication', exact: true }).selectOption('publication-1')
  await expectReadableControl(page.getByRole('combobox', { name: 'Consumer', exact: true }))
  await expectReadableControl(page.getByRole('combobox', { name: 'Publication', exact: true }))
})

test('MCP consent rejects duplicated parameters and isolates a replacement request from late approval', async ({ page }) => {
  await page.routeWebSocket('**/*', (socket) => socket.close())
  const pending = deferred()
  const finished = deferred()
  let approvals = 0
  await page.route('**/api/v1/mcp/oauth/consent-preview', (route) => route.fulfill({ json: preview }))
  await page.route('**/api/v1/mcp/oauth/authorize', async (route) => {
    approvals += 1
    expect(route.request().postDataJSON().current_password).toBe('temporary-password')
    await pending.promise
    await route.fulfill({ json: { redirect_uri: 'not-a-valid-callback' } })
    finished.resolve()
  })
  await page.goto(`/mcp/authorize?${request}`)
  await page.getByLabel('Current password (local accounts)').fill('temporary-password')
  await page.getByRole('button', { name: 'Allow read access' }).click()
  await expect.poll(() => approvals).toBe(1)
  await expect(page.getByLabel('Current password (local accounts)')).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Deny', exact: true })).toBeDisabled()
  const replacement = new URLSearchParams(request)
  replacement.set('state', 'replacement-state-123456')
  await page.evaluate((query) => {
    window.history.pushState({}, '', `/mcp/authorize?${query}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, replacement.toString())
  await expect(page.getByLabel('Current password (local accounts)')).toHaveValue('')
  pending.resolve()
  await finished.promise
  await expect(page.getByRole('button', { name: 'Allow read access' })).toBeEnabled()
  await expect(page.getByText(/callback could not be verified/)).toHaveCount(0)
  await page.evaluate((query) => {
    window.history.pushState({}, '', `/mcp/authorize?${query}&client_id=client-a`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, replacement.toString())
  await expect(page.getByRole('alert').filter({ hasText: 'duplicate parameters' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Allow read access' })).toHaveCount(0)
})

test('consumer creation disables edits and hides a late secret after its manager closes', async ({ page }) => {
  const pending = deferred()
  const finished = deferred()
  let created = false
  let requests = 0
  await page.route('**/api/v1/teams/team-1/publication-consumers', async (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: created ? [consumer] : [] })
    requests += 1
    expect(route.request().postDataJSON().name).toBe('SIEM receiver')
    await pending.promise
    created = true
    await route.fulfill({ status: 201, json: { ...consumer, token: 'tlpc_sensitive_late_secret' } })
    finished.resolve()
  })
  await openConsumers(page)
  await page.getByLabel('Consumer name', { exact: true }).fill('SIEM receiver')
  await page.getByRole('button', { name: 'Register consumer' }).click()
  await expect.poll(() => requests).toBe(1)
  await expect(page.getByLabel('Consumer name', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Publication consumers', exact: true }).click()
  pending.resolve()
  await finished.promise
  await page.getByRole('button', { name: 'Publication consumers', exact: true }).click()
  await expect(page.getByText('SIEM receiver', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('tlpc_sensitive_late_secret')).toHaveCount(0)
  await expect(page.getByLabel('Consumer name', { exact: true })).toHaveValue('')
})

test('consumer revocation confirms intent and removes cached controls and secrets after access loss', async ({ page }) => {
  let denied = false
  let revokeRequests = 0
  await page.route('**/api/v1/teams/team-1/publication-consumers', (route) => denied
    ? route.fulfill({ status: 403, json: { detail: 'Team manager access was removed' } })
    : route.fulfill({ json: [consumer] }))
  await page.route('**/api/v1/teams/team-1/publication-consumers/consumer-1/rotate', (route) => route.fulfill({ json: { ...consumer, token: 'tlpc_rotated_secret' } }))
  await page.route('**/api/v1/teams/team-1/publication-consumers/consumer-1', (route) => {
    revokeRequests += 1
    denied = true
    return route.fulfill({ status: 403, json: { detail: 'Team manager access was removed' } })
  })
  await openConsumers(page)
  await page.getByRole('button', { name: 'Rotate credential' }).click()
  await expect(page.getByText('tlpc_rotated_secret')).toBeVisible()
  await page.getByRole('button', { name: 'Revoke credential', exact: true }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Revoke consumer credential: SIEM receiver' })
  await expect(dialog).toBeVisible()
  expect(revokeRequests).toBe(0)
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await page.getByRole('button', { name: 'Revoke credential', exact: true }).click()
  await dialog.getByRole('button', { name: 'Confirm consumer change' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Team manager access was removed' })).toBeVisible()
  await expect(page.getByText('tlpc_rotated_secret')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Register consumer' })).toHaveCount(0)
  await expect(dialog).toHaveCount(0)
  await page.getByRole('button', { name: 'Retry consumer access' }).click()
  await expect(page.getByRole('button', { name: 'Register consumer' })).toHaveCount(0)
  expect(revokeRequests).toBe(1)
})

test('lost registration response retains idempotency and guides recovery by credential rotation', async ({ page }) => {
  const registrations: { idempotency_key: string }[] = []
  await page.route('**/api/v1/teams/team-1/publication-consumers', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: registrations.length ? [consumer] : [] })
    registrations.push(route.request().postDataJSON())
    return registrations.length === 1
      ? route.fulfill({ status: 503, json: { detail: 'Registration response lost' } })
      : route.fulfill({ status: 409, json: { error: { code: 'consumer_already_registered', message: 'Already registered' } } })
  })
  await page.route('**/api/v1/teams/team-1/publication-consumers/consumer-1/rotate', (route) => route.fulfill({ json: { ...consumer, token: 'tlpc_recovered_secret' } }))
  await openConsumers(page)
  await page.getByLabel('Consumer name', { exact: true }).fill('SIEM receiver')
  await page.getByRole('button', { name: 'Register consumer' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Registration response lost' })).toBeVisible()
  await page.getByRole('button', { name: 'Register consumer' }).click()
  await expect(page.getByText(/original token cannot be retrieved/)).toBeVisible()
  expect(registrations).toHaveLength(2)
  expect(registrations[0].idempotency_key).toBe(registrations[1].idempotency_key)
  await expect(page.getByRole('button', { name: 'Register consumer' })).toBeDisabled()
  await page.getByRole('button', { name: 'Refresh consumers' }).click()
  await page.getByRole('button', { name: 'Rotate credential' }).click()
  await expect(page.getByText('tlpc_recovered_secret')).toBeVisible()
})

test('retirement preserves acknowledgement recovery and archive failures remain visible in the confirmation', async ({ page }) => {
  let retiredAt: string | null = null
  let archiveCalls = 0
  let archived = false
  await page.route('**/api/v1/teams/team-1/publication-consumers', (route) => route.fulfill({ json: archived ? [] : [{ ...consumer, retired_at: retiredAt }] }))
  await page.route('**/api/v1/teams/team-1/publication-consumers/consumer-1/retire', (route) => {
    retiredAt = '2026-09-27T00:00:00Z'
    return route.fulfill({ status: 204 })
  })
  await page.route('**/api/v1/teams/team-1/publication-consumers/consumer-1/archive', (route) => {
    archiveCalls += 1
    if (archiveCalls === 1) return route.fulfill({ status: 409, json: { detail: 'Withdrawals are awaiting acknowledgement' } })
    archived = true
    return route.fulfill({ status: 204 })
  })
  await openConsumers(page)
  await expect(page.getByRole('button', { name: 'Archive acknowledged consumer' })).toBeDisabled()
  await page.getByRole('button', { name: 'Retire and withdraw' }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: 'Confirm consumer change' }).click()
  await expect(page.getByRole('button', { name: 'Retire and withdraw' })).toBeDisabled()
  await page.getByRole('button', { name: 'Archive acknowledged consumer' }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Archive consumer: SIEM receiver' })
  await dialog.getByRole('button', { name: 'Confirm consumer change' }).click()
  await expect(dialog.getByRole('alert')).toContainText('Withdrawals are awaiting acknowledgement')
  await dialog.getByRole('button', { name: 'Confirm consumer change' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('No registered consumers.')).toBeVisible()
  await expect(page.getByLabel('Consumer name', { exact: true })).toBeEnabled()
})
