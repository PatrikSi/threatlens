// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { TeamIntegrations } from './TeamIntegrations'
import { TeamIntegrationConfiguration } from './TeamIntegrationConfiguration'
import { createDefaultDraft, createRequestFromDraft } from './notificationWebhookDraft'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
const base = '/teams/team/integrations/destination'
const destination = { id: 'destination', name: 'Private team destination', enabled: true,
  custodian_user_id: 'analyst', ownership_revision: 2, event_type: 'rss_item_new' }
const configuration = { ...createRequestFromDraft(createDefaultDraft()), ...destination,
  user_id: 'analyst', team_id: 'team', created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z' }
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let failedPath: string | undefined
let status: number
let savedName: string
beforeEach(() => {
  status = 200; failedPath = undefined; savedName = destination.name
  vi.mocked(apiFetch).mockImplementation(async (path) => {
    if (path === failedPath && status !== 200) throw new ApiError(`Read failed ${status}`, status, path)
    if (path === '/notifications/webhooks') return [{ ...configuration, id: 'personal', name: 'Private personal destination', team_id: null }]
    if (path === '/teams/team/integrations') return { items: [destination] }
    if (path.endsWith('/receiver-credentials')) return { items: [{ id: 'credential', name: 'Private receiver name', expires_at: '2026-12-15T00:00:00Z', revoked_at: null }] }
    if (path.endsWith('/configuration')) return { ...configuration, name: savedName }
    if (path === '/feeds' || path === '/notifications/template-variables') return []
    throw new Error(`Unexpected path: ${path}`)
  })
})
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
async function refresh() {
  await act(async () => { await view!.client.invalidateQueries() }); await settle()
}
function nameInput() {
  return [...view!.host.querySelectorAll('label')].find((label) => label.textContent?.startsWith('Destination name'))?.control as HTMLInputElement
}
function editName(value: string) {
  const input = nameInput()
  act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true })) })
}

it.each([
  ['/teams/team/integrations', 'Private team destination'],
  ['/notifications/webhooks', 'Private personal destination'],
] as const)('keeps denied destination metadata private through a later outage: %s', async (path, text) => {
  view = await mountIntel(<TeamIntegrations teamId="team" unavailable={false} />)
  expect(view.host.textContent).toContain(text)
  failedPath = path; status = 403; await refresh()
  expect(view.host.textContent).not.toContain(text)
  status = 503; await refresh()
  expect(view.host.textContent).not.toContain(text)
})

it('keeps denied receiver inventory private through a later outage', async () => {
  view = await mountIntel(<TeamIntegrations teamId="team" unavailable={false} />)
  act(() => intelButton(view!.host, 'Receiver credentials').click()); await settle()
  expect(view.host.textContent).toContain('Private receiver name')
  failedPath = `${base}/receiver-credentials`; status = 403; await refresh()
  expect(view.host.textContent).not.toContain('Private receiver name')
  status = 503; await refresh()
  expect(view.host.textContent).not.toContain('Private receiver name')
})

it('does not show a late receiver token after denied reads and later recovery', async () => {
  const pending = deferred<{ token: string }>()
  const original = vi.mocked(apiFetch).getMockImplementation()!
  vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'POST' ? pending.promise : original(path, init))
  view = await mountIntel(<TeamIntegrations teamId="team" unavailable={false} />)
  act(() => intelButton(view!.host, 'Receiver credentials').click()); await settle()
  act(() => intelButton(view!.host, 'Create receiver credential').click()); await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  failedPath = `${base}/receiver-credentials`; status = 403; await refresh()
  status = 503; await refresh()
  await act(async () => pending.resolve({ token: 'late-private-token' })); await settle()
  status = 200; await refresh()
  expect(view.host.querySelector('input[readonly]')).toBeNull()
})

it.each(['configuration', 'parent inventory'] as const)('preserves a hidden dirty destination after denied %s until explicit reload', async (source) => {
  view = await mountIntel(source === 'configuration'
    ? <TeamIntegrationConfiguration base={base} unavailable={false} onChanged={vi.fn()} />
    : <TeamIntegrations teamId="team" unavailable={false} />)
  if (source === 'parent inventory') { act(() => intelButton(view!.host, 'Edit destination configuration').click()); await settle() }
  expect(nameInput().value).toBe(destination.name)
  editName('Unsent destination draft')
  failedPath = source === 'configuration' ? `${base}/configuration` : '/teams/team/integrations'
  status = 403; await refresh()
  expect(view.host.querySelector('input[value="Unsent destination draft"]')).toBeNull()
  status = 200; await refresh()
  expect(nameInput().value).toBe('Unsent destination draft')
})

it('loads a fresh clean editor after its configuration access recovers', async () => {
  view = await mountIntel(<TeamIntegrationConfiguration base={base} unavailable={false} onChanged={vi.fn()} />)
  expect(nameInput().value).toBe(destination.name)
  failedPath = `${base}/configuration`; status = 403; await refresh()
  expect(nameInput()).toBeUndefined()
  savedName = 'Fresh saved destination'; status = 200; await refresh()
  expect(nameInput().value).toBe(savedName)
})

it.each(['configuration', 'receiver'] as const)('requires fresh child %s evidence after the parent integration list recovers', async (child) => {
  const original = vi.mocked(apiFetch).getMockImplementation()!
  let holdChild = false
  vi.mocked(apiFetch).mockImplementation((path, init) => holdChild && path.endsWith(child === 'configuration' ? '/configuration' : '/receiver-credentials')
    ? new Promise(() => {}) : original(path, init))
  view = await mountIntel(<TeamIntegrations teamId="team" unavailable={false} />)
  act(() => intelButton(view!.host, child === 'configuration' ? 'Edit destination configuration' : 'Receiver credentials').click()); await settle()
  if (child === 'configuration') editName('Unsent destination draft')
  else expect(view.host.textContent).toContain('Private receiver name')
  failedPath = '/teams/team/integrations'; status = 403
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['teams', 'team', 'integrations'], exact: true }) }); await settle()
  expect(view.host.textContent).not.toContain(destination.name)
  holdChild = true; status = 200
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['teams', 'team', 'integrations'], exact: true }) }); await settle()
  expect(view.host.textContent).toContain(destination.name)
  if (child === 'configuration') expect(nameInput()).toBeUndefined()
  else {
    act(() => intelButton(view!.host, 'Receiver credentials').click()); await settle()
    expect(view.host.textContent).not.toContain('Private receiver name')
  }
})

it('pauses a queued receiver creation after its parent read fails before observer delivery', async () => {
  const original = vi.mocked(apiFetch).getMockImplementation()!
  vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'POST'
    ? Promise.resolve({ token: 'queued-receiver-token' }) : original(path, init))
  view = await mountIntel(<TeamIntegrations teamId="team" unavailable={false} />)
  act(() => intelButton(view!.host, 'Receiver credentials').click()); await settle()
  const create = intelButton(view.host, 'Create receiver credential')
  expect(create.matches(':disabled')).toBe(false)
  let observedFailure = false
  const unsubscribe = view.client.getQueryCache().subscribe((event) => {
    if (event.type === 'updated' && event.action.type === 'error'
      && JSON.stringify(event.query.queryKey) === JSON.stringify(['teams', 'team', 'integrations'])) {
      observedFailure = true
      create.click()
    }
  })
  failedPath = '/teams/team/integrations'; status = 503
  try {
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['teams', 'team', 'integrations'], exact: true }) }); await settle()
    expect(observedFailure).toBe(true)
    expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  } finally { unsubscribe() }
})
