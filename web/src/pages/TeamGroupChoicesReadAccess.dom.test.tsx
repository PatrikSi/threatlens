// @vitest-environment jsdom
import { act } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRoot, type Root } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { Team } from '../types/teams'
import { TeamsPage } from './TeamsPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<typeof import('../api/client')>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { access: { permissions: ['read:teams', 'write:teams', 'read:iam', 'write:iam'] } }, isError: false }) }))
let root: Root | undefined
let client: QueryClient
let router: ReturnType<typeof createMemoryRouter>
let host: HTMLDivElement
const team: Team = { id: 'team-1', key: 'soc', name: 'Readable SOC team', description: 'Readable team details', membership_group_id: 'group-1', manager_group_id: 'group-2', active: true, revision: 1, can_manage: true, created_at: '2026-09-12T00:00:00Z', updated_at: '2026-09-12T00:00:00Z' }
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
afterEach(() => { act(() => root?.unmount()); root = undefined; router?.dispose(); client?.clear(); host?.remove(); vi.resetAllMocks() })

it('keeps denied IAM group choice labels hidden while independently readable team details remain available', async () => {
  let status = 200
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path === '/iam/groups') return status === 200
      ? Promise.resolve([{ id: 'group-1', name: 'Private member group label', is_system: false }, { id: 'group-2', name: 'Private manager group label', is_system: false }])
      : Promise.reject(new ApiError('Group choices unavailable', status, path))
    if (path.startsWith('/teams/admin?')) return Promise.resolve({ items: [team], total: 1, page: 1, page_size: 25 })
    if (path === '/teams/admin/team-1') return Promise.resolve(team)
    throw new Error(`Unexpected request: ${path}`)
  })
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  router = createMemoryRouter([{ path: '/teams', element: <TeamsPage /> }], { initialEntries: ['/teams?mode=admin&team=team-1'] })
  act(() => root!.render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>))
  await settle()
  expect(host.textContent).toContain('Private manager group label')
  for (const next of [403, 503]) {
    status = next
    await act(async () => { await client.invalidateQueries({ queryKey: ['teams', 'group-choices'] }) })
    await settle()
    expect(host.textContent).not.toContain('Private manager group label')
    expect(host.textContent).not.toContain('Private member group label')
    expect(host.textContent).toContain(team.description)
  }
})
