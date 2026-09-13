// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import type { AlertInterest } from '../types/alerts'
import { useAlertsPageController } from './useAlertsPageController'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
const rule: AlertInterest = {
  id: 'rule-1', user_id: null, team_id: 'team-1', name: 'Team exposure', category: 'software', keywords: ['vpn'],
  enabled: true, revision: 7, row_version: 11, due_after_minutes: 60, escalation_after_minutes: 30,
  created_at: '', updated_at: '',
}
let current: ReturnType<typeof useAlertsPageController>
let root: Root | null = null
let router: ReturnType<typeof createMemoryRouter> | null = null
let client: QueryClient | null = null
let source = rule
function Harness() { current = useAlertsPageController(); return current.confirmDiscardUnsavedAlertChanges.discardDialog }
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) }) }
async function mount() {
  source = rule
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (path.startsWith('/alerts?')) return Promise.resolve([source]) as never
    if (path === '/alerts/preview') return Promise.resolve({ items: [], total: 0 }) as never
    if (init?.method) return Promise.resolve({ ...source, row_version: 12 }) as never
    return Promise.resolve([]) as never
  })
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  router = createMemoryRouter([{ path: '/alerts', element: <Harness /> }], { initialEntries: ['/alerts?team_id=team-1'] })
  root = createRoot(document.createElement('div'))
  act(() => root!.render(<QueryClientProvider client={client!}><RouterProvider router={router!} /></QueryClientProvider>))
  await settle()
}
afterEach(() => { act(() => root?.unmount()); router?.dispose(); client?.clear(); vi.clearAllMocks(); root = null })

describe('team watchlist request lifecycle', () => {
  it('creates a team-owned watchlist with bounded deadline defaults and a scoped list query', async () => {
    await mount()
    expect(apiFetch).toHaveBeenCalledWith('/alerts?include_disabled=false&team_id=team-1')
    act(() => { current.setName('New team watchlist'); current.setKeywordsText('vpn'); current.setDueAfterMinutes('90'); current.setEscalationAfterMinutes('0') })
    act(() => current.onSave({ preventDefault: vi.fn() } as never)); await settle()
    const create = vi.mocked(apiFetch).mock.calls.find(([path, init]) => path === '/alerts' && init?.method === 'POST')!
    expect(JSON.parse(create[1]!.body as string)).toMatchObject({ team_id: 'team-1', due_after_minutes: 90, escalation_after_minutes: 0 })
  })
  it('keeps the loaded rule version with a dirty deadline draft through a background refresh', async () => {
    await mount()
    act(() => current.onEdit(rule))
    act(() => current.setDueAfterMinutes('90'))
    source = { ...rule, row_version: 12, due_after_minutes: 120 }
    await act(async () => { await client!.invalidateQueries({ queryKey: ['alerts'] }) }); await settle()
    expect(current.dueAfterMinutes).toBe('90')
    expect(current.editingAlertRowVersion).toBe(11)
    act(() => current.onSave({ preventDefault: vi.fn() } as never)); await settle()
    const patch = vi.mocked(apiFetch).mock.calls.find(([path, init]) => path === '/alerts/rule-1' && init?.method === 'PATCH')!
    const payload = JSON.parse(patch[1]!.body as string)
    expect(payload).toMatchObject({ expected_row_version: 11, due_after_minutes: 90, escalation_after_minutes: 30 })
    expect(payload).not.toHaveProperty('team_id')
  })
})
