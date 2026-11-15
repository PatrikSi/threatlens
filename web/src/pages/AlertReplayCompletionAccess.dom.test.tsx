// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { AlertEvaluationReplayResponse, AlertEvaluationRequest } from '../types/alerts'
import { AlertOperationsWorkspace } from './AlertOperationsWorkspace'
import { deferred, intelButton, mountIntel } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
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

it('keeps a replay detail read error until its canonical read succeeds after late replay acknowledgment', async () => {
  const response = deferred<AlertEvaluationReplayResponse>()
  const canonical = deferred<AlertEvaluationRequest>()
  let detailState: 'initial' | 'outage' | 'held' = 'initial'
  let committed = false
  const queued = { ...request, state: 'pending' as const, active_source: 'replay' as const, version: 8, last_error_message: null, last_error_code: null }
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === 'POST') return response.promise as never
    if (path.startsWith('/alerts/occurrences/evaluations?')) return Promise.resolve({ items: [committed ? queued : request], total: 1, page: 1, page_size: 25 }) as never
    if (path.includes('/metrics?')) return Promise.resolve({ items: [], truncated: false }) as never
    if (path.includes('/activity?')) return Promise.resolve({ items: [], total: 0, page: 1, page_size: 50 }) as never
    if (detailState === 'outage') return Promise.reject(new ApiError('Canonical detail unavailable503', 503, path))
    if (detailState === 'held') return canonical.promise as never
    return Promise.resolve(request) as never
  })
  view = await mountIntel(<AlertOperationsWorkspace />)
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Private evaluation diagnostic'))
  act(() => intelButton(view!.host, 'Replay').click())
  act(() => intelButton(document.querySelector('[role=alertdialog]')!, 'Replay evaluation').click())
  await vi.waitFor(() => expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true))
  detailState = 'outage'
  const filters = { queryKey: ['alerts', 'operations', 'evaluation', requestId], exact: false,
    predicate: (query: { queryKey: readonly unknown[] }) => !query.queryKey.includes('activity') }
  await act(async () => { await view!.client.invalidateQueries(filters) })
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Canonical detail unavailable503'))
  expect(view.client.getQueryCache().find(filters)?.state.status).toBe('error')
  detailState = 'held'
  committed = true
  await act(async () => response.resolve({ request: queued, enqueue_failed: false }))
  await vi.waitFor(() => expect(view!.client.getQueryCache().find(filters)?.state.fetchStatus).toBe('fetching'))
  await vi.waitFor(() => expect(view!.client.getMutationCache().getAll().some((mutation) => mutation.state.status === 'success')).toBe(true))
  expect(view.client.getQueryCache().find(filters)?.state.status).toBe('error')
  expect(view.host.textContent).toContain('Canonical detail unavailable503')
  expect(view.host.textContent).toContain('Replay accepted and queued')
  await act(async () => canonical.resolve(queued))
  await vi.waitFor(() => expect(view!.client.getQueryCache().find(filters)?.state.status).toBe('success'))
  await vi.waitFor(() => expect(view!.host.textContent).not.toContain('Canonical detail unavailable503'))
})
