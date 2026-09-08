// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { apiFetch } from '../api/client'
import { useFeedsPageController } from './useFeedsPageController'
import { useSMTPIntegrationController } from './useSMTPIntegrationController'
import { useNotificationWebhooksController } from './useNotificationWebhooksController'
import { createDefaultDraft } from './notificationWebhookDraft'
import type { Feed, NotificationWebhook, SMTPHook } from '../types/api'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', () => ({ apiFetch: vi.fn(), ApiError: class extends Error {} }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'user', role: 'analyst', access: { permissions: ['*:*'] }, features: {} } }) }))
vi.mock('../hooks/useUnsavedChangesWarning', () => ({ useUnsavedChangesWarning: vi.fn(() => Object.assign((callback: () => void) => callback(), { discardDialog: null })) }))
let root: Root | undefined
let client: QueryClient
function mount<T>(useController: () => T) {
  let current: T
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  function Harness() { current = useController(); return null }
  root = createRoot(document.createElement('div'))
  act(() => root!.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
  return () => current!
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) }) }
afterEach(() => { act(() => root?.unmount()); client?.clear(); vi.clearAllMocks(); window.sessionStorage.clear() })
const feed = { id: 'feed-1', name: 'Original', url: 'https://example.test/rss', enabled: true, fetch_mode: 'interval', fetch_interval_seconds: 1800, schedule_cron: null, error_count: 0, created_at: '2026-09-08T00:00:00Z' } as Feed
const smtp: SMTPHook = { integration_type: 'smtp', direction: 'destination', configured: true, schema_version: 1, password_configured: false, has_unreadable_secret: false, health_status: 'healthy', last_test_at: null, last_success_at: null, last_error_at: null, last_error: null, last_test_duration_ms: null, created_at: '2026-09-08T00:00:00Z', updated_at: '2026-09-08T00:00:00Z', is_default: false, uses_shared_credentials: false, credential_source_name: null, id: 'smtp-1', name: 'Original', host: 'smtp.example.test', port: 587, security: 'starttls', username: '', from_email: 'sender@example.test', from_name: '', to_emails: ['analyst@example.test'], timeout_seconds: 10, event_types: ['rss_item_new'], feed_scope: 'all', feed_ids: [], subject_template: 'Subject', html_template: '<p>Body</p>', enabled: true, credential_source_id: null }
const webhook: NotificationWebhook = { user_id: 'user', created_at: '2026-09-08T00:00:00Z', updated_at: '2026-09-08T00:00:00Z', ...createDefaultDraft(), id: 'webhook-1', name: 'Original', url_template: 'https://example.test/hook', body_template: null }

describe('save completion draft isolation', () => {
  it('preserves feed edits typed after submission and rebases unchanged server-normalized fields', async () => {
    let resolve!: (value: Feed) => void
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'PATCH' ? new Promise((done) => { resolve = done }) : Promise.resolve(path === '/feeds' ? [feed] : { items: [], total: 0 }) as never)
    const get = mount(useFeedsPageController)
    await settle()
    act(() => get().openFeedDetail(feed))
    act(() => get().updateFeedEditDraft({ name: 'Submitted', description: '  submitted description  ' }))
    act(() => get().onSaveFeedDetail())
    await settle()
    act(() => get().updateFeedEditDraft({ name: 'Newer edit' }))
    await act(async () => resolve({ ...feed, name: 'Submitted', description: 'submitted description' }))
    await settle()
    expect(get().feedEditDraft?.name).toBe('Newer edit')
    expect(get().feedEditDraft?.description).toBe('submitted description')
    expect(get().feedEditDirty).toBe(true)
  })

  it('keeps a different feed editor and new-feed changes when earlier saves finish', async () => {
    let resolve!: (value: Feed) => void
    const second = { ...feed, id: 'feed-2', name: 'Second' }
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'PATCH' || init?.method === 'POST' ? new Promise((done) => { resolve = done }) : Promise.resolve(path === '/feeds' ? [feed, second] : { items: [], total: 0 }) as never)
    const get = mount(useFeedsPageController)
    await settle()
    act(() => get().openFeedDetail(feed))
    act(() => get().updateFeedEditDraft({ name: 'Submitted' }))
    act(() => get().onSaveFeedDetail())
    await settle()
    act(() => get().openFeedDetail(second))
    await act(async () => resolve({ ...feed, name: 'Submitted' }))
    await settle()
    expect(get().feedEditDraft?.name).toBe('Second')
    act(() => { get().setName('New feed'); get().setUrl('https://example.test/new') })
    act(() => get().onSubmit({ preventDefault: () => {} } as React.FormEvent))
    await settle()
    act(() => get().setName('Next feed draft'))
    await act(async () => resolve({ ...feed, id: 'new-feed' }))
    await settle()
    expect(get().name).toBe('Next feed draft')
    expect(get().url).toBe('')
  })

  it('preserves SMTP edits and their dirty state after a delayed save', async () => {
    let resolve!: (value: SMTPHook) => void
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'PATCH' ? new Promise((done) => { resolve = done }) : Promise.resolve(path === '/integrations/smtp/hooks' ? [smtp] : []) as never)
    const get = mount(useSMTPIntegrationController)
    await settle()
    act(() => get().setDraft((current) => ({ ...current, name: 'Submitted' })))
    act(() => get().onSave())
    await settle()
    expect(resolve).toBeTypeOf('function')
    act(() => get().setDraft((current) => ({ ...current, name: 'Newer edit' })))
    await act(async () => resolve({ ...smtp, name: 'Submitted' }))
    await settle()
    expect(get().draft.name).toBe('Newer edit')
    expect(vi.mocked(useUnsavedChangesWarning).mock.lastCall?.[0]).toBe(true)
  })

  it('keeps later webhook edits dirty and does not replace a different selected webhook', async () => {
    let resolve!: (value: NotificationWebhook) => void
    const second = { ...webhook, id: 'webhook-2', name: 'Second' }
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'PATCH' ? new Promise((done) => { resolve = done }) : Promise.resolve(path === '/notifications/webhooks' ? [webhook, second] : []) as never)
    const get = mount(useNotificationWebhooksController)
    await settle()
    act(() => get().onSelectWebhook(webhook))
    act(() => get().setDraft((current) => ({ ...current, name: 'Submitted' })))
    act(() => get().onSave())
    await settle()
    act(() => get().setDraft((current) => ({ ...current, name: 'Newer edit' })))
    await act(async () => resolve({ ...webhook, name: 'Submitted' }))
    await settle()
    expect(get().draft.name).toBe('Newer edit')
    expect(vi.mocked(useUnsavedChangesWarning).mock.lastCall?.[0]).toBe(true)
    act(() => get().onSave())
    await settle()
    act(() => get().onSelectWebhook(second))
    await act(async () => resolve({ ...webhook, name: 'Newer edit' }))
    await settle()
    expect(get().selectedWebhookId).toBe(second.id)
    expect(get().draft.name).toBe('Second')
  })
})
