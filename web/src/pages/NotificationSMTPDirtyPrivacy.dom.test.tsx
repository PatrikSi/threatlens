// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { notificationAnalytics, smtpAnalytics, smtpHook, webhook } from '../testing/notificationReadFixtures'
import { SMTPIntegrationSettingsPage } from './IntegrationsSettingsPage'
import { NotificationsPage } from './NotificationsPage'
import { intelButton, mountIntel } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({
  data: { id: 'operator', role: 'admin', access: { permissions: ['read:notifications', 'write:notifications', 'read:integrations', 'write:integrations'] }, features: {} },
  isLoading: false, isError: false, error: null,
}) }))

let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

function privateDOM() {
  return `${view!.host.textContent}\n${[...view!.host.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea')].map((field) => field.value).join('\n')}`
}
function edit(field: HTMLInputElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

it.each(['notify', 'smtp'] as const)('hides untouched saved %s fields during denial while retaining the actual name edit for recovery', async (kind) => {
  const notification = {
    ...webhook,
    headers: [{ key: 'X-Private-Route', value: 'private-saved-webhook-header' }],
  }
  const email = {
    ...smtpHook,
    from_email: 'private-saved-sender@example.test',
  }
  const row = kind === 'notify' ? notification : email
  const path = kind === 'notify' ? '/notifications/webhooks' : '/integrations/smtp/hooks'
  const key = kind === 'notify' ? ['notifications', 'webhooks'] : ['integrations', 'smtp', 'hooks']
  const nameId = kind === 'notify' ? 'notification-webhook-name' : 'smtp-hook-name'
  const markers = kind === 'notify'
    ? [notification.url_template, notification.headers[0].value]
    : [email.host!, email.from_email!]
  let status = 200
  vi.mocked(apiFetch).mockImplementation((request) => {
    if (request === path) return status === 200 ? Promise.resolve([row])
      : Promise.reject(new ApiError(`Saved destination read ${status}`, status, request))
    if (request === '/notifications/analytics') return Promise.resolve(notificationAnalytics)
    if (request === '/integrations/smtp/analytics') return Promise.resolve(smtpAnalytics)
    if (request.includes('/deliveries?')) return Promise.resolve({ deliveries: [], total: 0, page: 1, page_size: 10 })
    if (request.includes('/test-runs?')) return Promise.resolve({ runs: [], total: 0, page: 1, page_size: 10 })
    return Promise.resolve([])
  })
  view = await mountIntel(kind === 'notify' ? <NotificationsPage /> : <SMTPIntegrationSettingsPage />)
  if (kind === 'notify') {
    await vi.waitFor(() => expect(view!.host.textContent).toContain(row.name))
    act(() => view!.host.querySelector<HTMLButtonElement>('button[aria-pressed="false"]')!.click())
  }
  const name = () => view!.host.querySelector<HTMLInputElement>(`#${nameId}`)
  await vi.waitFor(() => expect(name()?.value).toBe(row.name))
  for (const marker of markers) expect(privateDOM()).toContain(marker)
  edit(name()!, 'Actual unsent destination name')
  expect(name()?.value).toBe('Actual unsent destination name')
  for (const marker of markers) expect(privateDOM()).toContain(marker)

  for (const next of [403, 503]) {
    status = next
    await act(async () => { await view!.client.invalidateQueries({ queryKey: key, exact: true }) })
    await vi.waitFor(() => expect(view!.host.textContent).toContain(`Saved destination read ${next}`))
    for (const marker of markers) expect(privateDOM()).not.toContain(marker)
  }

  status = 200
  await act(async () => { await view!.client.invalidateQueries({ queryKey: key, exact: true }) })
  await vi.waitFor(() => expect(name()?.value).toBe('Actual unsent destination name'))
  expect(intelButton(view!.host, kind === 'notify' ? 'Save changes' : 'Save destination').disabled).toBe(true)
  expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method)).toBe(false)
})
