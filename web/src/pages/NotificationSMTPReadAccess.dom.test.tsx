// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { NotificationWebhook, SMTPHook } from '../types/api'
import { notificationAnalytics, notificationDelivery, smtpAnalytics, smtpDelivery, smtpHook, smtpTestRun, webhook } from '../testing/notificationReadFixtures'
import { NotificationsPage } from './NotificationsPage'
import { SMTPIntegrationSettingsPage } from './IntegrationsSettingsPage'
import { deferred, intelButton, mountIntel } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({
  data: { id: 'operator', role: 'admin', access: { permissions: ['read:notifications', 'write:notifications', 'read:integrations', 'write:integrations'] }, features: {} },
  isLoading: false, isError: false, error: null,
}) }))
type Resource = 'notify-hooks' | 'notify-analytics' | 'notify-deliveries' | 'smtp-hooks' | 'smtp-analytics' | 'smtp-deliveries' | 'smtp-tests'
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let failure: { resource: Resource; status: number; page?: number } | undefined
let pendingSave: Promise<NotificationWebhook | SMTPHook> | undefined
let pendingCollection: Promise<NotificationWebhook[]> | undefined

function resourceFor(path: string): Resource | undefined {
  if (path === '/notifications/webhooks') return 'notify-hooks'
  if (path === '/notifications/analytics') return 'notify-analytics'
  if (path.startsWith(`/notifications/webhooks/${webhook.id}/deliveries?`)) return 'notify-deliveries'
  if (path === '/integrations/smtp/hooks') return 'smtp-hooks'
  if (path === '/integrations/smtp/analytics') return 'smtp-analytics'
  if (path.startsWith(`/integrations/smtp/hooks/${smtpHook.id}/deliveries?`)) return 'smtp-deliveries'
  if (path.startsWith(`/integrations/smtp/hooks/${smtpHook.id}/test-runs?`)) return 'smtp-tests'
}
function filters(resource: Resource, page?: number) {
  const queryKey = resource === 'notify-hooks' ? ['notifications', 'webhooks']
    : resource === 'notify-analytics' ? ['notifications', 'analytics']
      : resource === 'notify-deliveries' ? ['notifications', 'webhooks', webhook.id, 'deliveries']
        : resource === 'smtp-hooks' ? ['integrations', 'smtp', 'hooks']
          : resource === 'smtp-analytics' ? ['integrations', 'smtp', 'analytics']
            : ['integrations', 'smtp', 'hooks', smtpHook.id, resource === 'smtp-tests' ? 'test-runs' : 'deliveries']
  return { queryKey, exact: ['notify-hooks', 'notify-analytics', 'smtp-hooks', 'smtp-analytics'].includes(resource),
    predicate: (query: { queryKey: readonly unknown[] }) => page === undefined || query.queryKey[5] === page }
}
async function rejectRead(resource: Resource, status: number, page?: number) {
  failure = { resource, status, page }
  const match = filters(resource, page)
  await act(async () => { await view!.client.invalidateQueries(match) })
  await vi.waitFor(() => expect((view!.client.getQueryCache().find(match)?.state.error as ApiError)?.status).toBe(status))
  await vi.waitFor(() => expect(view!.host.textContent).toContain(`${resource} unavailable ${status}`))
}
function input(id: string) { return view!.host.querySelector<HTMLInputElement>(`#${id}`) }
function edit(id: string, value: string) {
  const field = input(id)!
  expect(field).not.toBeNull()
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function mountNotification(select = true) {
  view = await mountIntel(<NotificationsPage />)
  if (!select) return
  await vi.waitFor(() => expect(view!.host.textContent).toContain(webhook.name))
  act(() => view!.host.querySelector<HTMLButtonElement>('button[aria-pressed="false"]')!.click())
  await vi.waitFor(() => expect(input('notification-webhook-name')?.value).toBe(webhook.name))
}
async function mountSMTP() {
  view = await mountIntel(<SMTPIntegrationSettingsPage />)
  await vi.waitFor(() => expect(input('smtp-hook-name')?.value).toBe(smtpHook.name))
}
function writes() { return vi.mocked(apiFetch).mock.calls.filter(([, init]) => ['POST', 'PATCH', 'DELETE'].includes(init?.method ?? '')) }

beforeEach(() => {
  failure = undefined
  pendingSave = undefined
  pendingCollection = undefined
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === 'PATCH') return (pendingSave ?? Promise.resolve(path.startsWith('/notifications') ? webhook : smtpHook)) as never
    if (init?.method === 'POST') return Promise.resolve({ ...webhook, id: 'created-webhook', ...JSON.parse(String(init.body)) }) as never
    if (init?.method === 'DELETE') return Promise.resolve(undefined) as never
    const resource = resourceFor(path)
    const page = Number(new URL(path, 'https://local.test').searchParams.get('page') ?? 1)
    if (failure && resource === failure.resource && (failure.page === undefined || failure.page === page)) return Promise.reject(new ApiError(`${resource} unavailable ${failure.status}`, failure.status, path))
    if (resource === 'notify-hooks') return (pendingCollection ?? Promise.resolve([webhook])) as never
    if (resource === 'notify-analytics') return Promise.resolve(notificationAnalytics) as never
    if (resource === 'notify-deliveries') return Promise.resolve({ deliveries: [notificationDelivery], total: 1, page: 1, page_size: 10 }) as never
    if (resource === 'smtp-hooks') return Promise.resolve([smtpHook]) as never
    if (resource === 'smtp-analytics') return Promise.resolve(smtpAnalytics) as never
    if (resource === 'smtp-deliveries') return Promise.resolve({ deliveries: [{ ...smtpDelivery, id: `${smtpDelivery.id}-${page}`, last_error_message: page === 1 ? smtpDelivery.last_error_message : 'Second SMTP delivery diagnostic' }], total: 20, page, page_size: 10 }) as never
    if (resource === 'smtp-tests') return Promise.resolve({ runs: [{ ...smtpTestRun, id: `${smtpTestRun.id}-${page}`, recipient_email: page === 1 ? smtpTestRun.recipient_email : 'second-test-recipient@example.test' }], total: 20, page, page_size: 10 }) as never
    return Promise.resolve([]) as never
  })
})
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

describe('notification and email actual cache read authority', () => {
  it.each(['notify-hooks', 'notify-analytics', 'notify-deliveries', 'smtp-hooks', 'smtp-analytics'] as const)('keeps denied %s metadata hidden through an outage until a fresh read', async (resource) => {
    const notify = resource.startsWith('notify')
    if (notify) await mountNotification()
    else await mountSMTP()
    const marker = resource === 'notify-hooks' ? webhook.name : resource === 'notify-analytics' ? 'Private failing webhook'
      : resource === 'notify-deliveries' ? notificationDelivery.item_title! : resource === 'smtp-hooks' ? smtpHook.name : 'Private failing email destination'
    await vi.waitFor(() => expect(view!.host.textContent).toContain(marker))
    for (const status of [403, 503]) {
      await rejectRead(resource, status)
      expect(view!.host.textContent).not.toContain(marker)
    }
    failure = undefined
    await act(async () => { await view!.client.invalidateQueries(filters(resource)) })
    await vi.waitFor(() => expect(view!.host.textContent).toContain(marker))
    expect(writes()).toHaveLength(0)
  })

  it.each(['notify', 'smtp'] as const)('does not keep an untouched saved %s draft after its collection read is denied', async (kind) => {
    if (kind === 'notify') await mountNotification()
    else await mountSMTP()
    const id = kind === 'notify' ? 'notification-webhook-url' : 'smtp-host'
    const privateValue = kind === 'notify' ? webhook.url_template : smtpHook.host
    expect(input(id)?.value).toBe(privateValue)
    for (const status of [403, 503]) {
      await rejectRead(kind === 'notify' ? 'notify-hooks' : 'smtp-hooks', status)
      expect(input(id)?.value).not.toBe(privateValue)
    }
    expect(writes()).toHaveLength(0)
  })

  it.each([['notify', 403], ['notify', 503], ['smtp', 403], ['smtp', 503]] as const)('preserves an edited %s draft and pauses its save during read status%s', async (kind, status) => {
    if (kind === 'notify') await mountNotification()
    else await mountSMTP()
    const id = kind === 'notify' ? 'notification-webhook-name' : 'smtp-hook-name'
    edit(id, 'Unsaved operator destination name')
    await rejectRead(kind === 'notify' ? 'notify-hooks' : 'smtp-hooks', status)
    if (status === 403) {
      expect(input(id)).toBeNull()
      failure = undefined
      await act(async () => { await view!.client.invalidateQueries(filters(kind === 'notify' ? 'notify-hooks' : 'smtp-hooks')) })
      await vi.waitFor(() => expect(input(id)?.value).toBe('Unsaved operator destination name'))
    }
    expect(input(id)?.value).toBe('Unsaved operator destination name')
    const save = intelButton(view!.host, kind === 'notify' ? 'Save changes' : 'Save destination')
    expect(save.disabled).toBe(true)
    act(() => save.click())
    expect(writes()).toHaveLength(0)
  })

  it.each([403, 503])('retires or pauses a webhook retry confirmation after its history read status%s', async (status) => {
    await mountNotification()
    await vi.waitFor(() => expect(view!.host.textContent).toContain(notificationDelivery.item_title!))
    act(() => {
      const retry = intelButton(view!.host, 'Retry failed delivery')
      retry.closest('details')!.open = true
      retry.click()
    })
    expect(document.querySelector('[role=alertdialog]')).not.toBeNull()
    await rejectRead('notify-deliveries', status)
    if (status === 403) expect(document.querySelector('[role=alertdialog]')).toBeNull()
    else {
      const confirm = intelButton(document.querySelector('[role=alertdialog]')!, 'Retry delivery')
      expect(confirm.disabled).toBe(true)
      act(() => confirm.click())
    }
    expect(writes()).toHaveLength(0)
  })

  it.each(['smtp-deliveries', 'smtp-tests'] as const)('keeps %s page1 hidden after page2 denial and same-client remount', async (resource) => {
    await mountSMTP()
    if (resource === 'smtp-tests') act(() => intelButton(view!.host, 'Tests').click())
    const panelId = resource === 'smtp-tests' ? 'smtp-tests-panel' : 'smtp-deliveries-panel'
    const firstMarker = resource === 'smtp-tests' ? smtpTestRun.recipient_email! : smtpDelivery.last_error_message!
    await vi.waitFor(() => expect(view!.host.textContent).toContain(firstMarker))
    act(() => intelButton(view!.host.querySelector(`#${panelId}`)!, 'Next').click())
    await vi.waitFor(() => expect(view!.host.textContent).toContain(resource === 'smtp-tests' ? 'second-test-recipient@example.test' : 'Second SMTP delivery diagnostic'))
    await rejectRead(resource, 403, 2)
    failure = { resource, status: 503 }
    await act(async () => { await view!.router.navigate('/other') })
    await vi.waitFor(() => expect(view!.host.textContent).toContain('Other page'))
    await act(async () => { await view!.router.navigate('/') })
    await vi.waitFor(() => expect(view!.host.querySelector('button[aria-pressed="true"]')?.textContent).toContain(smtpHook.name))
    if (resource === 'smtp-tests') act(() => intelButton(view!.host, 'Tests').click())
    await vi.waitFor(() => expect(view!.host.querySelector(`#${panelId}`)).not.toBeNull())
    expect(view!.host.textContent).not.toContain(firstMarker)
    await vi.waitFor(() => expect(view!.host.textContent).toContain(`${resource} unavailable 503`))
    expect(view!.host.textContent).not.toContain(firstMarker)
    expect(writes()).toHaveLength(0)
  })

  it.each([403, 503])('retires or pauses an email replay confirmation after its history read status%s', async (status) => {
    await mountSMTP()
    await vi.waitFor(() => expect(view!.host.textContent).toContain(smtpDelivery.last_error_message!))
    act(() => {
      const replay = intelButton(view!.host, 'Replay dead-letter delivery')
      replay.closest('details')!.open = true
      replay.click()
    })
    expect(document.querySelector('[role=alertdialog]')).not.toBeNull()
    await rejectRead('smtp-deliveries', status, 1)
    if (status === 403) expect(document.querySelector('[role=alertdialog]')).toBeNull()
    else {
      const confirm = intelButton(document.querySelector('[role=alertdialog]')!, 'Replay delivery')
      expect(confirm.disabled).toBe(true)
      act(() => confirm.click())
    }
    expect(writes()).toHaveLength(0)
  })

  it.each(['notify', 'smtp'] as const)('does not admit a late %s save response after collection denial', async (kind) => {
    if (kind === 'notify') await mountNotification()
    else await mountSMTP()
    const id = kind === 'notify' ? 'notification-webhook-name' : 'smtp-hook-name'
    edit(id, 'Unsaved operator destination name')
    const pending = deferred<NotificationWebhook | SMTPHook>()
    pendingSave = pending.promise
    act(() => intelButton(view!.host, kind === 'notify' ? 'Save changes' : 'Save destination').click())
    await vi.waitFor(() => expect(writes()).toHaveLength(1))
    await rejectRead(kind === 'notify' ? 'notify-hooks' : 'smtp-hooks', 403)
    await act(async () => pending.resolve({ ...(kind === 'notify' ? webhook : smtpHook), name: 'Private late server destination' }))
    await vi.waitFor(() => expect(view!.client.getMutationCache().getAll().some((mutation) => mutation.state.status === 'success')).toBe(true))
    await vi.waitFor(() => expect(view!.host.textContent).not.toContain('Saving...'))
    expect(view!.host.textContent).not.toContain('Private late server destination')
    expect(input(id)).toBeNull()
    failure = undefined
    await act(async () => { await view!.client.invalidateQueries(filters(kind === 'notify' ? 'notify-hooks' : 'smtp-hooks')) })
    await vi.waitFor(() => expect(input(id)?.value).toBe('Unsaved operator destination name'))
    expect(view!.host.textContent).not.toContain('Private late server destination')
    expect(intelButton(view!.host, kind === 'notify' ? 'Save changes' : 'Save destination').disabled).toBe(true)
    expect(writes()).toHaveLength(1)
  })

  it('admits an explicitly authored new webhook while its initial collection read remains pending', async () => {
    pendingCollection = deferred<NotificationWebhook[]>().promise
    await mountNotification(false)
    await vi.waitFor(() => expect(input('notification-webhook-name')).not.toBeNull())
    edit('notification-webhook-name', 'New operator destination')
    edit('notification-webhook-url', 'https://new-operator.example.test/receiver')
    act(() => intelButton(view!.host, 'Create webhook').click())
    await vi.waitFor(() => expect(writes().some(([path, init]) => path === '/notifications/webhooks' && init?.method === 'POST')).toBe(true))
  })
})
