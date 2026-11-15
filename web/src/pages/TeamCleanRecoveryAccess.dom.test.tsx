// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { Team } from '../types/teams'
import { TeamAIContext } from './TeamAIContext'
import { TeamAIGovernance } from './TeamAIGovernance'
import { TeamsPage } from './TeamsPage'
import { contextFixture, editIntel, intelButton, intelField, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({
  data: { access: { permissions: ['read:teams', 'write:teams', 'read:views', 'write:views', 'read:alerts'] }, features: {} },
  isLoading: false, isError: false,
}) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

async function refresh(key: readonly unknown[]) {
  await act(async () => { await view!.client.invalidateQueries({ queryKey: key, exact: true }) }); await settle()
}
function teamField(label: string) {
  return [...view!.host.querySelectorAll('section[aria-label="Team settings"] label')]
    .find((entry) => entry.textContent?.trim().startsWith(label))?.querySelector<HTMLInputElement | HTMLTextAreaElement>('input, textarea')
}
function noWrites() { expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method)).toBe(false) }

it('loads the new clean team settings baseline after its denied detail read recovers', async () => {
  const initial: Team = { id: 'team-1', key: 'soc', name: 'Old saved team name', description: 'Old saved team description',
    membership_group_id: 'group-1', manager_group_id: 'group-2', active: true, revision: 1, can_manage: true,
    created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z' }
  let status = 200
  let latest = initial
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path.startsWith('/teams?')) return Promise.resolve({ items: [latest], total: 1, page: 1, page_size: 25 })
    if (path === '/teams/team-1') return status === 200 ? Promise.resolve(latest) : Promise.reject(new ApiError('Team detail denied', status, path))
    if (path.includes('/members')) return Promise.resolve({ items: [], total: 0, page: 1, page_size: 50 })
    return Promise.resolve([])
  })
  view = await mountIntel(<TeamsPage />, '/?team=team-1')
  expect(teamField('Name')?.value).toBe(initial.name)
  expect(teamField('Description')?.value).toBe(initial.description)
  status = 403; await refresh(['teams', 'detail', 'team-1', false])
  expect(teamField('Name')).toBeUndefined()
  latest = { ...initial, revision: 2, name: 'Fresh canonical team name', description: 'Fresh canonical team description' }
  status = 200; await refresh(['teams', 'detail', 'team-1', false])
  expect(teamField('Name')?.value).toBe(latest.name)
  expect(teamField('Description')?.value).toBe(latest.description)
  noWrites()
})

it('loads the new clean AI context baseline after its denied own read recovers', async () => {
  let status = 200
  let latest = contextFixture
  vi.mocked(apiFetch).mockImplementation((path) => status === 200 ? Promise.resolve(latest)
    : Promise.reject(new ApiError('Context denied', status, path)))
  view = await mountIntel(<TeamAIContext teamId="team-1" writable />)
  expect(intelField(view.host, 'Relevance criteria').value).toBe(contextFixture.relevance_criteria)
  status = 403; await refresh(['teams', 'ai-context', 'team-1'])
  expect(view.host.querySelector('textarea')).toBeNull()
  latest = { ...contextFixture, version: 2, relevance_criteria: 'Fresh canonical relevance criteria', technology_stack: ['Fresh canonical telemetry stack'] }
  status = 200; await refresh(['teams', 'ai-context', 'team-1'])
  expect(intelField(view.host, 'Relevance criteria').value).toBe(latest.relevance_criteria)
  expect(intelField(view.host, 'Technology stack').value).toBe(latest.technology_stack[0])
  noWrites()
})

it('loads the new clean destination policy baseline after its denied own read recovers', async () => {
  const oldKey = 'profile:00000000-0000-4000-8000-000000000001'
  const newKey = 'profile:00000000-0000-4000-8000-000000000002'
  const initial = { team_id: 'team-1', version: 1, configured: true,
    approved_provider_keys: [oldKey], selected_provider_key: oldKey,
    label_destinations: {}, destinations: [{ key: oldKey, name: 'Old private approved provider', available: true }],
    can_manage: true, can_approve: true }
  let status = 200
  let latest = initial
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path === '/iam/data-policies') return Promise.resolve({ labels: [] })
    if (path.startsWith('/ai/providers?')) return Promise.resolve({ items: [], total: 0 })
    return status === 200 ? Promise.resolve(latest) : Promise.reject(new ApiError('Destination policy denied', status, path))
  })
  view = await mountIntel(<TeamAIGovernance teamId="team-1" admin />)
  expect(intelField(view.host, 'Approved provider keys').value).toBe(oldKey)
  status = 403; await refresh(['teams', 'ai-governance', 'team-1', true])
  expect(view.host.querySelector('textarea')).toBeNull()
  latest = { ...initial, version: 2, approved_provider_keys: [newKey], selected_provider_key: newKey,
    destinations: [{ key: newKey, name: 'Fresh canonical approved provider', available: true }] }
  status = 200; await refresh(['teams', 'ai-governance', 'team-1', true])
  expect(intelField(view.host, 'Approved provider keys').value).toBe(newKey)
  const destination = [...view.host.querySelectorAll('label')]
    .find((label) => label.textContent?.trim().startsWith('Assessment destination'))?.control as HTMLSelectElement | undefined
  expect(destination?.value).toBe(newKey)
  noWrites()
})

it('keeps an admitted team save baseline after dirty recovery while the canonical refresh is pending', async () => {
  const team: Team = { id: 'team-1', key: 'soc', name: 'Saved team', description: 'Saved before withdrawal',
    membership_group_id: 'group-1', manager_group_id: 'group-2', active: true, revision: 1, can_manage: true,
    created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z' }
  let mode: 'good' | 'denied' | 'held' = 'good'
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (path === '/teams/team-1' && init?.method === 'PATCH') {
      mode = 'held'
      return Promise.resolve({ ...team, revision: 2, description: 'Actual dirty team draft' })
    }
    if (path.startsWith('/teams?')) return Promise.resolve({ items: [team], total: 1, page: 1, page_size: 25 })
    if (path === '/teams/team-1') {
      if (mode === 'denied') return Promise.reject(new ApiError('Team detail denied', 403, path))
      if (mode === 'held') return new Promise(() => {})
      return Promise.resolve(team)
    }
    if (path.includes('/members')) return Promise.resolve({ items: [], total: 0, page: 1, page_size: 50 })
    return Promise.resolve([])
  })
  view = await mountIntel(<TeamsPage />, '/?team=team-1')
  editIntel(view.host.querySelector('section[aria-label="Team settings"]')!, 'Description', 'Actual dirty team draft')
  mode = 'denied'; await refresh(['teams', 'detail', 'team-1', false])
  expect(teamField('Description')).toBeUndefined()
  mode = 'good'; await refresh(['teams', 'detail', 'team-1', false])
  expect(teamField('Description')?.value).toBe('Actual dirty team draft')
  act(() => intelButton(view!.host, 'Save team details').click())
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Team saved.'))
  await settle()
  expect(teamField('Description')?.value).toBe('Actual dirty team draft')
  const writes = vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')
  expect(writes).toHaveLength(1)
  expect(JSON.parse(String(writes[0][1]?.body))).toMatchObject({ expected_revision: 1, description: 'Actual dirty team draft' })
  expect(view.client.getQueryState(['teams', 'detail', 'team-1', false])?.fetchStatus).toBe('fetching')
})
