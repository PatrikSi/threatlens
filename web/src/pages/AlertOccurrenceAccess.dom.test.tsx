// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '../api/client'
import type { AlertOccurrence } from '../types/alerts'
import { AlertOccurrencesWorkspace } from './AlertOccurrencesWorkspace'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ data: { id: 'reader', role: 'analyst', features: {} }, isError: false }),
}))

const occurrence: AlertOccurrence = {
  id: 'private-occurrence', alert_interest_id: 'rule', rule_id_snapshot: 'rule', owner_user_id: 'reader',
  item_id: 'source', item_id_snapshot: 'source', integration_event_id: null, rule_revision: 1,
  item_content_hash: 'a'.repeat(64), alert_name_snapshot: 'Private watchlist',
  alert_category_snapshot: 'vulnerability', alert_keywords_snapshot: ['vpn'], matched_keywords: ['vpn'],
  source_snapshot_json: { item: { title: 'Sensitive source title', summary: 'Sensitive source excerpt', url: 'https://example.invalid/advisory' } },
  severity_snapshot: 'high', lifecycle_state: 'new', is_suppressed: false, suppressed_at: null,
  suppression_reason: null, is_snoozed: false, snoozed_until: null, snooze_reason: null,
  closure_disposition: null, acknowledged_at: null, acknowledged_by_user_id: null,
  investigating_at: null, investigating_by_user_id: null, closed_at: null, closed_by_user_id: null,
  version: 1, created_at: '2026-09-12T12:00:00Z', updated_at: '2026-09-12T12:00:00Z',
}
let root: Root | null = null
let host: HTMLDivElement | null = null
let client: QueryClient | null = null

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })
}

function resultFor(path: string) {
  if (path.startsWith('/teams/')) return { id: 'team-1', name: 'Team', active: true, can_manage: false }
  if (path.startsWith('/alerts?')) return [{ id: 'rule', name: 'Private watchlist', enabled: true }]
  if (path.includes('/activity?')) return {
    items: [{ id: 'activity', occurrence_id: occurrence.id, actor_user_id: 'reader', action: 'created',
      details_json: { reason: 'Sensitive activity context' }, created_at: occurrence.created_at }],
    total: 1, page: 1, page_size: 25,
  }
  if (path === `/alerts/occurrences/${occurrence.id}`) return occurrence
  return { items: [occurrence], total: 1, page: 1, page_size: 25 }
}

async function mount() {
  vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(resultFor(path)) as never)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root!.render(<MemoryRouter initialEntries={['/alerts?view=occurrences&occurrence=private-occurrence']}>
    <QueryClientProvider client={client!}><AlertOccurrencesWorkspace /></QueryClientProvider>
  </MemoryRouter>))
  await settle()
  expect(host.textContent).toContain('Sensitive source excerpt')
  expect(host.textContent).toContain('Sensitive activity context')
}

afterEach(() => {
  act(() => root?.unmount())
  client?.clear()
  host?.remove()
  root = null
  client = null
  host = null
  vi.resetAllMocks()
  delete occurrence.team_id
})

describe('alert access during real query-cache refreshes', () => {
  it('retains a known team withdrawal when metadata becomes unavailable before observer delivery', async () => {
    occurrence.team_id = 'team-1'
    await mount()
    const before = vi.mocked(apiFetch).mock.calls.filter(([path]) => path.startsWith('/teams/')).length
    const rejectedStatuses: number[] = []
    await act(async () => {
      // Both real query requests settle in this batch. Do not flush observer
      // timers or require a rendered403 before replacing it with an outage.
      for (const status of [403, 503]) {
        vi.mocked(apiFetch).mockImplementation((path) => path.startsWith('/teams/')
          ? Promise.reject(new ApiError('Team metadata unavailable', status, path, null, {
              code: status === 403 ? 'team_actor_unavailable' : 'team_access_unavailable',
            })) : path.startsWith('/alerts/occurrences')
              ? Promise.reject(new ApiError('Canonical evidence temporarily unavailable', 503, path))
              : Promise.resolve(resultFor(path)) as never)
        await client!.fetchQuery({ queryKey: ['teams', 'team-1'], staleTime: 0, retry: false }).then(
          () => { throw new Error('Expected the metadata request to be rejected') },
          (error) => {
            expect(error).toBeInstanceOf(ApiError)
            rejectedStatuses.push((error as ApiError).status)
          },
        )
      }
    })
    expect(rejectedStatuses).toEqual([403, 503])
    expect(vi.mocked(apiFetch).mock.calls.filter(([path]) => path.startsWith('/teams/'))).toHaveLength(before + 2)
    await settle()
    expect((client!.getQueryState(['teams', 'team-1'])?.error as ApiError)?.status).toBe(503)
    expect(host!.textContent).not.toContain('Sensitive source excerpt')
    expect(host!.textContent).not.toContain('Sensitive activity context')
  })

  it.each([403, 404])('keeps confirmed team withdrawal hidden through metadata outage and same-client remount (%s)', async (status) => {
    occurrence.team_id = 'team-1'
    await mount()
    const detailKey = ['alerts', 'occurrences', 'detail', occurrence.id]
    const teamKey = ['teams', 'team-1']
    let metadataStatus = status
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/teams/')) return Promise.reject(new ApiError('Confirmed team withdrawal', metadataStatus, path, null, {
        code: metadataStatus === 403 ? 'team_actor_unavailable' : metadataStatus === 404 ? 'team_not_found' : 'team_access_unavailable',
      }))
      if (path.startsWith('/alerts/occurrences')) return Promise.reject(new ApiError('Evidence temporarily unavailable', 503, path))
      return Promise.resolve(resultFor(path)) as never
    })
    await act(async () => { await client!.invalidateQueries({ queryKey: teamKey }) }); await settle()
    await vi.waitFor(() => expect((client!.getQueryState(teamKey)?.error as ApiError)?.status).toBe(status))
    await vi.waitFor(() => expect((client!.getQueryState(detailKey)?.error as ApiError)?.status).toBe(503))
    expect(host!.textContent).not.toContain('Sensitive source excerpt')
    expect(host!.textContent).not.toContain('Sensitive activity context')
    metadataStatus = 503
    await act(async () => { await client!.invalidateQueries({ queryKey: teamKey }) }); await settle()
    await vi.waitFor(() => expect((client!.getQueryState(teamKey)?.error as ApiError)?.status).toBe(503))
    const render = (visible: boolean) => act(() => root!.render(
      <MemoryRouter initialEntries={['/alerts?view=occurrences&occurrence=private-occurrence']}>
        <QueryClientProvider client={client!}>{visible ? <AlertOccurrencesWorkspace /> : <p>Other workspace</p>}</QueryClientProvider>
      </MemoryRouter>,
    ))
    render(false); await settle()
    expect(host!.textContent).toContain('Other workspace')
    render(true); await settle()
    await vi.waitFor(() => expect((client!.getQueryState(detailKey)?.error as ApiError)?.status).toBe(503))
    expect(host!.textContent).not.toContain('Sensitive source excerpt')
    expect(host!.textContent).not.toContain('Sensitive activity context')
    const retained = client!.getQueryData(detailKey)
    act(() => { client!.setQueryData(detailKey, retained) }); await settle()
    expect(host!.textContent).not.toContain('Sensitive source excerpt')
    let resolveEvidence!: (value: AlertOccurrence) => void
    const freshEvidence = new Promise<AlertOccurrence>((resolve) => { resolveEvidence = resolve })
    vi.mocked(apiFetch).mockImplementation((path) => path === `/alerts/occurrences/${occurrence.id}`
      ? freshEvidence : Promise.resolve(resultFor(path)) as never)
    await act(async () => { await client!.invalidateQueries({ queryKey: teamKey }) }); await settle()
    act(() => { void client!.invalidateQueries({ queryKey: detailKey }) }); await settle()
    expect(host!.textContent).not.toContain('Sensitive source excerpt')
    await act(async () => resolveEvidence({ ...occurrence, version: 2 })); await settle()
    await vi.waitFor(() => expect(host!.textContent).toContain('Sensitive source excerpt'))
    expect(host!.textContent).not.toContain('Sensitive activity context')
    await act(async () => { await client!.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'activity'] }) }); await settle()
    await vi.waitFor(() => expect(host!.textContent).toContain('Sensitive activity context'))
  })

  it('keeps direct personal evidence and activity hidden through denial followed by an outage', async () => {
    await mount()
    for (const status of [403, 503]) {
      vi.mocked(apiFetch).mockRejectedValue(new ApiError('Occurrence read unavailable', status, '/alerts/occurrences'))
      await act(async () => { await client!.invalidateQueries() })
      await settle()
      for (const privateText of ['Sensitive source title', 'Sensitive source excerpt', 'Sensitive activity context', 'Private watchlist']) {
        expect(host!.textContent).not.toContain(privateText)
      }
    }
  })

  it('retains an ordinary outage detail but pauses lifecycle and snooze actions', async () => {
    await mount()
    vi.mocked(apiFetch).mockImplementation((path) => path === `/alerts/occurrences/${occurrence.id}`
      ? Promise.reject(new ApiError('Detail temporarily unavailable', 503, path))
      : Promise.resolve(resultFor(path)) as never)
    await act(async () => { await client!.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'detail'] }) })
    await settle()
    expect(host!.textContent).toContain('Sensitive source excerpt')
    const actionSection = host!.querySelector('[aria-labelledby="occurrence-actions-heading"]')!
    const actions = [...actionSection.querySelectorAll('button')]
    expect(actions.map((button) => button.textContent?.trim())).toEqual(['Acknowledge', 'Start investigating', 'Close occurrence', 'Snooze'])
    expect(actions.every((button) => button.disabled)).toBe(true)
    act(() => { actions.forEach((button) => button.click()) })
    await settle()
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => ['PATCH', 'POST'].includes(options?.method ?? ''))).toBe(false)
  })

  it.each([
    [404, 'team_not_found', false], [403, 'team_actor_unavailable', false],
    [403, 'permission_denied', true], [503, 'team_access_unavailable', true],
  ] as const)('propagates confirmed team access loss without treating metadata permission limits as evidence denial (%s/%s)', async (status, code, visible) => {
    occurrence.team_id = 'team-1'
    await mount()
    await settle()
    vi.mocked(apiFetch).mockImplementation((path) => path.startsWith('/teams/')
      ? Promise.reject(new ApiError('Team verification failed', status, path, null, { code }))
      : Promise.resolve(resultFor(path)) as never)
    await act(async () => { await client!.invalidateQueries({ queryKey: ['teams', 'team-1'] }) })
    await settle()
    expect(host!.textContent!.includes('Sensitive source excerpt')).toBe(visible)
    expect(host!.textContent!.includes('Sensitive activity context')).toBe(visible)
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(resultFor(path)) as never)
    await act(async () => { await client!.invalidateQueries({ queryKey: ['teams', 'team-1'] }) })
    await settle()
    expect(host!.textContent).toContain('Sensitive source excerpt')
  })

  it('keeps withdrawn evidence hidden while recovered team access waits for fresh evidence', async () => {
    occurrence.team_id = 'team-1'
    await mount()
    await settle()
    let resolveEvidence!: (value: AlertOccurrence) => void
    const freshEvidence = new Promise<AlertOccurrence>((resolve) => { resolveEvidence = resolve })
    let teamRecovered = false
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/teams/') && !teamRecovered) {
        return Promise.reject(new ApiError('Team not found', 404, path, null, { code: 'team_not_found' }))
      }
      if (path === `/alerts/occurrences/${occurrence.id}`) return freshEvidence as never
      return Promise.resolve(resultFor(path)) as never
    })
    await act(async () => { await client!.invalidateQueries({ queryKey: ['teams', 'team-1'] }) })
    await settle()
    expect(host!.textContent).not.toContain('Sensitive source excerpt')
    teamRecovered = true
    await act(async () => { await client!.invalidateQueries({ queryKey: ['teams', 'team-1'] }) })
    await settle()
    expect(host!.textContent).not.toContain('Sensitive source excerpt')
    expect(host!.textContent).not.toContain('Sensitive activity context')
    await act(async () => { resolveEvidence(occurrence) })
    await settle()
    expect(host!.textContent).toContain('Sensitive source excerpt')
  })

  it.each([401, 403, 404, 503])('handles HTTP %s without presenting withdrawn access as an outage', async (status) => {
    await mount()
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Occurrence refresh rejected', status, '/alerts/occurrences'))
    await act(async () => { await client!.invalidateQueries() })
    await settle()

    expect(host!.textContent).toContain('Occurrence refresh rejected')
    for (const sensitive of ['Sensitive source title', 'Sensitive source excerpt', 'Sensitive activity context', 'Private watchlist']) {
      if (status === 503) expect(host!.textContent).toContain(sensitive)
      else expect(host!.textContent).not.toContain(sensitive)
    }
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(resultFor(path)) as never)
    await act(async () => { await client!.invalidateQueries() })
    await settle()
    expect(host!.textContent).toContain('Sensitive source excerpt')
  })

  it.each([403, 404])('hides independently denied activity after HTTP %s', async (status) => {
    await mount()
    vi.mocked(apiFetch).mockImplementation((path) => path.includes('/activity?')
      ? Promise.reject(new ApiError('Activity access withdrawn', status, path))
      : Promise.resolve(resultFor(path)) as never)
    await act(async () => { await client!.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'activity'] }) })
    await settle()

    expect(host!.textContent).toContain('Activity access withdrawn')
    expect(host!.textContent).not.toContain('Sensitive activity context')
    expect(host!.textContent).toContain('Sensitive source excerpt')
  })
})
