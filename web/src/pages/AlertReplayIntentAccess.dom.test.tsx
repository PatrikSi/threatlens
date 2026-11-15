// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import type { AlertEvaluationRequest } from '../types/alerts'
import { AlertOperationsWorkspace } from './AlertOperationsWorkspace'
import { intelButton, mountIntel } from './articleIntelligenceTestSupport'

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

it('retires a replay confirmation when polling selects another evaluation before returning to the original', async () => {
  const other = { ...request, id: 'other-evaluation', item_id: 'other-item', last_error_message: 'Other evaluation diagnostic' }
  let rows = [request]
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path.startsWith('/alerts/occurrences/evaluations?')) return Promise.resolve({ items: rows, total: rows.length, page: 1, page_size: 25 }) as never
    if (path.includes('/metrics?')) return Promise.resolve({ items: [], truncated: false }) as never
    if (path.includes('/activity?')) return Promise.resolve({ items: [], total: 0, page: 1, page_size: 50 }) as never
    return Promise.resolve(path.includes(other.id) ? other : request) as never
  })
  view = await mountIntel(<AlertOperationsWorkspace />)
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Private evaluation diagnostic'))
  act(() => intelButton(view!.host, 'Replay').click())
  expect(document.querySelector('[role=alertdialog]')).not.toBeNull()
  rows = [other]
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['alerts', 'operations', 'evaluations'] }) })
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Other evaluation diagnostic'))
  expect(document.querySelector('[role=alertdialog]')).toBeNull()
  rows = [request]
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['alerts', 'operations', 'evaluations'] }) })
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Private evaluation diagnostic'))
  expect(document.querySelector('[role=alertdialog]')).toBeNull()
  expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
})
