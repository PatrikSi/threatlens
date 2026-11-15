// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, apiFetchWithResponse } from '../api/client'
import type { ApiToken, ApiTokenListResponse } from '../types/api'
import { TokenInventory } from './TokenInventory'
import { deferred, intelButton, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn(), apiFetchWithResponse: vi.fn() }))
const sibling: ApiToken = { id: 'sibling-token', user_id: 'operator', name: 'Cached sibling token', token_prefix: 'tl_sibling',
  scopes: ['read:feeds'], created_at: '2026-11-15T00:00:00Z', expires_at: null, revoked_at: null, last_used_at: null }
const leaf: ApiToken = { ...sibling, id: 'leaf-token', name: 'Revocation leaf token', token_prefix: 'tl_leaf' }
let client: QueryClient
let root: Root | undefined
let router: ReturnType<typeof createMemoryRouter> | undefined
let host: HTMLDivElement
afterEach(() => { act(() => root?.unmount()); router?.dispose(); client?.clear(); host?.remove(); root = undefined; vi.resetAllMocks() })

it('keeps a cached sibling token action paused on reentry after another page revocation and unavailable canonical reads', async () => {
  let mode: 'good' | 'page-one-failed' | 'held' = 'good'
  const response = deferred<{ data: void; status: number; headers: Headers }>()
  vi.mocked(apiFetchWithResponse).mockReturnValue(response.promise)
  vi.mocked(apiFetch).mockImplementation((path) => {
    const page = Number(new URL(path, 'https://local.test').searchParams.get('page'))
    if (mode === 'held') return new Promise(() => {})
    if (mode === 'page-one-failed' && page === 1) return Promise.reject(new ApiError('Page one canonical read unavailable', 503, path))
    const result: ApiTokenListResponse = { tokens: [page === 2 ? leaf : sibling], total: 26, page, page_size: 25, unscoped_total: 0 }
    return Promise.resolve(result)
  })
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } } })
  router = createMemoryRouter([
    { path: '/tokens', element: <TokenInventory isAdmin canManage secretNotice="" /> },
    { path: '/other', element: <p>Other page</p> },
  ], { initialEntries: ['/tokens'] })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root!.render(<QueryClientProvider client={client}><RouterProvider router={router!} /></QueryClientProvider>))
  await vi.waitFor(() => expect(host.textContent).toContain(sibling.name))
  mode = 'page-one-failed'
  await act(async () => { await client.invalidateQueries({ queryKey: ['tokens', 'inventory', '', 1], exact: true }) })
  await vi.waitFor(() => expect(host.textContent).toContain('Page one canonical read unavailable'))
  expect(intelButton(host, 'Revoke').disabled).toBe(true)
  act(() => intelButton(host, 'Next').click())
  await vi.waitFor(() => expect(host.textContent).toContain(leaf.name))
  await vi.waitFor(() => expect(intelButton(host, 'Revoke').disabled).toBe(false))
  act(() => intelButton(host, 'Revoke').click())
  act(() => intelButton(document.body, 'Revoke token').click())
  await vi.waitFor(() => expect(apiFetchWithResponse).toHaveBeenCalledWith('/tokens/leaf-token', { method: 'DELETE' }))
  mode = 'held'
  await act(async () => response.resolve({ data: undefined, status: 204, headers: new Headers() }))
  await vi.waitFor(() => expect(host.textContent).toContain('API token revoked.'))
  await act(async () => { await router!.navigate('/other') }); await settle()
  const readsBefore = vi.mocked(apiFetch).mock.calls.filter(([path]) => path.includes('page=1&')).length
  await act(async () => { await router!.navigate('/tokens') }); await settle()
  expect(host.textContent).toContain(sibling.name)
  expect(intelButton(host, 'Revoke').disabled).toBe(true)
  expect(vi.mocked(apiFetch).mock.calls.filter(([path]) => path.includes('page=1&'))).toHaveLength(readsBefore + 1)
  expect(client.getQueryState(['tokens', 'inventory', '', 1])?.fetchStatus).toBe('fetching')
  act(() => intelButton(host, 'Revoke').click())
  expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  expect(apiFetchWithResponse).toHaveBeenCalledTimes(1)
})
