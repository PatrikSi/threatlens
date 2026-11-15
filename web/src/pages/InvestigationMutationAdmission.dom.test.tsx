// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { InvestigationDetail } from '../types/investigations'
import { useInvestigationDetail } from './useInvestigationDetail'
import { InvestigationDetailWorkspace } from './InvestigationDetailWorkspace'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<typeof import('../api/client')>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'user-1', access: { permissions: ['write:investigations'] } }, isError: false }) }))
const detail: InvestigationDetail = {
  id: '11111111-1111-4111-8111-111111111111', title: 'Private investigation', description: 'Synthetic investigation context',
  status: 'open', severity: 'high', visibility: 'private', disposition: null, assignee_user_id: 'user-1', assignee_email: 'analyst@example.test', current_user_role: 'owner',
  evidence_count: 0, member_count: 1, note_count: 0, version: 7,
  created_at: '2026-11-15T09:00:00Z', updated_at: '2026-11-15T09:00:00Z', closed_at: null, archived_at: null,
  members: [{ user_id: 'user-1', email: 'analyst@example.test', role: 'owner', created_at: '2026-11-15T09:00:00Z' }],
  evidence: [], evidence_truncated: false, notes: [], notes_truncated: false,
}
let root: Root | undefined
let client: QueryClient
let router: ReturnType<typeof createMemoryRouter>
let host: HTMLDivElement
let current: ReturnType<typeof useInvestigationDetail>
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
afterEach(() => { act(() => root?.unmount()); root = undefined; router?.dispose(); client?.clear(); host?.remove(); vi.resetAllMocks() })

it.each([403, 503])('checks the failed canonical read before a queued direct mutation can start after HTTP %s', async (status) => {
  let failed = false
  vi.mocked(apiFetch).mockImplementation((path, options) => options?.method === 'PATCH' ? Promise.resolve({ ...detail, status: 'monitoring', version: 8 })
    : failed ? Promise.reject(new ApiError('Canonical investigation refresh failed', status, path)) : Promise.resolve(detail))
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  function Harness() { current = useInvestigationDetail(detail.id); return <InvestigationDetailWorkspace controller={current} /> }
  router = createMemoryRouter([{ path: '/investigations/:id', element: <Harness /> }], { initialEntries: [`/investigations/${detail.id}`] })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root!.render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>))
  await settle()
  expect(host.textContent).toContain(detail.title)
  expect(current.detailReadUnavailable).toBe(false)
  const oldMutate = current.mutation.mutateAsync
  let attempted = false
  let result: Promise<unknown> | undefined
  const stop = client.getQueryCache().subscribe((event) => {
    if (attempted || event.type !== 'updated' || event.action.type !== 'error'
      || event.query.queryKey[0] !== 'investigations' || event.query.queryKey[1] !== 'detail') return
    attempted = true
    // Invoke the captured callback in the actual QueryCache failure event, before
    // relying on any later React observer render to update read-unavailable flags.
    result = oldMutate({ kind: 'update', changes: { status: 'monitoring' }, expectedVersion: 7 }).catch(() => undefined)
  })
  failed = true
  await act(async () => {
    await client.invalidateQueries({ queryKey: ['investigations', 'detail', detail.id], exact: true })
    await result
  })
  stop()
  await settle()
  expect(attempted).toBe(true)
  expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(0)
})
