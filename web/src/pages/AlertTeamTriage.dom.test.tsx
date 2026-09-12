// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '../api/client'
import type { AlertOccurrence } from '../types/alerts'
import type { Team } from '../types/teams'
import { AlertTeamTriage } from './AlertTeamTriage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ data: { id: 'analyst-1', access: { permissions: ['read:teams', 'write:teams', 'write:alerts', 'read:items'] } } }),
}))
const team: Team = {
  id: 'team-1', key: 'soc', name: 'SOC team', description: '', membership_group_id: 'members', manager_group_id: 'managers',
  active: true, revision: 1, can_manage: true, created_at: '', updated_at: '',
}
const original = {
  id: 'occurrence-1', team_id: team.id, version: 7, lifecycle_state: 'new', assignee_user_id: null,
  due_at: '2030-01-01T10:00:00Z', escalation_after_minutes: 30, escalated_at: null,
} as AlertOccurrence
let root: Root | null = null
let client: QueryClient | null = null
let host: HTMLDivElement | null = null
const onUpdated = vi.fn()
const button = (text: string) => Array.from(host!.querySelectorAll('button')).find((entry) => entry.textContent?.trim() === text)!
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) }) }
function render(occurrence = original) {
  act(() => root!.render(<QueryClientProvider client={client!}><AlertTeamTriage occurrence={occurrence} disabled={false} onUpdated={onUpdated} /></QueryClientProvider>))
}
async function mount(manage = true) {
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === 'PATCH') return Promise.resolve({ ...original, version: 8, assignee_user_id: 'analyst-1' }) as never
    if (path.includes('/members?')) return Promise.resolve({ items: [{ id: 'analyst-1', email: 'analyst@example.test', account_role: 'analyst', is_manager: false }], total: 1, page: 1, page_size: 50 }) as never
    return Promise.resolve({ ...team, can_manage: manage }) as never
  })
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  render(); await settle()
}
afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); root = null; client = null; host = null; vi.clearAllMocks() })

describe('team triage asynchronous workflows', () => {
  it('lets members claim using the displayed version without offering manager-only edits', async () => {
    await mount(false)
    expect(button('Change assignee')).toBeUndefined()
    expect(button('Edit deadline')).toBeUndefined()
    act(() => button('Claim occurrence').click()); await settle()
    expect(apiFetch).toHaveBeenCalledWith('/alerts/occurrences/occurrence-1/assignment', expect.objectContaining({
      method: 'PATCH', body: JSON.stringify({ action: 'claim', expected_version: 7 }),
    }))
    expect(onUpdated).toHaveBeenCalledWith([expect.objectContaining({ version: 8, assignee_user_id: 'analyst-1' })])
  })

  it('retains deadline draft versions through refresh and requires deliberate conflict reconciliation', async () => {
    await mount()
    act(() => button('Edit deadline').click())
    render({ ...original, version: 8, due_at: '2030-01-02T10:00:00Z' })
    expect(host!.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toContain('2030-01-01')
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method === 'PATCH') return Promise.reject(new ApiError('Another analyst changed the occurrence', 409, path))
      return Promise.resolve(team) as never
    })
    act(() => button('Save deadline').click()); await settle()
    const first = vi.mocked(apiFetch).mock.calls.find(([path]) => path.endsWith('/deadline'))!
    expect(JSON.parse(first[1]!.body as string)).toMatchObject({ expected_version: 7, escalation_after_minutes: 30 })
    expect(host!.textContent).toContain('your draft is retained')
    expect(host!.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toContain('2030-01-01')
    act(() => button('Discard triage draft and use displayed version 8').click())
    expect(host!.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toContain('2030-01-02')
    vi.mocked(apiFetch).mockResolvedValue({ ...original, version: 9 })
    act(() => button('Save deadline').click()); await settle()
    const requests = vi.mocked(apiFetch).mock.calls.filter(([path]) => path.endsWith('/deadline'))
    expect(JSON.parse(requests.at(-1)![1]!.body as string).expected_version).toBe(8)
    expect(host!.querySelector('input[type="datetime-local"]')).toBeNull()
  })

  it('locks a pending deadline request and leaves its draft available after a transient failure', async () => {
    await mount()
    act(() => button('Edit deadline').click())
    let reject!: (error: Error) => void
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'PATCH'
      ? new Promise((_, fail) => { reject = fail }) : Promise.resolve(team) as never)
    act(() => button('Save deadline').click()); await settle()
    expect(host!.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.disabled).toBe(true)
    expect(button('Cancel deadline').disabled).toBe(true)
    await act(async () => reject(new ApiError('Temporarily unavailable', 503, '/deadline'))); await settle()
    expect(host!.textContent).toContain('Temporarily unavailable')
    expect(host!.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.disabled).toBe(false)
    expect(button('Save deadline').disabled).toBe(false)
  })

  it.each([403, 503])('disables mutations during team access refresh failure (%s) without losing the deadline draft', async (status) => {
    await mount()
    act(() => button('Edit deadline').click())
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Team access unavailable', status, '/teams/team-1'))
    await act(async () => { await client!.invalidateQueries({ queryKey: ['teams', team.id] }) }); await settle()
    expect(button('Save deadline').disabled).toBe(true)
    expect(host!.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toContain('2030-01-01')
    expect(host!.textContent?.includes('SOC team')).toBe(status === 503)
    expect(button('Cancel deadline').disabled).toBe(false)
  })
})
