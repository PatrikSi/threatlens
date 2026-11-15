// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '../api/client'
import type { AlertEvaluationRequest } from '../types/alerts'
import { AlertOperationsWorkspace } from './AlertOperationsWorkspace'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
let client: QueryClient
let root: Root | undefined
let host: HTMLDivElement
let deniedResource: string | undefined
let status = 200
const requestId = 'private-evaluation'
const now = '2026-11-15T10:00:00Z'
const request: AlertEvaluationRequest = {
  id: requestId, item_id: 'private-item', item_content_hash: 'a'.repeat(64), state: 'dead_letter',
  source: 'live', active_source: 'live', notify: true, respect_rule_cutover: true,
  attempt_count: 5, max_attempts: 5, dispatch_attempt_count: 5, dispatch_failure_count: 1,
  version: 7, accepted_rule_count: 2, accepted_match_count: 1, degraded_owner_count: 0,
  degraded_owners_json: [], evaluated_rule_count: 0, occurrence_count: 0, backfill_count: 0,
  accepted_at: now, available_at: now, dispatch_claimed_at: null, last_dispatch_failed_at: null,
  claimed_at: null, lease_expires_at: null, completed_at: now, last_backfill_at: null,
  last_replayed_at: null, last_error_code: 'worker_error', last_error_message: 'Private evaluation diagnostic',
  created_at: now, updated_at: now,
}

beforeEach(() => {
  deniedResource = undefined
  status = 200
  client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0, staleTime: Infinity }, mutations: { retry: false } } })
  vi.mocked(apiFetch).mockImplementation(async (path) => {
    const resource = path.startsWith('/alerts/occurrences/evaluations?') ? 'list'
      : path.includes('/activity?') ? 'activity' : path.includes('/metrics?') ? 'metrics' : 'detail'
    if (resource === deniedResource && status !== 200) throw new ApiError(`Operational read rejected ${status}`, status, path)
    if (resource === 'list') return { items: [request], total: 7331, page: 1, page_size: 25 } as never
    if (resource === 'activity') return { items: [{ id: 'private-activity', request_id: requestId, actor_user_id: null,
      action: 'dead_lettered', details_json: { diagnostic: 'Private activity diagnostic' }, created_at: now }], total: 1, page: 1, page_size: 50 } as never
    if (resource === 'metrics') return { items: [], truncated: false } as never
    return request as never
  })
})
afterEach(() => {
  act(() => root?.unmount())
  client.clear()
  document.body.replaceChildren()
  root = undefined
  vi.resetAllMocks()
})
async function mount() {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root!.render(<QueryClientProvider client={client}><MemoryRouter><AlertOperationsWorkspace /></MemoryRouter></QueryClientProvider>))
  await vi.waitFor(() => expect(host.textContent).toContain('Private activity diagnostic'))
}

describe('alert operations actual query access history', () => {
  it('preserves ordinary outage context while pausing an already open replay confirmation', async () => {
    await mount()
    const replay = [...host.querySelectorAll('button')].find((entry) => entry.textContent === 'Replay')!
    act(() => replay.click())
    deniedResource = 'detail'
    status = 503
    const matches = { queryKey: ['alerts', 'operations', 'evaluation', requestId], exact: false, predicate: (query: { queryKey: readonly unknown[] }) => !query.queryKey.includes('activity') }
    await act(async () => { await client.invalidateQueries(matches) })
    await vi.waitFor(() => expect((client.getQueryCache().find(matches)?.state.error as ApiError)?.status).toBe(503))
    await vi.waitFor(() => expect(host.textContent).toContain('could not be'))
    expect(host.textContent).toContain('Private evaluation diagnostic')
    const dialog = document.querySelector('[role=alertdialog]')!
    expect(dialog).not.toBeNull()
    const confirm = [...dialog.querySelectorAll('button')].find((entry) => entry.textContent === 'Replay evaluation')!
    expect(confirm.disabled).toBe(true)
    act(() => confirm.click())
    expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it.each(['list', 'detail', 'activity'])('keeps denied evaluation %s hidden through an outage until a fresh read', async (resource) => {
    await mount()
    const marker = resource === 'list' ? '7331 results' : resource === 'detail' ? 'Private evaluation diagnostic' : 'Private activity diagnostic'
    expect(host.textContent).toContain(marker)
    const queryKey = resource === 'list' ? ['alerts', 'operations', 'evaluations']
      : resource === 'detail' ? ['alerts', 'operations', 'evaluation', requestId]
        : ['alerts', 'operations', 'evaluation', requestId, 'activity']
    const matches = { queryKey, exact: false, predicate: (query: { queryKey: readonly unknown[] }) => resource !== 'detail' || !query.queryKey.includes('activity') }
    deniedResource = resource
    for (status of [403, 503]) {
      await act(async () => { await client.invalidateQueries(matches) })
      await vi.waitFor(() => expect((client.getQueryCache().find(matches)?.state.error as ApiError)?.status).toBe(status))
      await vi.waitFor(() => expect(host.textContent).toContain(`Operational read rejected ${status}`))
      expect(host.textContent).not.toContain(marker)
    }
    status = 200
    await act(async () => { await client.invalidateQueries(matches) })
    await vi.waitFor(() => expect(host.textContent).toContain(marker))
    expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
})
