// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, apiFetchWithResponse } from '../api/client'
import type { ApiTokenListResponse } from '../types/api'
import { TokenInventory } from './TokenInventory'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn(), apiFetchWithResponse: vi.fn() }))

const PAGE: ApiTokenListResponse = {
  tokens: [{ id: 'private-token', user_id: 'private-owner', name: 'Private automation credential', token_prefix: 'tlp_private_prefix',
    scopes: ['read:feeds'], created_at: '2026-11-15T00:00:00Z', expires_at: null, revoked_at: null, last_used_at: null }],
  total: 1, page: 1, page_size: 25, unscoped_total: 0,
}
let client: QueryClient
let root: Root | undefined
let host: HTMLDivElement
let showInventory: (show: boolean) => void

function Harness() {
  const [show, setShow] = useState(true)
  showInventory = setShow
  return show ? <TokenInventory isAdmin canManage secretNotice="" /> : <p>Inventory collapsed</p>
}
async function settle() { await act(async () => { await new Promise((done) => setTimeout(done, 15)) }) }
async function mountInventory() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root!.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
  await settle()
  await vi.waitFor(() => expect(host.textContent).toContain('Private automation credential'))
}
function tokenButton(parent: ParentNode, label: string) {
  const button = [...parent.querySelectorAll('button')].find((entry) => entry.textContent?.trim() === label)
  if (!button) throw new Error(`Button missing: ${label}`)
  return button
}
async function rejectInventory(status: number, page = 1) {
  vi.mocked(apiFetch).mockRejectedValue(new ApiError(`Inventory rejected ${status}`, status, '/tokens/inventory'))
  await act(async () => { await client.invalidateQueries({ queryKey: ['tokens', 'inventory'] }) })
  await settle()
  await vi.waitFor(() => {
    expect((client.getQueryState(['tokens', 'inventory', '', page])?.error as ApiError)?.status).toBe(status)
    expect(host.querySelector('[role="alert"]')?.textContent).toContain(`Inventory rejected ${status}`)
  })
}
beforeEach(() => { vi.mocked(apiFetch).mockResolvedValue(PAGE) })
afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); root = undefined; vi.resetAllMocks() })

describe('token inventory read authority', () => {
  it('hides denied token metadata through a later outage until a fresh read succeeds', async () => {
    await mountInventory()
    for (const status of [403, 503]) {
      await rejectInventory(status)
      for (const value of ['Private automation credential', 'tlp_private_prefix', 'private-owner']) expect(host.textContent).not.toContain(value)
    }
    vi.mocked(apiFetch).mockResolvedValue({ ...PAGE, tokens: [{ ...PAGE.tokens[0], name: 'Fresh token metadata' }] })
    await act(async () => { await client.invalidateQueries({ queryKey: ['tokens', 'inventory'] }) })
    await vi.waitFor(() => expect(host.textContent).toContain('Fresh token metadata'))
  })

  it('retires token revocation intent when inventory access is denied and recovered', async () => {
    await mountInventory()
    act(() => tokenButton(host, 'Revoke').click())
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Private automation credential')
    await rejectInventory(403)
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
    vi.mocked(apiFetch).mockResolvedValue(PAGE)
    await act(async () => { await client.invalidateQueries({ queryKey: ['tokens', 'inventory'] }) })
    await vi.waitFor(() => expect(host.textContent).toContain('Private automation credential'))
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
    expect(apiFetchWithResponse).not.toHaveBeenCalled()
  })

  it('retains ordinary-outage token context while pausing revocation', async () => {
    await mountInventory()
    await rejectInventory(503)
    expect(host.textContent).toContain('Private automation credential')
    expect(tokenButton(host, 'Revoke').disabled).toBe(true)
    act(() => tokenButton(host, 'Revoke').click())
    expect(apiFetchWithResponse).not.toHaveBeenCalled()
  })

  it('marks an authorized revocation in the real cache while its canonical read is pending', async () => {
    vi.mocked(apiFetchWithResponse).mockResolvedValue({ data: undefined, status: 204, headers: new Headers() })
    await mountInventory()
    let resolveFresh!: (value: ApiTokenListResponse) => void
    vi.mocked(apiFetch).mockReturnValue(new Promise((resolve) => { resolveFresh = resolve }))
    act(() => tokenButton(host, 'Revoke').click())
    act(() => tokenButton(document, 'Revoke token').click())
    await vi.waitFor(() => {
      expect(apiFetchWithResponse).toHaveBeenCalledWith('/tokens/private-token', { method: 'DELETE' })
      expect(host.querySelector('ul[aria-label="API tokens"]')?.textContent).toContain('Revoked')
    })
    await act(async () => resolveFresh({ ...PAGE, tokens: [{ ...PAGE.tokens[0], revoked_at: '2026-11-15T01:00:00Z' }] }))
    await settle()
    expect(host.querySelector('ul[aria-label="API tokens"]')?.textContent).toContain('Revoked')
    expect(host.textContent).toContain('API token revoked.')
  })

  it('does not restore a cached token page on remount after a sibling page denial', async () => {
    let phase: 'seed' | 'held' = 'seed'
    let resolveFresh!: (value: ApiTokenListResponse) => void
    const fresh = new Promise<ApiTokenListResponse>((resolve) => { resolveFresh = resolve })
    vi.mocked(apiFetch).mockImplementation((path) => path.includes('page=2&')
      ? Promise.reject(new ApiError('Inventory rejected 403', 403, path))
      : phase === 'held' ? fresh : Promise.resolve({ ...PAGE, total: 26 }))
    await mountInventory()
    act(() => tokenButton(host, 'Next').click())
    await vi.waitFor(() => expect(host.querySelector('[role="alert"]')?.textContent).toContain('Inventory rejected 403'))
    phase = 'held'
    act(() => showInventory(false))
    await settle()
    act(() => showInventory(true))
    await settle()
    expect(host.textContent).not.toContain('Private automation credential')
    expect(host.textContent).not.toContain('tlp_private_prefix')
    await act(async () => resolveFresh({ ...PAGE, tokens: [{ ...PAGE.tokens[0], name: 'Fresh remounted inventory' }] }))
    await vi.waitFor(() => expect(host.textContent).toContain('Fresh remounted inventory'))
  })

  it('does not let an older revocation response restore subsequently denied token metadata', async () => {
    let resolveRevocation!: (value: { data: void; status: number; headers: Headers }) => void
    vi.mocked(apiFetchWithResponse).mockReturnValue(new Promise((resolve) => { resolveRevocation = resolve }))
    await mountInventory()
    act(() => tokenButton(host, 'Revoke').click())
    act(() => tokenButton(document, 'Revoke token').click())
    await vi.waitFor(() => expect(apiFetchWithResponse).toHaveBeenCalledTimes(1))
    await rejectInventory(403)
    await act(async () => resolveRevocation({ data: undefined, status: 204, headers: new Headers() }))
    await settle()
    expect(host.textContent).not.toContain('Private automation credential')
    expect(host.textContent).not.toContain('tlp_private_prefix')
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  })
})
