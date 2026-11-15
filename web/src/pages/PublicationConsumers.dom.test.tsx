// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { PublicationConsumers } from './PublicationConsumers'
import { ApiError, apiFetch } from '../api/client'
import { invalidateSession } from '../api/sessionLifecycle'
import type { IndicatorPublication } from '../types/indicatorPublications'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks(); invalidateSession() })
const consumer = { id: 'consumer-1', name: 'SIEM receiver', expires_at: '2026-12-26T00:00:00Z',
  retired_at: null, revoked_at: null, sequence: 0, generation: 1, replay_floor: 0, last_poll_at: null }
const publication: IndicatorPublication = { id: 'publication-1', team_id: 'team-1', format: 'misp', marking: 'TLP:AMBER',
  status: 'active', revision: 1, indicator_count: 1, withdrawn_count: 0, created_at: '2026-09-27T00:00:00Z', updated_at: '2026-09-27T00:00:00Z' }
async function open() {
  act(() => intelButton(view!.host, 'Publication consumers').click())
  await settle()
}
function select(label: string, value: string) {
  const control = [...view!.host.querySelectorAll('label')].find((node) => node.textContent?.startsWith(label) && node.control instanceof HTMLSelectElement)!.control as HTMLSelectElement
  act(() => { control.value = value; control.dispatchEvent(new Event('change', { bubbles: true })) })
}

it('keeps a consumer denial fenced through an outage until a fresh authorized read', async () => {
  vi.mocked(apiFetch).mockResolvedValue([consumer])
  view = await mountIntel(<PublicationConsumers teamId="team-1" publications={[publication]} />)
  await open()
  expect(view.host.textContent).toContain(consumer.name)
  for (const status of [403, 503]) {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Consumer access unavailable', status, '/consumers'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['publication-consumers', 'team-1'] }) }); await settle()
    expect(view.host.textContent).not.toContain(consumer.name)
    expect(view.host.querySelector('form')).toBeNull()
  }
  vi.mocked(apiFetch).mockResolvedValue([{ ...consumer, name: 'Fresh authorized receiver' }])
  act(() => intelButton(view!.host, 'Retry consumer access').click()); await settle()
  expect(view.host.textContent).toContain('Fresh authorized receiver')
})

it('pauses consumer writes and retires confirmation while an outage preserves context', async () => {
  vi.mocked(apiFetch).mockResolvedValue([consumer])
  view = await mountIntel(<PublicationConsumers teamId="team-1" publications={[publication]} />)
  await open()
  select('Consumer ', consumer.id)
  select('Publication ', publication.id)
  act(() => intelButton(view!.host, 'Retire and withdraw').click()); await settle()
  expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
  vi.mocked(apiFetch).mockRejectedValue(new ApiError('Consumer status unavailable', 503, '/consumers'))
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['publication-consumers', 'team-1'] }) }); await settle()
  expect(view.host.textContent).toContain(consumer.name)
  expect(intelButton(view.host, 'Rotate credential').matches(':disabled')).toBe(true)
  expect(intelButton(view.host, 'Retire and withdraw').matches(':disabled')).toBe(true)
  expect(intelButton(view.host, 'Subscribe publication').matches(':disabled')).toBe(true)
  expect(view.host.querySelector('form fieldset')?.matches(':disabled')).toBe(true)
  expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  vi.mocked(apiFetch).mockResolvedValue([consumer])
  act(() => intelButton(view!.host, 'Retry consumer access').click()); await settle()
  expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method === 'POST' || init?.method === 'DELETE')).toBe(false)
})

it('does not restore a consumer confirmation when denied access is freshly recovered', async () => {
  vi.mocked(apiFetch).mockResolvedValue([consumer])
  view = await mountIntel(<PublicationConsumers teamId="team-1" publications={[publication]} />)
  await open()
  act(() => intelButton(view!.host, 'Retire and withdraw').click()); await settle()
  vi.mocked(apiFetch).mockRejectedValue(new ApiError('Consumer access removed', 403, '/consumers'))
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['publication-consumers', 'team-1'] }) }); await settle()
  expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  vi.mocked(apiFetch).mockResolvedValue([consumer])
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['publication-consumers', 'team-1'] }) }); await settle()
  expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method === 'POST' || init?.method === 'DELETE')).toBe(false)
})

it('keeps another team consumer cache independent from a denied manager scope', async () => {
  const held = deferred<unknown>()
  let phase = 'seed'
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (phase === 'held') return held.promise
    if (phase === 'denied') return Promise.reject(new ApiError('Team manager access removed', 403, path))
    return Promise.resolve([{ ...consumer, name: path.includes('/team-2/') ? 'Other team receiver' : consumer.name }])
  })
  let choose!: (id: string) => void
  function Workspace() {
    const [team, setTeam] = useState('team-1')
    choose = setTeam
    return <PublicationConsumers teamId={team} publications={[]} />
  }
  view = await mountIntel(<Workspace />)
  await open()
  act(() => choose('team-2')); await settle()
  expect(view.host.textContent).toContain('Other team receiver')
  act(() => choose('team-1')); await settle()
  phase = 'denied'
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['publication-consumers', 'team-1'] }) }); await settle()
  phase = 'held'
  act(() => choose('team-2')); await settle()
  expect(view.host.textContent).toContain('Other team receiver')
  expect(intelButton(view.host, 'Rotate credential').matches(':disabled')).toBe(false)
})

it('does not return a credential from a replaced session even while the workspace is mounted', async () => {
  const pending = deferred<unknown>()
  vi.mocked(apiFetch).mockImplementation((path) => path.endsWith('/rotate') ? pending.promise : Promise.resolve([consumer]))
  view = await mountIntel(<PublicationConsumers teamId="team-1" publications={[publication]} />)
  await open()
  act(() => intelButton(view!.host, 'Rotate credential').click())
  await settle()
  invalidateSession()
  await act(async () => pending.resolve({ ...consumer, token: 'tlpc_old_session_secret' }))
  await settle()
  expect(view.host.textContent).not.toContain('tlpc_old_session_secret')
  expect(view.host.textContent).toContain('The session changed')
})

it('disables subscriptions when a selected publication or consumer is no longer eligible', async () => {
  vi.mocked(apiFetch).mockResolvedValue([consumer])
  let update!: (rows: IndicatorPublication[]) => void
  function Workspace() {
    const [rows, setRows] = useState([publication])
    update = setRows
    return <PublicationConsumers teamId="team-1" publications={rows} />
  }
  view = await mountIntel(<Workspace />)
  await open()
  select('Consumer ', consumer.id)
  select('Publication ', publication.id)
  expect(intelButton(view.host, 'Subscribe publication').disabled).toBe(false)
  act(() => update([{ ...publication, status: 'withdrawn' }]))
  expect(intelButton(view.host, 'Subscribe publication').disabled).toBe(true)
  act(() => update([publication]))
  expect(intelButton(view.host, 'Subscribe publication').disabled).toBe(false)
  vi.mocked(apiFetch).mockResolvedValue([{ ...consumer, retired_at: '2026-09-27T00:00:00Z' }])
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['publication-consumers', 'team-1'] }) }); await settle()
  expect(intelButton(view.host, 'Subscribe publication').disabled).toBe(true)
})

it('hides cached consumers immediately after an authorization failure', async () => {
  vi.mocked(apiFetch).mockImplementation((path) => path.endsWith('/rotate')
    ? Promise.reject(new ApiError('Team access removed', 403, path)) : Promise.resolve([consumer]))
  view = await mountIntel(<PublicationConsumers teamId="team-1" publications={[publication]} />)
  await open()
  act(() => intelButton(view!.host, 'Rotate credential').click())
  await settle()
  expect(view.host.textContent).toContain('Team access removed')
  expect(view.host.querySelector('form')).toBeNull()
  expect(view.host.textContent).not.toContain('SIEM receiver')
})
