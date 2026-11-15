// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { NotificationWebhook, SMTPHook } from '../types/api'
import { webhook, smtpHook, notificationAnalytics, smtpAnalytics } from '../testing/notificationReadFixtures'
import { NotificationsPage } from './NotificationsPage'
import { SMTPIntegrationSettingsPage } from './IntegrationsSettingsPage'
import { deferred, intelButton, mountIntel } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({
  data: { id: 'operator', role: 'admin', access: { permissions: ['read:notifications', 'write:notifications', 'read:integrations', 'write:integrations'] }, features: {} },
  isLoading: false, isError: false, error: null,
}) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
function edit(field: HTMLInputElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function auxiliaryRead(path: string) {
  if (path === '/notifications/analytics') return notificationAnalytics
  if (path === '/integrations/smtp/analytics') return smtpAnalytics
  if (path.includes('/deliveries?')) return { deliveries: [], total: 0, page: 1, page_size: 10 }
  if (path.includes('/test-runs?')) return { runs: [], total: 0, page: 1, page_size: 10 }
  return []
}
function configuration(kind: 'notify' | 'smtp') {
  return kind === 'notify'
    ? { row: webhook, path: '/notifications/webhooks', key: ['notifications', 'webhooks'], field: 'notification-webhook-name', save: 'Save changes', reload: 'Reload saved webhook' }
    : { row: smtpHook, path: '/integrations/smtp/hooks', key: ['integrations', 'smtp', 'hooks'], field: 'smtp-hook-name', save: 'Save destination', reload: 'Reload saved destination' }
}
async function mount(kind: 'notify' | 'smtp') {
  const config = configuration(kind)
  view = await mountIntel(kind === 'notify' ? <NotificationsPage /> : <SMTPIntegrationSettingsPage />)
  if (kind === 'notify') {
    await vi.waitFor(() => expect(view!.host.textContent).toContain(config.row.name))
    act(() => view!.host.querySelector<HTMLButtonElement>('button[aria-pressed="false"]')!.click())
  }
  await vi.waitFor(() => expect(view!.host.querySelector<HTMLInputElement>(`#${config.field}`)?.value).toBe(config.row.name))
  return config
}

it.each(['notify', 'smtp'] as const)('keeps an old %s dirty baseline paused after fresh read until explicit reload', async (kind) => {
  const config = configuration(kind)
  let status = 200
  let row: NotificationWebhook | SMTPHook = config.row
  vi.mocked(apiFetch).mockImplementation((path) => path === config.path
    ? status === 200 ? Promise.resolve([row]) : Promise.reject(new ApiError('Saved collection denied', status, path))
    : Promise.resolve(auxiliaryRead(path)))
  await mount(kind)
  const field = () => view!.host.querySelector<HTMLInputElement>(`#${config.field}`)!
  edit(field(), 'Unsaved operator destination name')
  status = 403
  await act(async () => { await view!.client.invalidateQueries({ queryKey: config.key, exact: true }) })
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Saved collection denied'))
  row = { ...config.row, name: 'Fresh canonical destination' }
  status = 200
  await act(async () => { await view!.client.invalidateQueries({ queryKey: config.key, exact: true }) })
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Fresh canonical destination'))
  expect(field().value).toBe('Unsaved operator destination name')
  expect(intelButton(view!.host, config.save).disabled).toBe(true)
  act(() => intelButton(view!.host, config.reload).click())
  const dialog = document.querySelector('[role=alertdialog]')!
  expect(dialog).not.toBeNull()
  act(() => intelButton(dialog, 'Cancel').click())
  expect(field().value).toBe('Unsaved operator destination name')
  act(() => intelButton(view!.host, config.reload).click())
  act(() => intelButton(document.querySelector('[role=alertdialog]')!, 'Discard changes').click())
  await vi.waitFor(() => expect(field().value).toBe('Fresh canonical destination'))
  expect(intelButton(view!.host, config.save).disabled).toBe(false)
  expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method)).toBe(false)
})

it.each(['notify', 'smtp'] as const)('does not clear an ordinary %s collection read error with a late successful write acknowledgement', async (kind) => {
  const config = configuration(kind)
  let mode: 'good' | 'failed' | 'held' = 'good'
  const response = deferred<NotificationWebhook | SMTPHook>()
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === 'PATCH') return response.promise
    if (path !== config.path) return Promise.resolve(auxiliaryRead(path))
    if (mode === 'failed') return Promise.reject(new ApiError('Canonical destination unavailable', 503, path))
    if (mode === 'held') return new Promise(() => {})
    return Promise.resolve([config.row])
  })
  await mount(kind)
  edit(view!.host.querySelector<HTMLInputElement>(`#${config.field}`)!, 'Submitted destination name')
  act(() => intelButton(view!.host, config.save).click())
  await vi.waitFor(() => expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1))
  mode = 'failed'
  await act(async () => { await view!.client.invalidateQueries({ queryKey: config.key, exact: true }) })
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Canonical destination unavailable'))
  mode = 'held'
  await act(async () => response.resolve({ ...config.row, name: 'Submitted destination name' }))
  await vi.waitFor(() => expect(view!.client.getMutationCache().getAll().some((mutation) => mutation.state.status === 'success')).toBe(true))
  await vi.waitFor(() => expect(view!.host.textContent).not.toContain('Saving...'))
  expect(view!.client.getQueryState(config.key)?.status).toBe('error')
  expect(view!.host.textContent).toContain('Canonical destination unavailable')
  expect(intelButton(view!.host, config.save).disabled).toBe(true)
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1)
})
