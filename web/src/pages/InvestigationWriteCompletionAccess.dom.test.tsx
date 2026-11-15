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

it('preserves the canonical read error after a late successful write until its real GET succeeds', async () => {
  let mode: 'good' | 'failed' | 'held' = 'good'
  let complete!: (value: InvestigationDetail) => void
  vi.mocked(apiFetch).mockImplementation((path, options) => {
    if (options?.method === 'PATCH') return new Promise((resolve) => { complete = resolve as typeof complete })
    if (mode === 'failed') return Promise.reject(new ApiError('Investigation temporarily unavailable', 503, path))
    if (mode === 'held') return new Promise(() => {})
    return Promise.resolve(detail)
  })
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  function Harness() { current = useInvestigationDetail(detail.id); return <InvestigationDetailWorkspace controller={current} /> }
  router = createMemoryRouter([{ path: '/investigations/:id', element: <Harness /> }], { initialEntries: [`/investigations/${detail.id}`] })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root!.render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>))
  await settle()
  const description = host.querySelector<HTMLTextAreaElement>('#investigation-description')!
  function edit(value: string) {
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(description, value)
      description.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const save = () => [...host.querySelectorAll<HTMLButtonElement>('button')].find((entry) => entry.textContent === 'Save changes')!
  edit('Submitted investigation scope')
  act(() => save().click())
  await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1)
  mode = 'failed'
  await act(async () => { await client.invalidateQueries({ queryKey: ['investigations', 'detail', detail.id], exact: true }) })
  await settle()
  expect(current.detailQuery.isError).toBe(true)
  expect(host.textContent).toContain('Investigation temporarily unavailable')
  mode = 'held'
  await act(async () => { complete({ ...detail, version: 8, description: 'Submitted investigation scope' }) })
  await settle()
  edit('Another unsent scope change')
  expect(current.detailQuery.isError).toBe(true)
  expect(host.textContent).toContain('Investigation temporarily unavailable')
  expect(description.value).toBe('Another unsent scope change')
  expect(save().disabled).toBe(true)
  expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1)
})
