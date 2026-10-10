// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { WebhookAutomationFields } from './WebhookAutomationFields'
import { WebhookCredentialProfiles } from './WebhookCredentialProfiles'
import type { NotificationWebhooksController } from './useNotificationWebhooksController'
import { createDefaultDraft } from './notificationWebhookDraft'
import type { WebhookCredentialProfile, WebhookMatchPreview } from '../types/webhookAutomation'
import { automationField, editAutomation } from './indicatorAutomationTestSupport'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

function PreviewHarness() {
  const [draft, setDraft] = useState(() => ({ ...createDefaultDraft(), name: 'SIEM', url_template: 'https://receiver.test', payload_mode: 'automation_v1' as const }))
  const controller = { draft, setDraft, canManageWebhooks: true, currentUserQuery: { isError: false }, saveWebhook: { isPending: false } } as unknown as NotificationWebhooksController
  return <WebhookAutomationFields controller={controller} />
}
describe('webhook preview lifecycle', () => {
  it('sends only to the preview API and hides a late result after the subscription changes', async () => {
    const pending = deferred<WebhookMatchPreview>()
    vi.mocked(apiFetch).mockImplementation((url) => {
      if (url === '/notifications/webhooks/preview') return pending.promise as ReturnType<typeof apiFetch>
      return Promise.resolve(url.includes('/events?') ? { events: [{ id: 'event-1', event_type: 'rss_item_new', label: 'Article event', created_at: '2026-09-26T00:00:00Z' }] } : []) as ReturnType<typeof apiFetch>
    })
    view = await mountIntel(<PreviewHarness />)
    const details = view.host.querySelector('details')!
    act(() => { details.open = true; details.dispatchEvent(new Event('toggle')) })
    await settle()
    editAutomation(view.host, 'Sample event', 'event-1')
    act(() => intelButton(view!.host, 'Evaluate event').click())
    await settle()
    expect(intelButton(view.host, 'Evaluating…').disabled).toBe(true)
    act(() => intelButton(view!.host, 'Add event conditions').click())
    await act(async () => pending.resolve({ matches: true, checks: [], missing_fields: [], automation_payload: { event_id: 'event-1' } }))
    await settle()
    expect(view.host.textContent).toContain('The draft changed. Evaluate again')
    expect(view.host.textContent).not.toContain('This event matches.')
    const mutations = vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'POST')
    expect(mutations).toHaveLength(1)
    expect(mutations[0][0]).toBe('/notifications/webhooks/preview')
    expect(JSON.parse(String(mutations[0][1]?.body))).toMatchObject({ event_id: 'event-1', webhook: { payload_mode: 'automation_v1' } })
  })
  it('explains an unavailable sample and allows retry without exposing an old result', async () => {
    vi.mocked(apiFetch).mockImplementation((url) => url.includes('/events?') ? Promise.reject(new ApiError('No current access', 403, url)) : Promise.resolve([]))
    view = await mountIntel(<PreviewHarness />)
    const details = view.host.querySelector('details')!
    act(() => { details.open = true; details.dispatchEvent(new Event('toggle')) })
    await settle()
    expect(view.host.textContent).toContain('No current access')
    expect(intelButton(view.host, 'Evaluate event').disabled).toBe(true)
    expect(intelButton(view.host, 'Retry events').disabled).toBe(false)
  })
})

describe('credential profile lifecycle', () => {
  const profile: WebhookCredentialProfile = { id: 'profile-1', name: 'SIEM credential', revision: 3, enabled: true, auth_type: 'bearer', header_name: null, auth_configured: true, signing_configured: true }
  it('retains blank saved secrets, submits its original revision and preserves input on conflict', async () => {
    const pending = deferred<WebhookCredentialProfile>()
    vi.mocked(apiFetch).mockImplementation((_, options) => options?.method === 'PATCH' ? pending.promise as ReturnType<typeof apiFetch> : Promise.resolve([profile]) as ReturnType<typeof apiFetch>)
    view = await mountIntel(<WebhookCredentialProfiles writable onClose={vi.fn()} onDirtyChange={vi.fn()} />)
    editAutomation(document, 'Saved profile', profile.id)
    editAutomation(document, 'Profile name', 'Updated SIEM credential')
    act(() => intelButton(document, 'Save credential profile').click())
    await settle()
    expect(automationField(document, 'Profile name').matches(':disabled')).toBe(true)
    const request = vi.mocked(apiFetch).mock.calls.find(([, options]) => options?.method === 'PATCH')!
    const body = JSON.parse(String(request[1]?.body))
    expect(body).toMatchObject({ expected_revision: 3, clear_auth_secret: false, clear_signing_secret: false })
    expect(body).not.toHaveProperty('auth_secret')
    expect(body).not.toHaveProperty('signing_secret')
    await act(async () => pending.reject(new ApiError('Credential revision changed', 409, '/notifications/credential-profiles/profile-1')))
    await settle()
    expect(automationField(document, 'Profile name').value).toBe('Updated SIEM credential')
    expect(document.body.textContent).toContain('Credential revision changed')
    act(() => intelButton(document, 'Close profiles').click())
    await settle()
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard credential changes?')
  })
  it('makes clearing a signing key explicit and removes secrets from the form after saving', async () => {
    const writes: Record<string, unknown>[] = []
    vi.mocked(apiFetch).mockImplementation((_, options) => {
      if (options?.method === 'PATCH') writes.push(JSON.parse(String(options.body)))
      return Promise.resolve(options?.method === 'PATCH' ? { ...profile, revision: 4, signing_configured: false } : [profile]) as ReturnType<typeof apiFetch>
    })
    view = await mountIntel(<WebhookCredentialProfiles writable onClose={vi.fn()} onDirtyChange={vi.fn()} />)
    editAutomation(document, 'Saved profile', profile.id)
    editAutomation(document, 'Replacement authentication secret', 'replacement-secret')
    act(() => (automationField(document, 'Remove request signing') as HTMLInputElement).click())
    act(() => intelButton(document, 'Save credential profile').click())
    await settle()
    expect(writes[0]).toMatchObject({ auth_secret: 'replacement-secret', clear_signing_secret: true })
    expect(automationField(document, 'Replacement authentication secret').value).toBe('')
    expect(document.body.textContent).toContain('Secrets have been cleared from this form.')
  })
})
